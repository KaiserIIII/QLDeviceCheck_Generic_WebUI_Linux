"""Exercise real discovery methods using byte sockets and authorized loopback only."""
import socket
import threading

import pytest

from core import generic_detector as gd
from core.protocol_engine import validate_modbus_tcp_response


READ_PDU = bytes.fromhex('03 02 00 01')
ID_PDU = bytes.fromhex('2b 0e 01 01 00 00 03 00 01 56 01 01 50 02 01 31')


class Config:
    def __init__(self, unit=1, registers=None):
        self.unit = unit
        self.registers = registers or [{'address': 0, 'count': 1}]

    def protocol_probes(self, protocol):
        assert protocol == 'modbus_tcp'
        return [{'unit_id': self.unit, 'test_registers': self.registers}]


def detector(unit=1, registers=None):
    d = object.__new__(gd.GenericDetector)
    d.config = Config(unit, registers)
    return d


def packet(request, pdu):
    return request[:2] + b'\0\0' + (len(pdu) + 1).to_bytes(2, 'big') + request[6:7] + pdu


def invoke(d, path, port=1502):
    return (d._modbus_tcp_identify if path == 'identify' else d._modbus_tcp_standard_probe)('127.0.0.1', port)


class ByteSocket:
    def __init__(self, make_parts, opened):
        self.make_parts = make_parts
        self.parts = []
        self.closed = False
        self.sent = []
        self.timeouts = []
        opened.append(self)

    def settimeout(self, timeout):
        assert 0 < timeout <= .45
        self.timeouts.append(timeout)

    def connect(self, endpoint):
        assert endpoint[0] == '127.0.0.1'

    def connect_ex(self, endpoint):
        self.connect(endpoint)
        return 0

    def sendall(self, request):
        self.sent.append(request)
        self.parts = list(self.make_parts(request))

    def recv(self, size):
        if not self.parts:
            return b''
        item = self.parts.pop(0)
        if isinstance(item, BaseException):
            raise item
        if len(item) > size:
            self.parts.insert(0, item[size:])
        return item[:size]

    def close(self):
        self.closed = True


def install(monkeypatch, make_parts):
    opened = []
    monkeypatch.setattr(gd.socket, 'socket', lambda *a, **kw: ByteSocket(make_parts, opened))
    return opened


def malformed(request, path, kind):
    raw = packet(request, ID_PDU if path == 'identify' else READ_PDU)
    if kind == 'text':
        return b'HTTP/1.1 200 OK\r\n'
    if kind == 'transaction':
        return b'\x7f\xff' + raw[2:]
    if kind == 'unit':
        return raw[:6] + b'\x02' + raw[7:]
    if kind == 'protocol':
        return raw[:2] + b'\x00\x01' + raw[4:]
    if kind == 'function':
        return raw[:7] + b'\x04' + raw[8:]
    if kind == 'short':
        return raw[:5]
    if kind == 'missing_body':
        return raw[:-1]
    if kind == 'too_short_length':
        return raw[:4] + b'\0\x02' + raw[6:]
    if kind == 'oversized_length':
        return raw[:4] + b'\x01\x00' + raw[6:]
    raise AssertionError(kind)


@pytest.mark.parametrize('path', ['read', 'identify'])
@pytest.mark.parametrize('kind', ['text', 'transaction', 'unit', 'protocol', 'function', 'short', 'missing_body', 'too_short_length', 'oversized_length'])
def test_discovery_does_not_classify_nonempty_invalid_protocol(monkeypatch, path, kind):
    opened = install(monkeypatch, lambda request: [malformed(request, path, kind)])
    assert invoke(detector(), path) is None
    assert opened and all(s.closed for s in opened)


@pytest.mark.parametrize('path', ['read', 'identify'])
def test_every_single_fragment_split_preserves_complete_protocol_evidence(monkeypatch, path):
    pdu = ID_PDU if path == 'identify' else READ_PDU
    for split in range(1, 7 + len(pdu)):
        expected = []
        def parts(request):
            raw = packet(request, pdu)
            expected.append(raw)
            return [raw[:split], raw[split:]]
        opened = install(monkeypatch, parts)
        result = invoke(detector(), path)
        assert result
        assert result['response'] == expected[0].hex(' ').upper()
        assert all(s.closed for s in opened)


@pytest.mark.parametrize('path', ['read', 'identify'])
@pytest.mark.parametrize('ending', ['eof', 'timeout', 'reset'])
def test_partial_frame_followed_by_interruption_is_not_identified(monkeypatch, path, ending):
    tail = {'eof': b'', 'timeout': socket.timeout(), 'reset': ConnectionResetError()}[ending]
    opened = install(monkeypatch, lambda request: [packet(request, ID_PDU if path == 'identify' else READ_PDU)[:8], tail])
    assert invoke(detector(), path) is None
    assert all(s.closed for s in opened)


