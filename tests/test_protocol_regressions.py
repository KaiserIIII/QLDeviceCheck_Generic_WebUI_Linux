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
    detector = GenericDetector()
    protocol = {'type': 'modbus_tcp', 'test_registers': [{'address': 0, 'count': 1}]} if modbus else {'type': 'fixture_tcp', 'probes': [{'request_text': 'PING', 'response_match': {'exact_text': 'PONG DEVICE-01'}}]}
    parent = {'network_config': {'transport': 'tcp', 'device_role': 'server'}}
    profile = detector._network_profile({'protocol': protocol}, parent)
    try:
        result = detector._test_modbus_tcp_configured('127.0.0.1', port, '', protocol, profile) if modbus else detector._test_tcp_protocol_configured('127.0.0.1', port, '', {'timeout': 1}, protocol, profile)
        assert result['ok']
        assert raw.hex(' ').upper() in result['response']
    finally:
        thread.join(2)
