#!/usr/bin/env python3
"""Offline regression checks. No industrial device is accessed."""

import socket
import threading

from core.config_manager import StandardDeviceConfig
from core.generic_detector import GenericDetector
from core.protocol_engine import (
    append_checksum,
    build_modbus_rtu_read,
    build_modbus_tcp_read,
    find_modbus_rtu_response,
    match_response,
    request_payload,
    validate_modbus_tcp_response,
)
from web_app import preserve_scan_interfaces


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def start_tcp_fixture():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        try:
            conn, _ = server.accept()
            with conn:
                if conn.recv(64) == b"PING\r\n":
                    conn.sendall(b"PONG DEVICE-01")
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


def start_udp_fixture():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def serve():
        try:
            payload, source = server.recvfrom(64)
            if payload == bytes.fromhex("AA 01"):
                server.sendto(bytes.fromhex("55 AA 01 00"), source)
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return port, thread


def main():
    request = build_modbus_rtu_read(1, 1, 0, 1)
    check("Modbus RTU读线圈请求", request.hex(" ").upper() == "01 01 00 00 00 01 FD CA")

    response = append_checksum(bytes.fromhex("01 01 01 01"), "crc16_modbus")
    ok, _, frame = find_modbus_rtu_response(response, 1, 1)
    check("Modbus RTU有效响应", ok and frame == response)
    check("串口请求回显不会误判", not find_modbus_rtu_response(request, 1, 1)[0])

    exception = append_checksum(bytes.fromhex("01 81 02"), "crc16_modbus")
    check("Modbus异常默认判失败", not find_modbus_rtu_response(exception, 1, 1)[0])
    check("配置允许时接受Modbus异常", find_modbus_rtu_response(exception, 1, 1, True)[0])

    check("十六进制请求", request_payload({"request_hex": "AA 55 01"}) == bytes.fromhex("AA5501"))
    check("转义文本请求", request_payload({"request_text": "PING\\r\\n"}) == b"PING\r\n")
    check("响应前缀匹配", match_response(bytes.fromhex("AA 55 10 20"), {"prefix_hex": "AA55"})[0])
    check("响应文本正则", match_response(b"TEMP=23.5", {"regex_text": r"TEMP=\d+\.\d+"})[0])

    tcp_request = build_modbus_tcp_read(1, 1, 3, 0, 1)
    check("Modbus TCP请求", tcp_request.hex(" ").upper() == "00 01 00 00 00 06 01 03 00 00 00 01")
    tcp_response = bytes.fromhex("00 01 00 00 00 05 01 03 02 00 2A")
    check("Modbus TCP有效响应", validate_modbus_tcp_response(tcp_response, 1, 1, 3)[0])

    config = StandardDeviceConfig()
    check("正式配置加载", bool(config.raw_devices()))
    check("生产安全默认只读", not config.allow_write_tests())
    relay = next(item for item in config.raw_devices() if item.get("device_id") == "SER_RELAY_XP3018_001")
    check(
        "透明IO继电器配置",
        relay.get("device_type") == "relay"
        and (relay.get("via_device", {}) or {}).get("role") == "transparent_io"
        and relay.get("test_scope") == "communication_path",
    )
    selected = config.selected(["SER_RELAY_XP3018_001"], {"SER_RELAY_XP3018_001": "/dev/ttyS1"})
    selected_relay = selected.raw_devices()[0]
    check(
        "扫描接口可固定用于单设备复测",
        len(selected.raw_devices()) == 1 and selected_relay.get("interface") == "/dev/ttyS1",
    )
    serial_scope = config.for_interface("serial", "/dev/ttyS7")
    check(
        "单串口扫描仅加载串口设备",
        serial_scope is not None
        and all(item.get("connection_type") == "serial" for item in serial_scope.raw_devices())
        and all(item.get("interface") == "/dev/ttyS7" for item in serial_scope.raw_devices()),
    )
    network_scope = config.for_interface("network", "eno1")
    check(
        "单网口扫描绑定指定网卡",
        network_scope is not None
        and all(item.get("connection_type") == "network" for item in network_scope.raw_devices())
        and all(item.get("interface") == "eno1" for item in network_scope.raw_devices()),
    )
    pci_scope = config.for_interface("pci", "0000:03:00.0")
    check(
        "单PCI扫描固定指定槽位",
        pci_scope is not None
        and all(item.get("connection_type") == "pci" for item in pci_scope.raw_devices())
        and all(item.get("pci_slot") == "0000:03:00.0" for item in pci_scope.raw_devices()),
    )
    scan_raw = {
        "serial_ports": [
            {"name": "/dev/ttyS0", "status": "正常", "details": {}},
            {"name": "/dev/ttyS1", "status": "正常", "details": {}},
        ],
        "network_interfaces": [{"name": "eno1", "status": "正常", "details": {}}],
        "pci_devices": [{"name": "0000:03:00.0", "status": "正常", "details": {}}],
    }
    tested_result = {
        "summary": {},
        "serial_ports": [{"name": "/dev/ttyS1", "status": "正常", "details": {}}],
        "network_interfaces": [],
        "pci_devices": [],
        "devices": [{
            "device_id": "SER_RELAY_XP3018_001",
            "name": "24VDC中间继电器",
            "connection_type": "serial",
            "interface": "/dev/ttyS1",
            "status": "正常",
        }],
    }
    preserve_scan_interfaces(tested_result, scan_raw)
    check(
        "测试全部后保留所有已扫描接口",
        [item.get("name") for item in tested_result["serial_ports"]] == ["/dev/ttyS0", "/dev/ttyS1"]
        and [item.get("name") for item in tested_result["network_interfaces"]] == ["eno1"]
        and [item.get("name") for item in tested_result["pci_devices"]] == ["0000:03:00.0"]
        and tested_result["serial_ports"][0]["details"].get("configured_devices") == []
        and tested_result["serial_ports"][1]["details"].get("configured_devices") == ["24VDC中间继电器"],
    )

    detector = GenericDetector()
    pci_config = {
        "device_type": "ethernet_controller",
        "pci_match": {"vendor_id": "10ec", "class_code": "02", "driver": ["r8169", "r8168"]},
    }
    pci_device = {"vendor_id": "10ec", "device_id": "8168", "class_code": "0200", "driver": "r8169"}
    check("PCI身份规则匹配", detector._pci_matches_config(pci_config, pci_device, {"driver": "r8169"}))
    check("PCI错误厂商不匹配", not detector._pci_matches_config(pci_config, {**pci_device, "vendor_id": "8086"}, {"driver": "r8169"}))

    tcp_port, tcp_thread = start_tcp_fixture()
    tcp_protocol = {
        "type": "fixture_tcp",
        "probes": [{
            "name": "回环TCP身份查询",
            "request_text": "PING\\r\\n",
            "response_match": {"exact_text": "PONG DEVICE-01"},
        }],
    }
    tcp_profile = detector._network_profile(
        {"protocol": tcp_protocol},
        {"network_config": {"transport": "tcp", "device_role": "server"}},
    )
    tcp_result = detector._test_tcp_protocol_configured(
        "127.0.0.1", tcp_port, "", {"timeout": 2}, tcp_protocol, tcp_profile
    )
    tcp_thread.join(timeout=2)
    check("TCP通用配置真实收发", tcp_result.get("ok"))

    udp_port, udp_thread = start_udp_fixture()
    udp_protocol = {
        "type": "fixture_udp",
        "probes": [{
            "name": "回环UDP身份查询",
            "request_hex": "AA 01",
            "response_match": {"exact_hex": "55 AA 01 00"},
        }],
    }
    udp_profile = detector._network_profile(
        {"protocol": udp_protocol},
        {"network_config": {"transport": "udp", "device_role": "server"}},
    )
    udp_result = detector._test_udp_server_configured(
        "127.0.0.1", udp_port, "", {"timeout": 2}, udp_protocol, udp_profile
    )
    udp_thread.join(timeout=2)
    check("UDP通用配置真实收发", udp_result.get("ok"))

    print("\n全部离线自检通过。")


if __name__ == "__main__":
    main()