@pytest.mark.parametrize('path', ['read', 'identify'])
@pytest.mark.parametrize('ending', [b'', socket.timeout(), ConnectionResetError(), OSError('fixture down')])
def test_empty_or_failed_discovery_closes_transport(monkeypatch, path, ending):
    opened = install(monkeypatch, lambda request: [ending])
    assert invoke(detector(), path) is None
    assert all(s.closed for s in opened)


@pytest.mark.parametrize('path', ['read', 'identify'])
def test_valid_exception_only_claims_protocol_reachability(monkeypatch, path):
    pdu = bytes.fromhex('ab 01' if path == 'identify' else '83 02')
    opened = install(monkeypatch, lambda request: [packet(request, pdu)])
    result = invoke(detector(), path)
    assert result and '异常' in result['summary']
    assert result['type'] == 'TCP连通 + Modbus TCP异常响应'
    assert '仅确认协议可达' in result['summary']
    assert '按配置' not in result['summary']
    assert '读取成功' not in result['summary'] and '识别成功' not in result['summary']
    assert all(s.closed for s in opened)


@pytest.mark.parametrize('unit', [0, 1, 255])
def test_standard_read_preserves_explicit_unit_and_request(monkeypatch, unit):
    opened = install(monkeypatch, lambda request: [packet(request, READ_PDU)])
    result = invoke(detector(unit), 'read')
    assert result
    assert opened[0].sent == [bytes([0, 2, 0, 0, 0, 6, unit, 3, 0, 0, 0, 1])]
    assert all(s.closed for s in opened)


@pytest.mark.parametrize('pdu', [bytes.fromhex('03 04 00 01 00 02'), bytes.fromhex('03 01 00'), bytes.fromhex('83 02 00')])
def test_standard_read_rejects_wrong_count_odd_data_and_malformed_exception(monkeypatch, pdu):
    install(monkeypatch, lambda request: [packet(request, pdu)])
    assert invoke(detector(), 'read') is None


@pytest.mark.parametrize('pdu', [
    bytes.fromhex('2b 0f 01 01 00 00 00'),
    bytes.fromhex('2b 0e 02 01 00 00 00'),
    bytes.fromhex('2b 0e 01 00 00 00 00'),
    bytes.fromhex('2b 0e 01 01 01 00 00'),
    bytes.fromhex('2b 0e 01 01 00 01 00'),
    bytes.fromhex('2b 0e 01 01 00 00 01'),
    bytes.fromhex('2b 0e 01 01 00 00 01 00 03 56'),
    bytes.fromhex('2b 0e 01 01 00 00 00 00'),
    bytes.fromhex('2b 0e 01 01 00 00 02 00 01 56 00 01 50'),
    bytes.fromhex('2b 0e 01 01 00 00 02 01 01 50 00 01 56'),
    bytes.fromhex('ab 01 00'),
])
def test_basic_identification_refuses_malformed_fields_and_object_lengths(monkeypatch, pdu):
    install(monkeypatch, lambda request: [packet(request, pdu)])
    assert invoke(detector(), 'identify') is None


@pytest.mark.parametrize('conformity', [1, 2, 3, 0x81, 0x82, 0x83])
def test_basic_identification_accepts_actual_conformity_levels(monkeypatch, conformity):
    pdu = ID_PDU[:3] + bytes([conformity]) + ID_PDU[4:]
    opened = install(monkeypatch, lambda request: [packet(request, pdu)])
    assert invoke(detector(), 'identify')
    assert opened[0].sent == [bytes.fromhex('00 01 00 00 00 05 01 2b 0e 01 00')]


@pytest.mark.parametrize('pdu', [
    bytes.fromhex('2b 0e 01 01 ff 01 01 00 01 56'),
    bytes.fromhex('2b 0e 01 01 ff 02 02 00 01 56 01 01 50'),
])
def test_identification_accepts_a_bounded_first_page_without_followup_requests(monkeypatch, pdu):
    opened = install(monkeypatch, lambda request: [packet(request, pdu)])
    result = invoke(detector(), 'identify')
    assert result and len(opened) == 1 and len(opened[0].sent) == 1
    assert '分页' in result['summary']


