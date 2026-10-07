import socket
import threading
import time

import pytest

from core.protocol_engine import (ProtocolConfigError, append_checksum,
    build_modbus_rtu_read, build_modbus_tcp_read, find_modbus_rtu_response,
    validate_modbus_tcp_response)


def test_tcp_declared_byte_count_is_checked():
    packet = bytes.fromhex('00 01 00 00 00 05 01 03 04 00 2A')
    assert not validate_modbus_tcp_response(packet, 1, 1, 3)[0]


@pytest.mark.parametrize('payload', ['010303000000', '010300', '01030200'])
def test_rtu_malformed_register_length_is_rejected(payload):
    packet = append_checksum(bytes.fromhex(payload), 'crc16_modbus')
    assert not find_modbus_rtu_response(packet, 1, 3)[0]


@pytest.mark.parametrize('function', [5, 6, 15, 16, 23])
def test_automatic_write_requests_are_refused(function):
    with pytest.raises(ProtocolConfigError):
        build_modbus_rtu_read(1, function, 0, 1)
    with pytest.raises(ProtocolConfigError):
        build_modbus_tcp_read(1, 1, function, 0, 1)


def test_crc_corruption_and_truncation_are_rejected():
    packet = bytes.fromhex('01 03 02 00 2A 39 9B')
    assert not find_modbus_rtu_response(packet[:-1], 1, 3)[0]
    assert not find_modbus_rtu_response(packet[:-1] + b'\x00', 1, 3)[0]


@pytest.mark.parametrize('packet', [
    '00 02 00 00 00 05 01 03 02 00 2A',
    '00 01 00 01 00 05 01 03 02 00 2A',
    '00 01 00 00 00 05 02 03 02 00 2A',
    '00 01 00 00 00 05 01 04 02 00 2A',
    '00 01 00 00 00 05 01 03 02 00',
    '00 01 00 00 00 04 01 83 02 00',
])
def test_invalid_tcp_frames(packet):
    assert not validate_modbus_tcp_response(bytes.fromhex(packet), 1, 1, 3)[0]


class FragmentSocket:
    def __init__(self, fragments):
        self.fragments = iter(fragments)
    def settimeout(self, value):
        pass
    def recv(self, size):
        value = next(self.fragments, b'')
        if isinstance(value, Exception):
            raise value
        return value[:size]


def test_split_tcp_and_eof():
    from core.transport import receive_response
    sock = FragmentSocket([b'PO', b'NG', b' DEVICE-01', b''])
    assert receive_response(sock, 1) == b'PONG DEVICE-01'


def test_timeout_preserves_partial_response():
    from core.transport import receive_response
    assert receive_response(FragmentSocket([b'partial', socket.timeout()]), .1) == b'partial'


@pytest.mark.parametrize('epoch', [1_000_000.0, 1_000_000_000.0])
def test_tcp_receive_timeout_stays_within_budget_on_coarse_clock(monkeypatch, epoch):
    from core import transport
    ticks = iter([epoch, epoch, epoch + .025, epoch + .15])
    monkeypatch.setattr(transport.time, 'monotonic', lambda: next(ticks))
    class BudgetSocket(FragmentSocket):
        def __init__(self):
            super().__init__([b'PO', b'NG', AssertionError('deadline must stop reception')])
            self.timeouts = []
        def settimeout(self, value):
            self.timeouts.append(value)
    sock = BudgetSocket()
    assert transport.receive_response(sock, .1) == b'PONG'
    assert len(sock.timeouts) == 2
    assert all(0 < value <= .1 for value in sock.timeouts)
    assert sock.timeouts[1] < sock.timeouts[0]


def test_complete_mbap_frame_does_not_wait_for_eof():
    from core.transport import receive_response, modbus_tcp_complete
    raw = bytes.fromhex('00 01 00 00 00 05 01 03 02 00 2A')
    assert receive_response(FragmentSocket([raw[:4], raw[4:8], raw[8:], AssertionError('extra recv')]), 1, complete=modbus_tcp_complete) == raw


def test_connection_reset_preserves_partial_bytes():
    from core.transport import receive_response
    assert receive_response(FragmentSocket([b'partial', ConnectionResetError()]), 1) == b'partial'


def test_empty_connection_reset_remains_error():
    from core.transport import receive_response
    with pytest.raises(ConnectionResetError):
        receive_response(FragmentSocket([ConnectionResetError()]), 1)


def test_read_response_must_match_requested_count():
    tcp = bytes.fromhex('00 01 00 00 00 05 01 03 02 00 2A')
    rtu = append_checksum(bytes.fromhex('01 03 02 00 2A'), 'crc16_modbus')
    assert not validate_modbus_tcp_response(tcp, 1, 1, 3, expected_count=2)[0]
    assert not find_modbus_rtu_response(rtu, 1, 3, expected_count=2)[0]


