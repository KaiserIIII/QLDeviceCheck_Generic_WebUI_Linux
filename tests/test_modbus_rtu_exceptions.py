"""Read-probe exception regressions; use real parsers and Fake serial, no hardware."""
import pytest

from core.protocol_engine import append_checksum, find_modbus_rtu_response


def exception_frame(code, slave=1, function=3):
    # CRC is input construction, not the expected classification under test.
    return append_checksum(bytes([slave, function | 0x80, code]), 'crc16_modbus')


@pytest.mark.parametrize('code', [0x00, 0x07, 0x09, 0xFF])
@pytest.mark.parametrize('accept', [False, True])
def test_unsupported_exception_cannot_pass_even_when_exceptions_allowed(code, accept):
    frame = exception_frame(code)
    ok, note, captured = find_modbus_rtu_response(frame, 1, 3, accept_exception=accept, expected_count=1)
    assert not ok
    assert captured == frame
    assert '未定义' in note


@pytest.mark.parametrize('code', [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x08, 0x0A, 0x0B])
@pytest.mark.parametrize('accept', [False, True])
def test_defined_exception_preserves_explicit_acceptance_policy(code, accept):
    frame = exception_frame(code)
    ok, note, captured = find_modbus_rtu_response(frame, 1, 3, accept_exception=accept, expected_count=1)
    assert ok is accept
    assert captured == frame
    assert f'0x{code:02X}' in note
    assert ('按配置允许' in note) is accept


@pytest.mark.parametrize('bad', [
    bytes.fromhex('01 83 02 C0'),  # truncated standard exception
    bytes.fromhex('01 83 02 C0 F0'),  # known frame's CRC bit changed
    bytes.fromhex('02 83 02 30 F1'),  # different target with valid CRC
    bytes.fromhex('01 84 02 C2 C1'),  # different function with valid CRC
])
def test_exception_flag_cannot_bypass_framing_identity_or_crc(bad):
    assert not find_modbus_rtu_response(bad, 1, 3, accept_exception=True, expected_count=1)[0]


class SerialReply:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.closed = False
        self.reset_count = 0
    def reset_input_buffer(self): self.reset_count += 1
    def write(self, packet): self.requests.append(packet)
    def flush(self): pass
    def read(self, size):
        assert size == 256
        return next(self.replies)
    def close(self): self.closed = True


def configured(monkeypatch, replies, *, accept=True, mode='any'):
    from core import generic_detector
    port = SerialReply(replies)
    def open_fake(**kwargs):
        assert kwargs == {'port':'FAKE-RTU-ONLY', 'baudrate':9600, 'bytesize':8,
                          'parity':'N', 'stopbits':1., 'timeout':.02}
        return port
    monkeypatch.setattr(generic_detector.serial, 'Serial', open_fake)
    detector = object.__new__(generic_detector.GenericDetector)
    detector.connection_tests = []
    protocol = {'type':'modbus_rtu', 'slave_id':1, 'accept_exception':accept, 'match_mode':mode,
                'test_registers':[{'address':i, 'count':1} for i in range(len(replies))]}
    result = detector._test_modbus_rtu_configured('FAKE-RTU-ONLY', {'timeout':.02}, protocol)
    assert port.closed
    assert port.reset_count == len(port.requests) == len(detector.connection_tests)
    # Independent request literals: read-only function03, register0/1, quantity1.
    assert [p.hex() for p in port.requests] == ['010300000001840a','010300010001d5ca'][:len(port.requests)]
    return result, detector.connection_tests, port


def test_configured_read_does_not_mark_undefined_exception_as_responding(monkeypatch):
    result, evidence, _ = configured(monkeypatch, [exception_frame(0)])
    assert not result['ok']
    assert result['status'] == '需确认'
    assert '未定义' in result['summary']
    assert evidence[0]['response'] == '01 83 00 41 30'


def test_any_mode_can_continue_to_a_later_valid_read(monkeypatch):
    read = bytes.fromhex('01 03 02 00 2A 39 9B')
    result, evidence, port = configured(monkeypatch, [exception_frame(0xFF), read])
    assert result['ok']
    assert len(port.requests) == 2
    assert [x['status'] for x in evidence] == ['需确认', '有响应']


def test_all_mode_stops_at_unsupported_exception(monkeypatch):
    result, evidence, port = configured(monkeypatch, [exception_frame(0), exception_frame(2)], mode='all')
    assert not result['ok']
    assert len(port.requests) == len(evidence) == 1


@pytest.mark.parametrize('accept', [False, True])
def test_configured_defined_exception_keeps_existing_policy_and_evidence(monkeypatch, accept):
    result, evidence, _ = configured(monkeypatch, [bytes.fromhex('01 83 02 C0 F1')], accept=accept)
    assert result['ok'] is accept
    assert ('按配置允许' in result['summary']) is accept
    assert '0x02' in result['summary']
    assert evidence[0]['response'] == '01 83 02 C0 F1'