@pytest.mark.parametrize('pdu', [
    bytes.fromhex('2b 0e 01 01 00 00 00'),
    bytes.fromhex('2b 0e 01 01 ff 00 00'),
    bytes.fromhex('2b 0e 01 01 ff 01 00'),
    bytes.fromhex('2b 0e 01 01 00 00 01 02 01 31'),
    bytes.fromhex('2b 0e 01 01 00 00 01 03 01 31'),
    bytes.fromhex('2b 0e 01 01 00 00 01 7f 01 31'),
    bytes.fromhex('2b 0e 01 01 00 00 01 80 01 31'),
    bytes.fromhex('2b 0e 01 01 ff ff 01 00 01 56'),
    bytes.fromhex('2b 0e 01 01 ff 02 01 00 01 56'),
    bytes.fromhex('2b 0e 01 01 00 00 01 00 01 56'),
    bytes.fromhex('2b 0e 01 01 00 00 02 00 01 56 02 01 31'),
    bytes.fromhex('2b 0e 01 01 ff 03 03 00 01 56 01 01 50 02 01 31'),
])
def test_fixed_basic_query_refuses_empty_wrong_scope_or_incomplete_pages(monkeypatch, pdu):
    install(monkeypatch, lambda request: [packet(request, pdu)])
    assert invoke(detector(), 'identify') is None


@pytest.mark.parametrize('path', ['read', 'identify'])
@pytest.mark.parametrize('code', [0, 7, 9, 255])
def test_discovery_refuses_reserved_exception_codes(monkeypatch, path, code):
    pdu = bytes([0xAB if path == 'identify' else 0x83, code])
    install(monkeypatch, lambda request: [packet(request, pdu)])
    assert invoke(detector(), path) is None


@pytest.mark.parametrize('path', ['read', 'identify'])
@pytest.mark.parametrize('code', [1, 2, 3, 4, 5, 6, 8, 10, 11])
def test_defined_exception_codes_retain_explicit_protocol_only_label(monkeypatch, path, code):
    pdu = bytes([0xAB if path == 'identify' else 0x83, code])
    install(monkeypatch, lambda request: [packet(request, pdu)])
    result = invoke(detector(), path)
    assert result and result['type'] == 'TCP连通 + Modbus TCP异常响应'
    assert '仅确认协议可达' in result['summary']


@pytest.mark.parametrize('code', [0, 7, 9, 255])
@pytest.mark.parametrize('accept', [False, True])
def test_configured_TCP_validator_never_accepts_reserved_exception(code, accept):
    raw = bytes([0, 1, 0, 0, 0, 3, 1, 0x83, code])
    assert not validate_modbus_tcp_response(raw, 1, 1, 3, accept_exception=accept)[0]


def test_bad_first_read_proceeds_to_next_configured_register(monkeypatch):
    def parts(request):
        raw = packet(request, READ_PDU)
        return [b'not a Modbus frame' if request[1] == 2 else raw]
    opened = install(monkeypatch, parts)
    result = invoke(detector(registers=[{'address': 0, 'count': 1}, {'address': 1, 'count': 1}]), 'read')
    assert result and result['request'].endswith('00 03 00 00 00 06 01 03 00 01 00 01')
    assert len(opened) == 2 and all(s.closed for s in opened)


def test_invalid_responses_preserve_TCP_connect_result_without_Modbus_label(monkeypatch):
    opened = install(monkeypatch, lambda request: [b'HTTP/1.1 200 OK\r\n'])
    result = detector()._tcp_probe('127.0.0.1', 502)
    assert result['type'] == 'TCP端口连通'
    assert result['status'] == '连通' and result['response'] == 'connected'
    assert len(opened) == 3 and all(s.closed for s in opened)


@pytest.mark.parametrize('path', ['read', 'identify'])
def test_loopback_actual_TCP_fragmentation_preserves_frame(path):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]
    expected, errors = [], []
    def serve():
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(2)
                request = bytearray()
                size = 11 if path == 'identify' else 12
                while len(request) < size:
                    fragment = conn.recv(size - len(request))
                    if not fragment:
                        raise AssertionError('client disconnected before complete request')
                    request.extend(fragment)
                raw = packet(bytes(request), ID_PDU if path == 'identify' else READ_PDU)
                expected.append(raw)
                # Real TCP may coalesce writes. All split positions are also
                # deterministically covered by the ByteSocket test above.
                for byte in raw:
                    conn.sendall(bytes([byte]))
        except BaseException as exc:
            errors.append(repr(exc))
        finally:
            listener.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        result = invoke(detector(), path, port)
    finally:
        thread.join(3)
        listener.close()
    assert not thread.is_alive() and not errors
    assert result and result['response'] == expected[0].hex(' ').upper()