@pytest.mark.parametrize('modbus', [False, True])
def test_configured_tcp_receives_fragmented_loopback_response(modbus):
    from core.generic_detector import GenericDetector
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    server.settimeout(2)
    port = server.getsockname()[1]
    raw = bytes.fromhex('00 01 00 00 00 05 01 03 02 00 2A') if modbus else b'PONG DEVICE-01'
    def serve():
        try:
            conn, _ = server.accept()
            with conn:
                conn.recv(128)
                for chunk in (raw[:2], raw[2:6], raw[6:]):
                    conn.sendall(chunk)
                    time.sleep(.015)
        finally:
            server.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    from core.config_manager import StandardDeviceConfig
    from inspection.adapters import demo_config
    detector = GenericDetector(config=StandardDeviceConfig(data=demo_config()))
    protocol = {'type': 'modbus_tcp', 'test_registers': [{'address': 0, 'count': 1}]} if modbus else {'type': 'fixture_tcp', 'probes': [{'request_text': 'PING', 'response_match': {'exact_text': 'PONG DEVICE-01'}}]}
    parent = {'network_config': {'transport': 'tcp', 'device_role': 'server'}}
    profile = detector._network_profile({'protocol': protocol}, parent)
    try:
        result = detector._test_modbus_tcp_configured('127.0.0.1', port, '', protocol, profile) if modbus else detector._test_tcp_protocol_configured('127.0.0.1', port, '', {'timeout': 1}, protocol, profile)
        assert result['ok']
        assert raw.hex(' ').upper() in result['response']
    finally:
        thread.join(2)


def incoming_detector():
    from core.config_manager import StandardDeviceConfig
    from core.generic_detector import GenericDetector
    from inspection.adapters import demo_config
    detector = GenericDetector(config=StandardDeviceConfig(data=demo_config()))
    detector._local_ipv4_for_iface = lambda iface: '127.0.0.1'
    return detector


def test_incoming_tcp_split_loopback_frame_and_matching_reply():
    detector = incoming_detector()
    port_socket = socket.socket()
    port_socket.bind(('127.0.0.1', 0))
    port = port_socket.getsockname()[1]
    port_socket.close()
    protocol = {'type': 'fixture_tcp', 'response_match': {'exact_text': 'PONG'}, 'reply_text': 'ACK'}
    profile = detector._network_profile({'protocol': protocol}, {'network_config': {'transport': 'tcp', 'device_role': 'client'}})
    replies = []
    errors = []
    def client():
        try:
            deadline = time.monotonic() + 2
            while True:
                try:
                    conn = socket.create_connection(('127.0.0.1', port), timeout=.2)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.01)
            with conn:
                conn.sendall(b'PO')
                time.sleep(.035)
                conn.sendall(b'NG')
                replies.append(conn.recv(128))
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=client, daemon=True)
    thread.start()
    try:
        result = detector._test_tcp_client_configured('', '127.0.0.1', port, .4, protocol, profile)
    finally:
        thread.join(2)
    assert result['ok'], result
    assert '50 4F 4E 47' in result['response']
    assert replies == [b'ACK'] and not errors


@pytest.mark.parametrize('fragments,rule,expected_ip,ok,reply,reply_error', [
    ([b'PO', b'NG'], {'exact_text': 'PONG'}, '127.0.0.1', True, [b'ACK'], False),
    ([b'PO', socket.timeout()], {'exact_text': 'PONG'}, '127.0.0.1', False, [], False),
    ([b'PO', ConnectionResetError()], {'exact_text': 'PONG'}, '127.0.0.1', False, [], False),
    ([b'PO', b'NG', b''], {}, '127.0.0.1', True, [b'ACK'], False),
    ([b'PO', socket.timeout()], {}, '127.0.0.1', True, [b'ACK'], False),
    ([b'PONG'], {'exact_text': 'PONG'}, '127.0.0.2', False, [], False),
    ([b'PONG'], {'exact_text': 'PONG'}, '127.0.0.1', False, [b'ACK'], True),
])
def test_incoming_tcp_partial_evidence_deadline_source_and_reply(monkeypatch, fragments, rule, expected_ip, ok, reply, reply_error):
    detector = incoming_detector()
    class Connection(FragmentSocket):
        def __init__(self):
            super().__init__(fragments)
            self.sent = []
            self.timeouts = []
        def settimeout(self, value):
            self.timeouts.append(value)
        def getsockname(self):
            return ('127.0.0.1', 123)
        def sendall(self, data):
            self.sent.append(data)
            if reply_error:
                raise ConnectionResetError('reply reset')
        def close(self):
            pass
    conn = Connection()
    class Listener:
        def settimeout(self, value):
            pass
        def setsockopt(self, *args):
            pass
        def bind(self, address):
            assert address == ('127.0.0.1', 123)
        def listen(self, count):
            pass
        def accept(self):
            return conn, ('127.0.0.1', 42)
        def close(self):
            pass
    monkeypatch.setattr(socket, 'socket', lambda *args: Listener())
    protocol = {'type': 'fixture_tcp', 'request_text': 'read', 'response_match': rule, 'reply_text': 'ACK'}
    profile = detector._network_profile({'protocol': protocol}, {'network_config': {'device_role': 'client'}})
    result = detector._test_tcp_client_configured('', expected_ip, 123, .1, protocol, profile)
    assert result['ok'] == ok
    assert '50 4F' in result['response']
    if len(fragments) > 1 and fragments[1] == b'NG':
        assert '50 4F 4E 47' in result['response']
    assert conn.sent == reply
    assert all(0 < t <= .1 for t in conn.timeouts)
