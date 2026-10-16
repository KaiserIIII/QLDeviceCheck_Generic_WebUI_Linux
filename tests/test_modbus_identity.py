"""Target identity regressions: pure bytes, fake sockets and loopback only."""
import copy

import pytest

from core.config_manager import StandardDeviceConfig
from core.protocol_engine import (
    ProtocolConfigError, append_checksum, build_modbus_rtu_read,
    build_modbus_tcp_read, find_modbus_rtu_response, validate_modbus_tcp_response,
)
from inspection.adapters import demo_config
from test_http import api


def configured(tcp, value):
    config = demo_config()
    config['devices'][int(tcp)]['protocol']['unit_id' if tcp else 'slave_id'] = value
    return config


def build(tcp, value):
    return build_modbus_tcp_read(1, value, 3, 0, 1) if tcp else build_modbus_rtu_read(value, 3, 0, 1)


def response(tcp, value):
    if tcp:
        return b'\x00\x01\x00\x00\x00\x05' + bytes([value, 3, 2, 0, 42])
    return append_checksum(bytes([value, 3, 2, 0, 42]), 'crc16_modbus')


def receive(tcp, packet, expected):
    return (validate_modbus_tcp_response(packet, 1, expected, 3, expected_count=1)
            if tcp else find_modbus_rtu_response(packet, expected, 3, expected_count=1))


@pytest.mark.parametrize('tcp', [False, True], ids=['rtu', 'tcp'])
@pytest.mark.parametrize('value', [-1, 256, 257, True, False, 1.9, None, '1', float('nan'), float('inf')])
def test_byte_encoder_rejects_invalid_target_instead_of_wrapping(tcp, value):
    with pytest.raises(ProtocolConfigError):
        build(tcp, value)


@pytest.mark.parametrize('tcp', [False, True], ids=['rtu', 'tcp'])
@pytest.mark.parametrize('value', [-1, 256, 257, True, False, 1.9, None, '1.9', float('nan'), float('inf')])
def test_configuration_rejects_invalid_target_before_any_adapter(tcp, value):
    with pytest.raises(ValueError):
        StandardDeviceConfig(data=configured(tcp, value))


@pytest.mark.parametrize('value', [0, 248, 255])
def test_response_bearing_rtu_read_refuses_broadcast_and_reserved_targets(value):
    with pytest.raises(ValueError):
        StandardDeviceConfig(data=configured(False, value))
    with pytest.raises(ProtocolConfigError):
        build(False, value)


@pytest.mark.parametrize('tcp', [False, True], ids=['rtu', 'tcp'])
@pytest.mark.parametrize('expected', [257, -255, True])
def test_unit_one_response_never_satisfies_invalid_expected_identity(tcp, expected):
    with pytest.raises(ProtocolConfigError):
        receive(tcp, response(tcp, 1), expected)


@pytest.mark.parametrize('tcp', [False, True], ids=['rtu', 'tcp'])
def test_every_legal_target_roundtrips_without_aliasing(tcp):
    targets = range(256) if tcp else range(1, 248)
    for target in targets:
        request = build(tcp, target)
        assert request[6 if tcp else 0] == target
        assert receive(tcp, response(tcp, target), target)[0]
        other = (target + 1) % 256 if tcp else target % 247 + 1
        assert not receive(tcp, response(tcp, other), target)[0]


@pytest.mark.parametrize('tcp,value,target', [(False, '247', 247), (False, 247.0, 247), (True, '0', 0), (True, 255.0, 255)])
def test_config_keeps_existing_integral_numeric_compatibility(tcp, value, target):
    config = configured(tcp, value)
    before = copy.deepcopy(config)
    StandardDeviceConfig(data=config)
    assert config == before
    assert build(tcp, int(value))[6 if tcp else 0] == target


@pytest.mark.parametrize('path', ['configured', 'discovery'])
@pytest.mark.parametrize('unit', [0, 0.0])
def test_fake_socket_flow_preserves_explicit_tcp_zero(monkeypatch, path, unit):
    from core import generic_detector
    sent = []

    class FakeSocket:
        def settimeout(self, timeout):
            pass
        def connect(self, endpoint):
            assert endpoint == ('fixture.invalid', 1502)
        def sendall(self, data):
            self.request = data
            sent.append(data)
        def recv(self, size):
            return self.request[:2] + b'\x00\x00\x00\x05\x00\x03\x02\x00\x2a'
        def close(self):
            pass

    monkeypatch.setattr(generic_detector.socket, 'socket', lambda *args: FakeSocket())
    detector = object.__new__(generic_detector.GenericDetector)
    detector.config = StandardDeviceConfig(data=configured(True, unit))
    detector.connection_tests = []
    detector._bind_network_socket = lambda *args: None
    detector._socket_interface = lambda *args: 'SIM-ETH0'
    if path == 'configured':
        protocol = detector.config.data['devices'][1]['protocol']
        result = detector._test_modbus_tcp_configured('fixture.invalid', 1502, 'SIM-ETH0', protocol)
        assert result['ok']
    else:
        assert detector._modbus_tcp_standard_probe('fixture.invalid', 1502)
    assert sent and all(packet[6] == 0 for packet in sent)


@pytest.mark.parametrize('tcp', [False, True], ids=['rtu', 'tcp'])
def test_http_config_validation_rejects_target_alias(api, tcp):
    service, server, request = api
    status, body = request('POST', '/api/config/validate', {'config': configured(tcp, 257)})
    assert status == 400 and not body['ok']
    assert request('GET', '/api/health')[1]['persistence_ready']
    assert service.store.all() == []
