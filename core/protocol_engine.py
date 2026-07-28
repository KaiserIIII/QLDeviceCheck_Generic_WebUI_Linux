import base64
import binascii
import re
from typing import Dict, List, Optional, Tuple


READ_ONLY_MODBUS_FUNCTIONS = (1, 2, 3, 4)


class ProtocolConfigError(ValueError):
    pass


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_checksum(payload: bytes, checksum: str, byte_order: str = "little") -> bytes:
    mode = str(checksum or "").strip().lower()
    if not mode:
        return payload
    if mode in ("crc16_modbus", "modbus", "modbus_crc"):
        value = crc16_modbus(payload)
        suffix = value.to_bytes(2, "big" if byte_order == "big" else "little")
    elif mode == "sum8":
        suffix = bytes([sum(payload) & 0xFF])
    elif mode == "xor8":
        value = 0
        for byte in payload:
            value ^= byte
        suffix = bytes([value])
    else:
        raise ProtocolConfigError(f"不支持的校验算法: {checksum}")
    return payload + suffix


def decode_escaped_text(value: object, encoding: str = "utf-8") -> bytes:
    text = str(value or "")
    text = text.replace("\\r", "\r").replace("\\n", "\n").replace("\\t", "\t")
    return text.encode(encoding)


def decode_hex(value: object, field_name: str = "hex") -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    normalized = re.sub(r"0x", "", text, flags=re.IGNORECASE)
    invalid = re.sub(r"[0-9a-fA-F\s,;:_-]", "", normalized)
    if invalid:
        raise ProtocolConfigError(f"{field_name} 包含非法十六进制字符: {invalid}")
    normalized = re.sub(r"[^0-9a-fA-F]", "", normalized)
    if len(normalized) % 2:
        raise ProtocolConfigError(f"{field_name} 必须包含完整字节，当前十六进制位数为奇数")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise ProtocolConfigError(f"{field_name} 不是有效十六进制数据: {exc}") from exc


def protocol_probes(protocol: Dict[str, object]) -> List[Dict[str, object]]:
    probes = protocol.get("probes", []) or []
    if probes:
        if not isinstance(probes, list):
            raise ProtocolConfigError("protocol.probes 必须是数组")
        return [dict(item or {}) for item in probes]
    return [dict(protocol)]


