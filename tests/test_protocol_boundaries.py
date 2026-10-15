"""Pure byte/config regressions; never create transports or contact devices."""
import copy

import pytest

from core.config_manager import StandardDeviceConfig
from core.protocol_engine import (
    ProtocolConfigError, append_checksum, build_modbus_rtu_read,
    build_modbus_tcp_read, match_response, response_rule,
)
from inspection.adapters import demo_config


@pytest.mark.parametrize('guard', [
    {'min_length': 4}, {'max_length': 1}, {'length': 4},
    {'exact_hex': '00 00'}, {'prefix_hex': '00'}, {'suffix_hex': '00'},
    {'contains_hex': '00'}, {'exact_text': 'NO'}, {'contains_text': 'NO'},
    {'regex_text': '^NO$'}, {'regex_hex': '^00'}, {'checksum': 'sum8'},
])
def test_one_of_cannot_bypass_other_response_conditions(guard):
    rule=dict(guard, one_of=[{'exact_text':'OK'}, {'exact_text':'READY'}])
    assert not match_response(b'OK', rule)[0], guard


def test_parent_checksum_still_applies_after_probe_rule_merge():
    protocol={'response_match':{'checksum':'crc16_modbus'}}
    probe={'response_match':{'one_of':[{'prefix_hex':'01 03'}, {'prefix_hex':'01 04'}]}}
    rule=response_rule(protocol,probe)
    valid=append_checksum(bytes.fromhex('01 03 02 00 2A'),'crc16_modbus')
    assert match_response(valid,rule)[0]
    assert not match_response(valid[:-1]+bytes([valid[-1]^1]),rule)[0]


@pytest.mark.parametrize('function,limit', [(1,2000),(2,2000),(3,125),(4,125)])
@pytest.mark.parametrize('transport', ['rtu','tcp'])
def test_modbus_request_quantity_boundary(function,limit,transport):
    build=(lambda count:build_modbus_rtu_read(1,function,0,count)) if transport=='rtu' else (lambda count:build_modbus_tcp_read(1,1,function,0,count))
    assert build(limit)
    for count in [0,-1,limit+1,65536]:
        with pytest.raises(ProtocolConfigError):
            build(count)


@pytest.mark.parametrize('transport', ['rtu','tcp'])
@pytest.mark.parametrize('address,count', [(-1,1),(65536,1),(65535,2),(65534,3)])
def test_request_never_wraps_an_address_or_requested_span(transport,address,count):
    with pytest.raises(ProtocolConfigError):
        if transport=='rtu':build_modbus_rtu_read(1,3,address,count)
        else:build_modbus_tcp_read(1,1,3,address,count)


@pytest.mark.parametrize('function,limit', [(1,2000),(2,2000),(3,125),(4,125)])
@pytest.mark.parametrize('kind', ['modbus_rtu','modbus_tcp'])
def test_config_rejects_function_specific_quantity_and_address_overrun(function,limit,kind):
    for address,count in [(0,limit+1),(0,0),(65535,2)]:
        config=demo_config()
        index=0 if kind=='modbus_rtu' else 1
        config['devices'][index]['protocol']={'type':kind,'function_code':function,'address':address,'count':count}
        with pytest.raises(ValueError):StandardDeviceConfig(data=config)


@pytest.mark.parametrize('function,count,address',[(1,2000,63536),(2,2000,63536),(3,125,65411),(4,125,65411),(3,1,65535)])
def test_last_valid_address_span_stays_allowed(function,count,address):
    for kind in ['modbus_rtu','modbus_tcp']:
        config=demo_config()
        index=0 if kind=='modbus_rtu' else 1
        config['devices'][index]['protocol']={'type':kind,'function_code':function,'address':address,'count':count}
        assert StandardDeviceConfig(data=config)


def test_response_conditions_are_pure_and_conjunctive_on_valid_data():
    packet=append_checksum(b'OK','sum8')
    rule={'one_of':[{'prefix_hex':'4F'},{'exact_text':'NO'}], 'prefix_hex':'4F 4B', 'min_length':3,'max_length':3,'checksum':'sum8'}
    before=copy.deepcopy(rule)
    assert match_response(packet,rule)[0]
    assert rule==before
    assert not match_response(packet,dict(rule,one_of=[{'exact_text':'NO'}]))[0]


@pytest.mark.parametrize('field,value', [
    ('function_code',True),('function_code',3.9),('address',True),
    ('address',1.9),('count',True),('count',1.9),('count',float('inf')),
])
def test_protocol_integer_config_never_truncates_or_accepts_bool(field,value):
    config=demo_config()
    config['devices'][0]['protocol'][field]=value
    with pytest.raises(ValueError):StandardDeviceConfig(data=config)


@pytest.mark.parametrize('timeout', [float('nan'),'nan'])
def test_serial_timeout_must_be_finite(timeout):
    config=demo_config()
    config['devices'][0]['serial_config']['timeout']=timeout
    with pytest.raises(ValueError):StandardDeviceConfig(data=config)


def test_numeric_string_and_exact_integral_float_compatibility():
    for values in [{'function_code':'3','address':'0','count':'2'}, {'function_code':3.0,'address':0.0,'count':2.0}]:
        config=demo_config()
        config['devices'][0]['protocol'].update(values)
        assert StandardDeviceConfig(data=config)