def request_payload(probe: Dict[str, object]) -> bytes:
    if str(probe.get("request_mode") or "").lower() in ("passive", "listen", "none"):
        return b""
    encoding = str(probe.get("encoding") or "utf-8")
    for key in ("request_hex", "test_command_hex", "payload_hex", "test_payload_hex"):
        if probe.get(key) not in (None, ""):
            payload = decode_hex(probe.get(key), key)
            break
    else:
        for key in ("request_base64", "payload_base64"):
            if probe.get(key) not in (None, ""):
                try:
                    payload = base64.b64decode(str(probe.get(key)), validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ProtocolConfigError(f"{key} 不是有效Base64数据: {exc}") from exc
                break
        else:
            payload = b""
            for key in ("request_text", "test_command", "payload_text", "test_payload"):
                if probe.get(key) not in (None, ""):
                    payload = decode_escaped_text(probe.get(key), encoding)
                    break
    checksum = str(probe.get("append_checksum") or probe.get("append_crc") or "")
    return append_checksum(payload, checksum, str(probe.get("checksum_byte_order") or "little"))


def response_rule(protocol: Dict[str, object], probe: Dict[str, object]) -> Dict[str, object]:
    rule = dict(protocol.get("response_match", {}) or {})
    rule.update(dict(probe.get("response_match", {}) or {}))
    legacy = {
        "expected_response_pattern": "regex_text",
        "expected_response_hex": "exact_hex",
        "expected_response_prefix_hex": "prefix_hex",
        "expected_response_contains_hex": "contains_hex",
    }
    for old_key, new_key in legacy.items():
        value = probe.get(old_key, protocol.get(old_key))
        if value not in (None, "") and new_key not in rule:
            rule[new_key] = value
    return rule


def _check_crc(data: bytes, mode: str) -> bool:
    normalized = str(mode or "").lower()
    if normalized in ("crc16_modbus", "modbus", "modbus_crc"):
        if len(data) < 3:
            return False
        expected = data[-2] | (data[-1] << 8)
        return crc16_modbus(data[:-2]) == expected
    if normalized == "sum8":
        return len(data) >= 2 and (sum(data[:-1]) & 0xFF) == data[-1]
    if normalized == "xor8":
        if len(data) < 2:
            return False
        value = 0
        for byte in data[:-1]:
            value ^= byte
        return value == data[-1]
    raise ProtocolConfigError(f"不支持的响应校验算法: {mode}")


def match_response(data: bytes, rule: Optional[Dict[str, object]] = None) -> Tuple[bool, str]:
    rule = dict(rule or {})
    if not data:
        return False, "未收到响应"

    if "one_of" in rule:
        matches = [match_response(data, item or {}) for item in rule.get("one_of", []) or []]
        for ok, note in matches:
            if ok:
                return True, note
        return False, "响应不符合任一候选规则"

    length = len(data)
    if "min_length" in rule and length < int(rule["min_length"]):
        return False, f"响应长度{length}小于要求{rule['min_length']}"
    if "max_length" in rule and length > int(rule["max_length"]):
        return False, f"响应长度{length}大于要求{rule['max_length']}"
    if "length" in rule and length != int(rule["length"]):
        return False, f"响应长度{length}不等于要求{rule['length']}"

    byte_rules = (
        ("exact_hex", lambda expected: data == expected, "响应内容不完全一致"),
        ("prefix_hex", lambda expected: data.startswith(expected), "响应前缀不匹配"),
        ("suffix_hex", lambda expected: data.endswith(expected), "响应后缀不匹配"),
        ("contains_hex", lambda expected: expected in data, "响应不包含指定字节"),
    )
    for key, check, message in byte_rules:
        if rule.get(key) not in (None, ""):
            if not check(decode_hex(rule[key], f"response_match.{key}")):
                return False, message

    encoding = str(rule.get("encoding") or "utf-8")
    text = data.decode(encoding, errors="replace")
    if rule.get("exact_text") not in (None, "") and text != str(rule["exact_text"]):
        return False, "响应文本不完全一致"
    if rule.get("contains_text") not in (None, "") and str(rule["contains_text"]) not in text:
        return False, "响应文本不包含指定内容"
    if rule.get("regex_text") not in (None, ""):
        if re.search(str(rule["regex_text"]), text) is None:
            return False, "响应文本不符合正则表达式"
    if rule.get("regex_hex") not in (None, ""):
        hex_text = data.hex(" ").upper()
        if re.search(str(rule["regex_hex"]), hex_text) is None:
            return False, "响应十六进制内容不符合正则表达式"
    if rule.get("checksum") not in (None, "") and not _check_crc(data, str(rule["checksum"])):
        return False, "响应校验和不正确"
    return True, f"响应符合配置规则（{length}字节）"


def build_modbus_rtu_read(slave_id: int, function_code: int, address: int, count: int) -> bytes:
    if function_code not in READ_ONLY_MODBUS_FUNCTIONS:
        raise ProtocolConfigError(f"Modbus RTU自动检测只允许只读功能码1、2、3、4，当前为{function_code}")
    payload = bytes([
        slave_id & 0xFF,
        function_code & 0xFF,
        (address >> 8) & 0xFF,
        address & 0xFF,
        (count >> 8) & 0xFF,
        count & 0xFF,
    ])
    return append_checksum(payload, "crc16_modbus")


def find_modbus_rtu_response(data: bytes, slave_id: int, function_code: int,
                             accept_exception: bool = False) -> Tuple[bool, str, bytes]:
    if not data:
        return False, "未收到响应", b""
    for start in range(max(0, len(data) - 4)):
        if data[start] != (slave_id & 0xFF) or start + 4 >= len(data):
            continue
        actual_function = data[start + 1]
        is_exception = actual_function == (function_code | 0x80)
        if is_exception:
            frame_length = 5
        elif actual_function == function_code:
            frame_length = 5 + data[start + 2]
        else:
            continue
        end = start + frame_length
        if end > len(data):
            continue
        frame = data[start:end]
        if not _check_crc(frame, "crc16_modbus"):
            continue
        if is_exception:
            code = frame[2]
            if accept_exception:
                return True, f"设备返回Modbus异常码0x{code:02X}，按配置允许异常响应", frame
            return False, f"设备返回Modbus异常码0x{code:02X}", frame
        return True, "返回有效Modbus RTU响应", frame
    return False, "收到数据，但站号、功能码或CRC不匹配", b""


def build_modbus_tcp_read(transaction_id: int, unit_id: int, function_code: int,
                          address: int, count: int) -> bytes:
    if function_code not in READ_ONLY_MODBUS_FUNCTIONS:
        raise ProtocolConfigError(f"Modbus TCP自动检测只允许只读功能码1、2、3、4，当前为{function_code}")
    pdu = bytes([
        function_code & 0xFF,
        (address >> 8) & 0xFF,
        address & 0xFF,
        (count >> 8) & 0xFF,
        count & 0xFF,
    ])
    return (
        transaction_id.to_bytes(2, "big")
        + b"\x00\x00"
        + (len(pdu) + 1).to_bytes(2, "big")
        + bytes([unit_id & 0xFF])
        + pdu
    )


def validate_modbus_tcp_response(data: bytes, transaction_id: int, unit_id: int,
                                  function_code: int, accept_exception: bool = False) -> Tuple[bool, str, bytes]:
    if len(data) < 9:
        return False, "Modbus TCP响应长度不足", b""
    if int.from_bytes(data[0:2], "big") != transaction_id or data[2:4] != b"\x00\x00":
        return False, "Modbus TCP事务号或协议标识不匹配", b""
    declared_length = int.from_bytes(data[4:6], "big")
    frame_length = 6 + declared_length
    if declared_length < 3 or len(data) < frame_length:
        return False, "Modbus TCP长度字段不正确", b""
    frame = data[:frame_length]
    if frame[6] != (unit_id & 0xFF):
        return False, "Modbus TCP单元标识不匹配", frame
    actual_function = frame[7]
    if actual_function == (function_code | 0x80):
        code = frame[8]
        if accept_exception:
            return True, f"设备返回Modbus异常码0x{code:02X}，按配置允许异常响应", frame
        return False, f"设备返回Modbus异常码0x{code:02X}", frame
    if actual_function != function_code:
        return False, "Modbus TCP功能码不匹配", frame
    return True, "返回有效Modbus TCP响应", frame
