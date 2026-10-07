"""Industrial PC layered detection core."""

import glob
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import psutil
import serial
import serial.tools.list_ports

from .config_manager import StandardDeviceConfig
from .transport import modbus_tcp_complete, receive_response
from .protocol_engine import (
    ProtocolConfigError,
    build_modbus_rtu_read,
    build_modbus_tcp_read,
    crc16_modbus,
    find_modbus_rtu_response,
    match_response,
    protocol_probes,
    request_payload,
    response_rule,
    validate_modbus_tcp_response,
)


DEFAULT_TCP_PORTS = [502, 102, 44818, 4840]
INDUSTRIAL_TCP_PORTS = {502, 102, 44818, 4840}
MODBUS_RTU_READ_HOLDING = bytes.fromhex("01 03 00 00 00 01 84 0A")
MODBUS_TCP_DEVICE_ID = bytes.fromhex("00 01 00 00 00 05 01 2B 0E 01 00")


@dataclass
class CheckItem:
    name: str
    category: str
    status: str
    summary: str
    details: Dict[str, object] = field(default_factory=dict)
    method: str = ""
    request: str = ""
    risk_note: str = ""


@dataclass
class GenericDetectionResult:
    ok: bool
    started_at: str
    finished_at: str
    duration_ms: float
    hostname: str
    system: Dict[str, str]
    summary: Dict[str, object]
    serial_ports: List[CheckItem]
    network_interfaces: List[CheckItem]
    pci_devices: List[CheckItem]
    devices: List[dict]
    standard_devices: List[dict]
    connection_tests: List[dict]
    logs: List[str]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["serial_ports"] = [asdict(item) for item in self.serial_ports]
        data["network_interfaces"] = [asdict(item) for item in self.network_interfaces]
        data["pci_devices"] = [asdict(item) for item in self.pci_devices]
        return data


class GenericDetector:
    """Generic, read-only detector for Linux industrial PCs."""

    def __init__(
        self,
        serial_probe: bool = True,
        subnet_probe: bool = True,
        tcp_ports: Optional[List[int]] = None,
        config_path: Optional[str] = None,
        config: Optional[StandardDeviceConfig] = None,
    ):
        self.serial_probe = serial_probe
        self.subnet_probe = subnet_probe
        self.tcp_ports = tcp_ports or DEFAULT_TCP_PORTS
        self.config = config or (StandardDeviceConfig(config_path) if config_path else StandardDeviceConfig())
        self.standard_devices = self.config.devices()
        self.logs: List[str] = []
        self.connection_tests: List[dict] = []
        self._serial_scan_cache: Optional[List[str]] = None
        self._serial_candidate_count = 0
        self._pci_scan_cache: Optional[List[dict]] = None

    def run(self) -> GenericDetectionResult:
        return self._run_configured()

    def _run_configured(self) -> GenericDetectionResult:
        start = time.time()
        started_at = self._now()
        self.logs = []
        self.connection_tests = []

        raw_devices = self.config.raw_devices()
        self._log("开始第一阶段：按配置文件识别接口与设备列表...")
        serial_ports, network_interfaces, pci_devices = self._configured_interface_items(raw_devices)
        self._log(self._interface_summary(serial_ports, network_interfaces, pci_devices))
        self._log(
            "加载设备参数：配置设备 "
            f"{len(raw_devices)} 个，"
            f"中间模块 {len(self.config.intermediate_modules())} 类，"
            f"子设备 {len(self.config.child_device_templates())} 个"
        )
        self._log("开始第二阶段：按配置测试直连设备与中间模块...")
        devices: List[dict] = []
        parent_results: Dict[str, dict] = {}
        for config in raw_devices:
            phase = "中间模块测试" if config.get("is_intermediate_module") else "直连设备测试"
            result = self._test_configured_device(config, phase)
            devices.append(result)
            parent_results[str(config.get("device_id", ""))] = result

        self._log(self._configured_direct_summary(devices))
        self._log("开始第三阶段：按配置测试中间模块子设备...")
        for parent in raw_devices:
            children = parent.get("child_devices", []) or []
            if not children:
                continue
            parent_result = parent_results.get(str(parent.get("device_id", "")), {})
            for child in children:
                devices.append(self._test_configured_child_device(child, parent, parent_result))
        self._attach_devices_to_interface_items(serial_ports, network_interfaces, pci_devices, devices)
        self._log(self._configured_child_summary(devices))
        self._log("开始第四阶段：通信协议交互验证...")
        self._log(self._configured_protocol_summary())

        finished_at = self._now()
        duration_ms = (time.time() - start) * 1000
        all_items = serial_ports + network_interfaces + pci_devices
        total = len(devices)
        passed = sum(1 for item in devices if item.get("status") in ("在线", "已发现", "正常"))
        warning = sum(1 for item in devices if item.get("status") == "需确认")
        failed = sum(1 for item in devices if item.get("status") == "异常")
        link_total = len(self.connection_tests)
        link_passed = sum(
            1
            for item in self.connection_tests
            if item.get("status") in ("连通", "有响应", "已发现")
        )
        link_failed = sum(
            1
            for item in self.connection_tests
            if item.get("status") not in ("连通", "有响应", "已发现", "正常", "需确认")
        )
        link_no_response = sum(
            1
            for item in self.connection_tests
            if item.get("status") == "需确认"
        )
        modbus_responses = sum(
            1
            for item in self.connection_tests
            if "Modbus" in item.get("type", "") and item.get("status") == "有响应"
        )
        matched = passed

        self._log(self._response_decision_summary(link_passed, link_no_response, link_failed))
        self._log("开始第五阶段：测试结果汇总...")
        self._log("结果输出：网页汇总配置设备、接口映射、协议验证和故障项")
        self._log(f"测试完成：配置设备 {len(devices)} 个，通过 {passed} 个，失败 {failed} 个")
        return GenericDetectionResult(
            ok=True,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            hostname=socket.gethostname(),
            system=self._system_info(),
            summary={
                "total": total,
                "passed": passed,
                "warning": warning,
                "failed": failed,
                "device_count": len(devices),
                "interface_total": len(all_items),
                "serial_count": len(serial_ports),
                "network_count": len(network_interfaces),
                "pci_count": len(pci_devices),
                "link_total": link_total,
                "link_passed": link_passed,
                "link_no_response": link_no_response,
                "link_failed": link_failed,
                "modbus_responses": modbus_responses,
                "matched_count": matched,
                "standard_count": len(self.standard_devices),
                "direct_standard_count": len(self.config.direct_devices()),
                "intermediate_standard_count": len(self.config.intermediate_modules()),
                "child_template_count": len(self.config.child_device_templates()),
                "fault_count": failed,
                "configured_count": len(devices),
                "workstation_id": self.config.workstation_id(),
                "description": self.config.description(),
                "display_policy": "只检测配置文件中的设备",
                "scan_strategy": "读取配置 -> 枚举全部串口/检查配置网口PCI -> 测试直连设备/中间模块 -> 测试子设备 -> 汇总结果",
            },
            serial_ports=serial_ports,
            network_interfaces=network_interfaces,
            pci_devices=pci_devices,
            devices=devices,
            standard_devices=self.standard_devices,
            connection_tests=self.connection_tests,
            logs=self.logs,
        )

    def _configured_interface_items(self, raw_devices: List[dict]):
        mapping = self._configured_interface_mapping(raw_devices)
        pci_configs = self._configured_pci_config_by_slot(raw_devices)
        if self._has_serial_scan_targets(raw_devices):
            self._log("串口扫描策略：配置文件不指定固定串口，枚举本机所有串口并逐个匹配配置设备")
            for port in self._all_serial_scan_ports():
                mapping["serial"].setdefault(port, [])
        if self._has_network_auto_targets(raw_devices):
            self._log("网口选择策略：配置文件未固定网卡，按目标IP由Linux路由选择实际网口")
            for iface, label in self._configured_auto_network_routes(raw_devices):
                mapping["network"].setdefault(iface, []).append(label)
        for config, label in self._configured_auto_pci_targets(raw_devices):
            matches = self._matching_pci_devices(config)
            if matches:
                for dev in matches:
                    slot = str(dev.get("slot") or "")
                    if not slot:
                        continue
                    mapping["pci"].setdefault(slot, []).append(label)
                    pci_configs.setdefault(slot, config)
            else:
                key = f"匹配规则:{config.get('device_id', 'PCI设备')}"
                mapping["pci"].setdefault(key, []).append(label)
                pci_configs[key] = config
        serial_items = [
            self._configured_serial_item(port, names)
            for port, names in sorted(mapping.get("serial", {}).items())
        ]
        network_items = [
            self._configured_network_item(name, names)
            for name, names in sorted(mapping.get("network", {}).items())
        ]
        pci_items = [
            self._configured_pci_item(slot, names, pci_configs.get(slot, {}))
            for slot, names in sorted(mapping.get("pci", {}).items())
        ]
        return serial_items, network_items, pci_items

    def _has_network_auto_targets(self, raw_devices: List[dict]) -> bool:
        for device in raw_devices:
            if self._configured_connection_type(device) == "network" and not device.get("interface"):
                return True
            for child in device.get("child_devices", []) or []:
                if self._configured_connection_type(child, device) == "network" and not child.get("interface") and not device.get("interface"):
                    return True
        return False

    def _configured_auto_network_routes(self, raw_devices: List[dict]):
        routes = []
        for device in raw_devices:
            if self._configured_connection_type(device) != "network" or device.get("interface"):
                continue
            net_cfg = device.get("network_config", {}) or {}
            role = self._network_role_code(str(net_cfg.get("device_role") or "server")) or "server"
            iface = self._route_interface_for_target(str(net_cfg.get("ip") or "")) if role == "server" else "全部网口监听"
            routes.append((iface or "自动路由未确定", str(device.get("device_name") or device.get("device_id") or "网口设备")))
        return routes

    def _route_interface_for_target(self, target_ip: str) -> str:
        if not target_ip:
            return ""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target_ip, 9))
            return self._interface_for_local_ip(str(sock.getsockname()[0]))
        except OSError:
            return ""
        finally:
            sock.close()

    def _configured_auto_pci_targets(self, raw_devices: List[dict]):
        targets = []
        for device in raw_devices:
            if self._configured_connection_type(device) == "pci" and not device.get("pci_slot"):
                targets.append((device, str(device.get("device_name") or device.get("device_id") or "PCI设备")))
        return targets

    def _has_serial_scan_targets(self, raw_devices: List[dict]) -> bool:
        for device in raw_devices:
            if self._configured_connection_type(device) == "serial" and not device.get("interface"):
                return True
            for child in device.get("child_devices", []) or []:
                if self._configured_connection_type(child, device) == "serial" and not child.get("interface") and not device.get("interface"):
                    return True
        return False

    def _all_serial_scan_ports(self) -> List[str]:
        if self._serial_scan_cache is not None:
            return self._serial_scan_cache
        ports = set()
        for port in serial.tools.list_ports.comports():
            if port.device:
                ports.add(port.device)
        for pattern in (
            "/dev/ttyS*",
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
            "/dev/ttyAMA*",
            "/dev/ttyTHS*",
            "/dev/ttyAP*",
            "/dev/ttyXRUSB*",
        ):
            ports.update(glob.glob(pattern))
        ports.update(self._pci_tty_nodes())
        self._serial_candidate_count = len(ports)
        self._serial_scan_cache = sorted(
            (port for port in ports if self._is_usable_serial_scan_port(port)),
            key=self._serial_sort_key,
        )
        if self._serial_candidate_count != len(self._serial_scan_cache):
            self._log(
                f"串口候选节点 {self._serial_candidate_count} 个，"
                f"过滤不可打开/无效节点 {self._serial_candidate_count - len(self._serial_scan_cache)} 个，"
                f"实际参与设备匹配 {len(self._serial_scan_cache)} 个"
            )
        return self._serial_scan_cache

    def _is_usable_serial_scan_port(self, port: str) -> bool:
        if not port or not os.path.exists(port):
            return False
        name = os.path.basename(port)
        if name.startswith(("ttyUSB", "ttyACM", "ttyAMA", "ttyTHS", "ttyAP", "ttyXRUSB")):
            return True
        try:
            ser = serial.Serial(port, timeout=0.12)
            ser.close()
            return True
        except PermissionError:
            return True
        except serial.SerialException as exc:
            message = str(exc)
            if "Errno 13" in message or "Permission" in message or "权限" in message:
                return True
            return False
        except OSError:
            return False

    def _serial_sort_key(self, port: str):
        name = os.path.basename(port)
        match = re.match(r"([A-Za-z_/]+)(\d+)$", port)
        prefix = match.group(1) if match else port
        number = int(match.group(2)) if match else 99999
        family_order = 0
        if name.startswith("ttyUSB"):
            family_order = 1
        elif name.startswith("ttyACM"):
            family_order = 2
        elif name.startswith("ttyAMA"):
            family_order = 3
        elif name.startswith("ttyS"):
            family_order = 4
        elif name.startswith("ttyTHS"):
            family_order = 5
        return (family_order, prefix, number, port)

    def _configured_interface_mapping(self, raw_devices: List[dict]) -> Dict[str, Dict[str, List[str]]]:
        mapping: Dict[str, Dict[str, List[str]]] = {"serial": {}, "network": {}, "pci": {}}
        for device in raw_devices:
            self._add_configured_interface(mapping, device)
            for child in device.get("child_devices", []) or []:
                self._add_configured_interface(mapping, child, parent=device)
        return mapping

    def _configured_pci_config_by_slot(self, raw_devices: List[dict]) -> Dict[str, dict]:
        configs: Dict[str, dict] = {}
        for device in raw_devices:
            slot = str(device.get("pci_slot") or "")
            if slot:
                configs.setdefault(slot, device)
            for child in device.get("child_devices", []) or []:
                child_slot = str(child.get("pci_slot") or device.get("pci_slot") or "")
                if child_slot:
                    configs.setdefault(child_slot, device if device.get("pci_slot") else child)
        return configs

    def _configured_connection_type(self, device: dict, parent: Optional[dict] = None) -> str:
        explicit = str(device.get("connection_type") or "").strip()
        if explicit:
            return explicit
        interface = str(device.get("interface") or "").strip()
        if interface.startswith("/dev/tty") or device.get("serial_config"):
            return "serial"
        if device.get("network_config"):
            return "network"
        if device.get("pci_slot") or device.get("pci_match") or device.get("provided_interfaces"):
            return "pci"
        return str((parent or {}).get("connection_type") or "").strip()

    def _add_configured_interface(self, mapping: Dict[str, Dict[str, List[str]]], device: dict, parent: Optional[dict] = None):
        carrier = parent or device
        connection_type = self._configured_connection_type(device, parent)
        name = str(device.get("device_name") or device.get("device_id") or "未命名设备")
        if parent:
            name = f"{name}(经{parent.get('device_name', '中间模块')})"
        if connection_type == "serial":
            iface = str(device.get("interface") or carrier.get("interface") or "")
            if iface:
                mapping["serial"].setdefault(iface, []).append(name)
        elif connection_type == "network":
            iface = str(device.get("interface") or carrier.get("interface") or "")
            if iface:
                mapping["network"].setdefault(iface, []).append(name)
        elif connection_type == "pci":
            slot = str(device.get("pci_slot") or carrier.get("pci_slot") or "")
            if slot:
                mapping["pci"].setdefault(slot, []).append(name)

    def _configured_serial_item(self, port: str, names: List[str]) -> CheckItem:
        details = {"configured_devices": names, "access": "未测试", "scan_mode": "全串口设备匹配"}
        if not os.path.exists(port):
            status = "异常"
            summary = f"串口不存在，关联设备 {len(names)} 个"
            details["access"] = "不存在"
        else:
            try:
                ser = serial.Serial(port, timeout=0.2)
                ser.close()
                status = "正常"
                summary = f"串口可打开，等待配置设备匹配"
                details["access"] = "可打开"
            except PermissionError as exc:
                status = "异常"
                summary = "串口权限不足"
                details["access"] = f"权限不足: {exc}"
            except serial.SerialException as exc:
                status = "异常"
                summary = "串口打开失败"
                details["access"] = f"打开失败: {exc}"
        return CheckItem(
            name=port,
            category="串口",
            status=status,
            summary=summary,
            details=details,
            method="枚举本机串口 + open/close访问测试",
            request=f"open/close {port}",
            risk_note="接口阶段只做访问检查；设备阶段再按配置协议逐个串口只读探测",
        )

    def _configured_network_item(self, iface: str, names: List[str]) -> CheckItem:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        details = {"configured_devices": names}
        if iface in ("自动路由未确定", "全部网口监听"):
            return CheckItem(
                name=iface,
                category="网口",
                status="需确认",
                summary=f"{'等待设备主动接入' if iface == '全部网口监听' else '将在连接目标时由Linux选择网口'}，关联设备 {len(names)} 个",
                details=details,
                method="按配置目标IP自动路由/按配置端口监听",
                request=iface,
                risk_note="不扫描无关主机",
            )
        if iface not in addrs:
            return CheckItem(
                name=iface,
                category="网口",
                status="异常",
                summary=f"配置网口不存在，关联设备 {len(names)} 个",
                details=details,
                method="按配置文件检查指定网口",
                request=f"psutil.net_if_addrs()[{iface}]",
                risk_note="只检查配置网口，不扫描其他网口",
            )
        ipv4 = ""
        mac = ""
        for addr in addrs.get(iface, []):
            family_name = getattr(addr.family, "name", str(addr.family))
            if addr.family == socket.AF_INET or family_name == "AF_INET":
                ipv4 = addr.address
            elif family_name in ("AF_PACKET", "AF_LINK"):
                mac = addr.address
        stat = stats.get(iface)
        status = "正常" if stat and stat.isup else "需确认"
        summary = f"配置网口{'UP' if stat and stat.isup else '未启用'}，关联设备 {len(names)} 个"
        details.update({"ip": ipv4, "mac": mac, "is_up": bool(stat and stat.isup), "speed": f"{stat.speed}Mbps" if stat and stat.speed else ""})
        return CheckItem(
            name=iface,
            category="网口",
            status=status,
            summary=summary,
            details=details,
            method="按配置文件检查指定网口",
            request=f"读取 {iface} IP/MAC/UP状态",
            risk_note="只检查配置网口，不做全网扫描",
        )

    def _configured_pci_item(self, slot: str, names: List[str], config: Optional[dict] = None) -> CheckItem:
        if slot.startswith("匹配规则:"):
            return CheckItem(
                name=slot,
                category="PCI",
                status="异常",
                summary=f"未找到符合配置匹配规则的PCI设备，关联设备 {len(names)} 个",
                details={"configured_devices": names, "pci_match": (config or {}).get("pci_match", {})},
                method="按配置文件的PCI标识规则匹配本机设备",
                request="读取lspci/sysfs并匹配vendor/device/class/driver",
                risk_note="PCI被动读取，不改变设备状态",
            )
        dev = self._find_pci_by_slot(slot)
        details = {"configured_devices": names}
        if not dev:
            return CheckItem(
                name=slot,
                category="PCI",
                status="异常",
                summary=f"配置PCI设备不存在，关联设备 {len(names)} 个",
                details=details,
                method="按配置文件检查指定PCI槽位",
                request=f"lspci/sysfs slot {slot}",
                risk_note="只读取配置PCI槽位，不扫描无关PCI设备",
            )
        checks = self._pci_passive_checks(dev)
        details.update({**dev, **checks})
        if config and not self._pci_matches_config(config, dev, checks):
            actual = dev.get("class") or dev.get("description") or "未知PCI设备"
            driver = checks.get("driver") or dev.get("driver") or "未绑定驱动"
            details["match"] = "不匹配"
            return CheckItem(
                name=slot,
                category="PCI",
                status="异常",
                summary=f"PCI槽位存在但不匹配：实际为 {actual}，驱动 {driver}",
                details=details,
                method="按配置文件检查指定PCI槽位",
                request=f"lspci/sysfs slot {slot}",
                risk_note="PCI被动读取，不改变设备状态",
            )
        summary = "；".join(checks.get("passive_checks", []) or []) or "配置PCI设备已发现"
        return CheckItem(
            name=slot,
            category="PCI",
            status="正常",
            summary=summary,
            details=details,
            method="按配置文件检查指定PCI槽位",
            request=f"lspci/sysfs slot {slot}",
            risk_note="PCI被动读取，不改变设备状态",
        )

    def _test_configured_device(self, config: dict, phase: str) -> dict:
        connection_type = self._configured_connection_type(config)
        if connection_type == "serial":
            result = self._test_configured_serial(config, config)
        elif connection_type == "network":
            result = self._test_configured_network(config, config)
        elif connection_type == "pci":
            result = self._test_configured_pci(config)
        else:
            result = {"ok": False, "summary": "不支持的连接方式", "request": "", "response": "", "duration_ms": 0}
        return self._configured_device_result(config, phase, result)

    def _test_configured_child_device(self, child: dict, parent: dict, parent_result: dict) -> dict:
        if parent_result.get("status") not in ("在线", "正常"):
            result = {
                "ok": False,
                "summary": f"中间模块 {parent.get('device_name', '')} 通信失败，无法测试子设备",
                "request": "",
                "response": "",
                "duration_ms": 0,
            }
            if self._configured_connection_type(child, parent) == "network":
                result.update(self._network_public_profile(self._network_profile(child, parent)))
            return self._configured_device_result(child, "子设备测试", result, parent)
        connection_type = self._configured_connection_type(child, parent)
        carrier = dict(parent)
        if parent_result.get("interface") and self._configured_connection_type(parent) == "serial":
            carrier["interface"] = parent_result.get("interface")
        if connection_type == "serial":
            result = self._test_configured_serial(child, carrier)
        elif connection_type == "network":
            result = self._test_configured_network(child, carrier)
        elif connection_type == "pci":
            pci_target = child if (child.get("pci_slot") or child.get("pci_match")) else carrier
            result = self._test_configured_pci(pci_target)
        else:
            result = {"ok": False, "summary": "子设备缺少可用连接方式", "request": "", "response": "", "duration_ms": 0}
        return self._configured_device_result(child, "子设备测试", result, carrier)

    def _configured_device_result(self, config: dict, phase: str, test: dict, parent: Optional[dict] = None) -> dict:
        carrier = parent or config
        connection_type = self._configured_connection_type(config, parent)
        if connection_type == "network":
            net_cfg = config.get("network_config", {}) or carrier.get("network_config", {}) or {}
            iface = str(test.get("interface") or config.get("interface") or carrier.get("interface") or "")
            address = net_cfg.get("ip", "")
        elif connection_type == "pci":
            iface = str(test.get("interface") or config.get("pci_slot") or carrier.get("pci_slot") or "")
            address = iface
        else:
            carrier_is_serial = self._configured_connection_type(carrier) == "serial"
            iface = str(test.get("interface") or config.get("interface") or (carrier.get("interface") if carrier_is_serial else "") or "")
            address = iface
        ok = bool(test.get("ok"))
        status = "正常" if ok else "异常"
        device_id = str(config.get("device_id", ""))
        result = {
            "name": config.get("device_name", device_id),
            "device_id": device_id,
            "type": config.get("device_type", ""),
            "interface": iface,
            "address": address,
            "protocol": (config.get("protocol", {}) or {}).get("type", ""),
            "transport_protocol": test.get("transport_protocol", ""),
            "application_protocol": test.get("application_protocol", ""),
            "device_role": test.get("device_role", ""),
            "tester_role": test.get("tester_role", ""),
            "role_basis": test.get("role_basis", ""),
            "status": status,
            "test_result": "PASS 通过" if ok else "FAIL 失败",
            "test_phase": phase,
            "connection_type": connection_type,
            "evidence": test.get("summary", ""),
            "request": test.get("request", ""),
            "response": test.get("response", ""),
            "duration_ms": test.get("duration_ms", 0),
            "scanned_ports": test.get("scanned_ports", []),
            "matched": True,
            "standard_id": device_id,
            "standard_type": config.get("device_type", ""),
            "standard_model": config.get("device_name", ""),
            "standard_connection": connection_type,
            "standard_parameters": self._configured_parameter_text(config, parent),
            "standard_protocols": [(config.get("protocol", {}) or {}).get("type", "")],
            "is_intermediate_module": bool(config.get("is_intermediate_module")),
            "role": "middle_module" if config.get("is_intermediate_module") else "",
            "child_device_templates": config.get("child_devices", []) or [],
            "parent_id": parent.get("device_id", "") if parent else "",
            "parent_name": parent.get("device_name", "") if parent else "",
            "via_device": config.get("via_device", {}) or {},
            "test_scope": config.get("test_scope", "device"),
        }
        return result

    def _configured_parameter_text(self, config: dict, parent: Optional[dict] = None) -> str:
        carrier = parent or config
        connection_type = self._configured_connection_type(config, parent)
        if connection_type == "serial":
            serial_cfg = config.get("serial_config", {}) or carrier.get("serial_config", {}) or {}
            iface = config.get("interface") or carrier.get("interface") or "全串口扫描"
            return f"{iface}; {serial_cfg.get('baudrate', '')}, {serial_cfg.get('bytesize', '')}, {serial_cfg.get('parity', '')}, {serial_cfg.get('stopbits', '')}"
        if connection_type == "network":
            net_cfg = config.get("network_config", {}) or carrier.get("network_config", {}) or {}
            profile = self._network_profile(config, carrier)
            return (
                f"{config.get('interface') or carrier.get('interface') or '自动选择网口'}; "
                f"{net_cfg.get('ip', '')}:{net_cfg.get('port', '')}; "
                f"{profile['transport_protocol']}; 设备{profile['device_role']}"
            )
        if connection_type == "pci":
            target = config if (config.get("pci_slot") or config.get("pci_match")) else carrier
            provided = ",".join(target.get("provided_interfaces", []) or [])
            identity = str(target.get("pci_slot") or "")
            if not identity and target.get("pci_match"):
                identity = ", ".join(
                    f"{key}={value}"
                    for key, value in (target.get("pci_match", {}) or {}).items()
                    if value not in (None, "", [])
                )
            return "; ".join(part for part in (identity or "自动匹配PCI", provided) if part)
        return ""

    def _test_configured_serial(self, target: dict, carrier: dict) -> dict:
        port = str(target.get("interface") or carrier.get("interface") or "")
        protocol = target.get("protocol", {}) or {}
        serial_cfg = target.get("serial_config", {}) or carrier.get("serial_config", {}) or {}
        if not port:
            return self._test_configured_serial_all_ports(target, serial_cfg, protocol)
        if not os.path.exists(port):
            return {"ok": False, "summary": f"串口 {port} 不存在", "request": port, "response": "not found", "duration_ms": 0}
        return self._test_configured_serial_on_port(port, serial_cfg, protocol)

    def _test_configured_serial_all_ports(self, target: dict, serial_cfg: dict, protocol: dict) -> dict:
        ports = self._all_serial_scan_ports()
        device_name = str(target.get("device_name") or target.get("device_id") or "配置设备")
        if not ports:
            return {"ok": False, "summary": f"本机未发现可扫描串口，无法确认 {device_name}", "request": "枚举 /dev/tty*", "response": "no serial ports", "duration_ms": 0, "scanned_ports": []}
        self._log(f"{device_name}: 扫描全部串口 {','.join(ports)}")
        results = []
        for port in ports:
            result = self._test_configured_serial_on_port(port, serial_cfg, protocol)
            result["interface"] = port
            result["scanned_ports"] = ports
            results.append(result)
            if result.get("ok"):
                result["summary"] = f"在串口 {port} 识别到 {device_name}；{result.get('summary', '')}"
                return result
        abnormal = [item for item in results if item.get("response") and item.get("status") == "异常"]
        last = abnormal[-1] if abnormal else (results[-1] if results else {})
        return {
            "ok": False,
            "summary": f"已扫描 {len(ports)} 个串口，未识别到 {device_name}",
            "request": "全串口扫描: " + ",".join(ports),
            "response": last.get("response", "无响应"),
            "duration_ms": round(sum(float(item.get("duration_ms", 0) or 0) for item in results), 1),
            "scanned_ports": ports,
        }

    def _test_configured_serial_on_port(self, port: str, serial_cfg: dict, protocol: dict) -> dict:
        if str(protocol.get("type") or "custom").lower() == "modbus_rtu":
            result = self._test_modbus_rtu_configured(port, serial_cfg, protocol)
        else:
            result = self._test_custom_serial(port, serial_cfg, protocol)
        result["interface"] = port
        return result

    def _attach_devices_to_interface_items(self, serial_ports: List[CheckItem],
                                           network_interfaces: List[CheckItem],
                                           pci_devices: List[CheckItem],
                                           devices: List[dict]):
        by_key = {
            ("serial", item.name): item for item in serial_ports
        }
        by_key.update({("network", item.name): item for item in network_interfaces})
        by_key.update({("pci", item.name): item for item in pci_devices})
        for item in serial_ports:
            item.details["configured_devices"] = []
        for device in devices:
            connection_type = str(device.get("connection_type") or "")
            iface = str(device.get("interface") or "")
            if not iface:
                continue
            key_type = {
                "serial": "serial",
                "network": "network",
                "pci": "pci",
            }.get(connection_type)
            item = by_key.get((key_type, iface))
            if not item:
                continue
            label = str(device.get("name") or device.get("device_id") or "")
            parent = str(device.get("parent_name") or "")
            if parent:
                label = f"{label}(经{parent})"
            configured = item.details.setdefault("configured_devices", [])
            if label and label not in configured:
                configured.append(label)
        for item in serial_ports:
            count = len(item.details.get("configured_devices", []) or [])
            if item.status == "正常":
                item.summary = f"串口可打开，匹配配置设备 {count} 个" if count else "串口可打开，未匹配到配置设备"

    def _test_modbus_rtu_configured(self, port: str, serial_cfg: dict, protocol: dict) -> dict:
        start = time.time()
        baudrate = int(serial_cfg.get("baudrate", 9600) or 9600)
        bytesize = int(serial_cfg.get("bytesize", 8) or 8)
        parity = str(serial_cfg.get("parity", "N") or "N")
        stopbits = float(serial_cfg.get("stopbits", 1) or 1)
        timeout = float(serial_cfg.get("timeout", 2) or 2)
        frames = self._configured_modbus_rtu_frames(protocol)
        try:
            ser = serial.Serial(port=port, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits, timeout=timeout)
            try:
                matched_count = 0
                match_mode = str(protocol.get("match_mode") or "any").lower()
                for frame, label, probe in frames:
                    ser.reset_input_buffer()
                    ser.write(frame)
                    ser.flush()
                    delay_ms = max(0, min(2000, int(probe.get("response_delay_ms", 0) or 0)))
                    if delay_ms:
                        time.sleep(delay_ms / 1000.0)
                    response = ser.read(max(1, min(4096, int(probe.get("read_size", 256) or 256))))
                    duration_ms = round((time.time() - start) * 1000, 1)
                    response_ok, response_note, valid_frame = find_modbus_rtu_response(
                        response,
                        slave_id=frame[0],
                        function_code=frame[1],
                        accept_exception=bool(probe.get("accept_exception", protocol.get("accept_exception", False))),
                        expected_count=int.from_bytes(frame[4:6], "big"),
                    )
                    rule = response_rule(protocol, probe)
                    if response_ok and rule:
                        scope = str(probe.get("response_match_scope") or protocol.get("response_match_scope") or "data").lower()
                        match_data = valid_frame[3:-2] if scope == "data" and len(valid_frame) >= 5 else valid_frame
                        response_ok, rule_note = match_response(match_data, rule)
                        response_note = f"{response_note}；{rule_note}"
                    test = {
                        "target": port,
                        "type": "Modbus RTU配置数据只读测试",
                        "request": f"{label}; {baudrate},{bytesize},{parity},{stopbits}; {self._bytes_to_hex(frame)}",
                        "response": self._bytes_to_hex(response) if response else "超时",
                        "status": "有响应" if response_ok else "需确认",
                        "summary": f"{label} {response_note}",
                        "duration_ms": duration_ms,
                    }
                    self.connection_tests.append(test)
                    matched_count += int(response_ok)
                    if response_ok and match_mode != "all":
                        return {"ok": True, **test}
                    if not response_ok and match_mode == "all":
                        return {"ok": False, **test}
                if match_mode == "all" and matched_count == len(frames):
                    test["summary"] = f"全部 {matched_count} 项Modbus RTU只读规则通过"
                    return {"ok": True, **test}
                return {"ok": False, **test}
            finally:
                ser.close()
        except Exception as exc:
            duration_ms = round((time.time() - start) * 1000, 1)
            summary = f"无法打开串口 {port}" if isinstance(exc, (PermissionError, serial.SerialException)) else f"串口测试异常: {exc}"
            test = {
                "target": port,
                "type": "Modbus RTU配置数据只读测试",
                "request": f"{baudrate},{bytesize},{parity},{stopbits}",
                "response": str(exc),
                "status": "异常",
                "summary": summary,
                "duration_ms": duration_ms,
            }
            self.connection_tests.append(test)
            return {"ok": False, **test}

    def _configured_modbus_rtu_frames(self, protocol: dict) -> List[tuple]:
        slave_id = int(protocol.get("slave_id", 1) or 1)
        registers = protocol.get("probes", []) or protocol.get("test_registers", []) or [{"address": 0, "count": 1, "description": "默认只读寄存器"}]
        frames = []
        default_function = int(protocol.get("function_code", 3) or 3)
        for reg in registers:
            function_code = int(reg.get("function_code", default_function) or default_function)
            address = int(reg.get("address", 0) or 0)
            count = int(reg.get("count", 1) or 1)
            label = str(reg.get("name") or reg.get("description") or f"数据地址{address}")
            frames.append(
                (
                    build_modbus_rtu_read(slave_id, function_code, address, count),
                    f"{label}(功能码0x{function_code:02X})",
                    reg,
                )
            )
        return frames

    def _modbus_rtu_response_note(self, response: bytes, expected_slave: int,
                                  expected_function: int) -> tuple:
        ok, note, _ = find_modbus_rtu_response(response, expected_slave, expected_function)
        return ok, note

    def _test_custom_serial(self, port: str, serial_cfg: dict, protocol: dict) -> dict:
        start = time.time()
        baudrate = int(serial_cfg.get("baudrate", 9600) or 9600)
        bytesize = int(serial_cfg.get("bytesize", 8) or 8)
        parity = str(serial_cfg.get("parity", "N") or "N")
        stopbits = float(serial_cfg.get("stopbits", 1) or 1)
        timeout = float(serial_cfg.get("timeout", 2) or 2)
        last_request = ""
        try:
            ser = serial.Serial(port=port, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits, timeout=timeout)
            try:
                probes = protocol_probes(protocol)
                match_mode = str(protocol.get("match_mode") or "any").lower()
                matched_count = 0
                for index, probe in enumerate(probes, start=1):
                    operation = str(probe.get("operation") or protocol.get("operation") or "read").lower()
                    if operation == "write" and not self.config.allow_write_tests():
                        raise ProtocolConfigError("配置包含写操作，但settings.allow_write_tests未启用")
                    payload = request_payload(probe)
                    last_request = self._bytes_to_hex(payload) if payload else "被动接收"
                    if bool(probe.get("flush_input", True)):
                        ser.reset_input_buffer()
                    if payload:
                        ser.write(payload)
                        ser.flush()
                    delay_ms = max(0, min(2000, int(probe.get("response_delay_ms", 0) or 0)))
                    if delay_ms:
                        time.sleep(delay_ms / 1000.0)
                    response = ser.read(max(1, min(65536, int(probe.get("read_size", 256) or 256))))
                    rule = response_rule(protocol, probe)
                    response_ok, response_note = match_response(response, rule)
                    if not response and payload and bool(probe.get("success_on_send", False)):
                        response_ok, response_note = True, "报文发送完成，按配置无需响应"
                    duration_ms = round((time.time() - start) * 1000, 1)
                    label = str(probe.get("name") or probe.get("description") or f"探测规则{index}")
                    test = {
                        "target": port,
                        "type": "通用串口协议配置测试",
                        "request": f"{label}; {last_request}",
                        "response": self._bytes_to_hex(response) if response else "超时/无数据",
                        "status": "有响应" if response_ok else "需确认",
                        "summary": f"{label}：{response_note}",
                        "duration_ms": duration_ms,
                    }
                    self.connection_tests.append(test)
                    matched_count += int(response_ok)
                    if response_ok and match_mode != "all":
                        return {"ok": True, **test}
                    if not response_ok and match_mode == "all":
                        return {"ok": False, **test}
                if match_mode == "all" and matched_count == len(probes):
                    test["summary"] = f"全部 {matched_count} 项串口响应规则通过"
                    return {"ok": True, **test}
                return {"ok": False, **test}
            finally:
                ser.close()
        except Exception as exc:
            duration_ms = round((time.time() - start) * 1000, 1)
            test = {
                "target": port,
                "type": "通用串口协议配置测试",
                "request": last_request,
                "response": str(exc),
                "status": "异常",
                "summary": f"自定义串口测试异常: {exc}",
                "duration_ms": duration_ms,
            }
            self.connection_tests.append(test)
            return {"ok": False, **test}

    def _configured_command_bytes(self, command: str) -> bytes:
        return request_payload({"request_text": command})

    def _network_profile(self, target: dict, carrier: dict) -> dict:
        net_cfg = target.get("network_config", {}) or carrier.get("network_config", {}) or {}
        protocol = target.get("protocol", {}) or carrier.get("protocol", {}) or {}
        protocol_type = str(protocol.get("type", "") or "").lower()
        transport = str(
            net_cfg.get("transport")
            or net_cfg.get("transport_protocol")
            or net_cfg.get("protocol")
            or ""
        ).lower()
        if transport not in ("tcp", "udp"):
            transport = "udp" if "udp" in protocol_type else "tcp"
        role = self._network_role_code(str(net_cfg.get("device_role") or net_cfg.get("role") or ""))
        if not role:
            role = "server"
        app_protocol = str(protocol.get("type") or transport).replace("_", " ").upper()
        if protocol_type == "modbus_tcp":
            app_protocol = "Modbus TCP"
        elif protocol_type == "modbus_udp":
            app_protocol = "Modbus UDP"
        device_role = "服务端" if role == "server" else "客户端"
        tester_role = "客户端" if role == "server" else "服务端"
        basis = (
            "配置为设备服务端，本机主动连接目标IP和端口"
            if role == "server"
            else "配置为设备客户端，本机临时监听端口等待设备连接"
        )
        return {
            "transport_code": transport,
            "device_role_code": role,
            "transport_protocol": transport.upper(),
            "application_protocol": app_protocol,
            "device_role": device_role,
            "tester_role": tester_role,
            "role_basis": basis,
            "network_mode": f"{transport.upper()} / 设备{device_role}",
        }

    def _network_role_code(self, role: str) -> str:
        normalized = role.strip().lower()
        if normalized in ("server", "service", "device_server", "slave", "服务端"):
            return "server"
        if normalized in ("client", "device_client", "master", "客户端"):
            return "client"
        return ""

    def _network_public_profile(self, profile: dict) -> dict:
        return {
            key: value
            for key, value in profile.items()
            if key not in ("transport_code", "device_role_code")
        }

    def _local_ipv4_for_iface(self, iface: str) -> str:
        for addr in psutil.net_if_addrs().get(iface, []) or []:
            family_name = getattr(addr.family, "name", str(addr.family))
            if addr.family == socket.AF_INET or family_name == "AF_INET":
                return str(addr.address)
        return "0.0.0.0"

    def _interface_for_local_ip(self, local_ip: str) -> str:
        if not local_ip or local_ip == "0.0.0.0":
            return ""
        for name, addresses in psutil.net_if_addrs().items():
            for addr in addresses or []:
                family_name = getattr(addr.family, "name", str(addr.family))
                if (addr.family == socket.AF_INET or family_name == "AF_INET") and addr.address == local_ip:
                    return name
        return ""

    def _bind_network_socket(self, sock: socket.socket, iface: str, port: int = 0):
        if not iface:
            return
        local_ip = self._local_ipv4_for_iface(iface)
        if local_ip == "0.0.0.0":
            raise OSError(f"网口 {iface} 没有IPv4地址")
        sock.bind((local_ip, port))

    def _socket_interface(self, sock: socket.socket, configured_iface: str = "") -> str:
        try:
            local_ip = str(sock.getsockname()[0])
        except OSError:
            local_ip = ""
        return configured_iface or self._interface_for_local_ip(local_ip)

    def _protocol_has_exchange(self, protocol: dict) -> bool:
        keys = {
            "probes", "request_hex", "request_text", "request_base64",
            "test_command", "test_command_hex", "response_match",
            "expected_response_pattern", "expected_response_hex",
        }
        return any(protocol.get(key) not in (None, "", [], {}) for key in keys)

    def _match_incoming_protocol_payload(self, protocol: dict, payload: bytes) -> tuple:
        probes = protocol_probes(protocol)
        rules = [response_rule(protocol, probe) for probe in probes]
        rules = [rule for rule in rules if rule]
        if not rules:
            return True, "设备连接来源符合配置要求"
        matches = [match_response(payload, rule) for rule in rules]
        if str(protocol.get("match_mode") or "any").lower() == "all":
            failed = next((item for item in matches if not item[0]), None)
            return failed if failed else (True, f"全部 {len(matches)} 项响应规则通过")
        passed = next((item for item in matches if item[0]), None)
        return passed if passed else (False, "设备报文不符合配置响应规则")

    def _test_configured_network(self, target: dict, carrier: dict) -> dict:
        net_cfg = target.get("network_config", {}) or carrier.get("network_config", {}) or {}
        ip = str(net_cfg.get("ip", ""))
        port = int(net_cfg.get("port", 502) or 502)
        iface = str(target.get("interface") or carrier.get("interface") or "")
        profile = self._network_profile(target, carrier)
        if not iface and profile["device_role_code"] == "server":
            iface = self._route_interface_for_target(ip)
        if iface and iface not in psutil.net_if_addrs():
            return {
                "ok": False,
                "summary": f"网口 {iface} 不存在；配置通信形态 {profile['network_mode']}",
                "request": iface,
                "response": "not found",
                "duration_ms": 0,
                **self._network_public_profile(profile),
            }
        protocol = target.get("protocol", {}) or {}
        if profile["device_role_code"] == "client":
            local_port = int(net_cfg.get("local_port") or port or 502)
            timeout = float(net_cfg.get("listen_timeout", net_cfg.get("timeout", 5)) or 5)
            if profile["transport_code"] == "udp":
                return self._test_udp_client_configured(iface, ip, local_port, timeout, protocol, profile)
            return self._test_tcp_client_configured(iface, ip, local_port, timeout, protocol, profile)
        if profile["transport_code"] == "udp":
            return self._test_udp_server_configured(ip, port, iface, net_cfg, protocol, profile)
        if str(protocol.get("type") or "").lower() == "modbus_tcp":
            return self._test_modbus_tcp_configured(ip, port, iface, protocol, profile)
        if self._protocol_has_exchange(protocol):
            return self._test_tcp_protocol_configured(ip, port, iface, net_cfg, protocol, profile)
        return self._test_tcp_connect_configured(ip, port, iface, profile)

    def _test_tcp_connect_configured(self, ip: str, port: int, iface: str = "",
                                     profile: Optional[dict] = None) -> dict:
        profile = profile or self._network_profile({}, {"network_config": {"transport": "tcp", "device_role": "server"}})
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            self._bind_network_socket(sock, iface)
            code = sock.connect_ex((ip, port))
            duration_ms = round((time.time() - start) * 1000, 1)
            status = code == 0
            test = {
                "target": f"{ip}:{port}",
                "target_ip": ip,
                "port": port,
                "type": f"{profile['transport_protocol']}设备服务端连通测试",
                "request": f"本机{profile['tester_role']} -> 设备{profile['device_role']} TCP connect",
                "response": "connected" if status else f"connect_ex={code}",
                "status": "连通" if status else "异常",
                "summary": f"{profile['application_protocol']} {ip}:{port} 设备{profile['device_role']} {'可连接' if status else '不可连接'}",
                "duration_ms": duration_ms,
                "interface": self._socket_interface(sock, iface),
                **self._network_public_profile(profile),
            }
            self.connection_tests.append(test)
            return {"ok": status, **test}
        except OSError as exc:
            duration_ms = round((time.time() - start) * 1000, 1)
            test = {
                "target": f"{ip}:{port}",
                "target_ip": ip,
                "port": port,
                "type": f"{profile['transport_protocol']}设备服务端连通测试",
                "request": f"本机{profile['tester_role']} -> 设备{profile['device_role']} TCP connect",
                "response": str(exc),
                "status": "异常",
                "summary": f"{profile['application_protocol']} TCP连接异常: {exc}",
                "duration_ms": duration_ms,
                "interface": iface,
                **self._network_public_profile(profile),
            }
            self.connection_tests.append(test)
            return {"ok": False, **test}
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _test_tcp_protocol_configured(self, ip: str, port: int, iface: str,
                                      net_cfg: dict, protocol: dict, profile: dict) -> dict:
        start = time.time()
        probes = protocol_probes(protocol)
        match_mode = str(protocol.get("match_mode") or "any").lower()
        matched_count = 0
        last_test = {}
        for index, probe in enumerate(probes, start=1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            timeout = float(probe.get("timeout", net_cfg.get("timeout", 3)) or 3)
            sock.settimeout(timeout)
            label = str(probe.get("name") or probe.get("description") or f"探测规则{index}")
            try:
                operation = str(probe.get("operation") or protocol.get("operation") or "read").lower()
                if operation == "write" and not self.config.allow_write_tests():
                    raise ProtocolConfigError("配置包含写操作，但settings.allow_write_tests未启用")
                payload = request_payload(probe)
                rule = response_rule(protocol, probe)
                self._bind_network_socket(sock, iface)
                sock.connect((ip, port))
                if payload:
                    sock.sendall(payload)
                should_read = bool(rule) or bool(probe.get("read_response", bool(payload)))
                response = receive_response(
                    sock, timeout,
                    max_bytes=max(1, min(65536, int(probe.get("read_size", 4096) or 4096))),
                    complete=(lambda data: match_response(data, rule)[0]) if rule else None,
                ) if should_read else b""
                if should_read:
                    ok, note = match_response(response, rule)
                else:
                    ok, note = True, "TCP连接符合配置要求"
                if not response and payload and bool(probe.get("success_on_send", False)):
                    ok, note = True, "报文发送完成，按配置无需响应"
                duration_ms = round((time.time() - start) * 1000, 1)
                last_test = {
                    "target": f"{ip}:{port}",
                    "target_ip": ip,
                    "port": port,
                    "type": "TCP通用协议配置测试",
                    "request": f"{label}; {self._bytes_to_hex(payload) if payload else '仅连接/被动接收'}",
                    "response": self._bytes_to_hex(response) if response else ("无须响应" if not should_read else "超时/无数据"),
                    "status": "有响应" if ok else "需确认",
                    "summary": f"{profile['application_protocol']} {label}：{note}",
                    "duration_ms": duration_ms,
                    "interface": self._socket_interface(sock, iface),
                    **self._network_public_profile(profile),
                }
            except (OSError, ProtocolConfigError) as exc:
                duration_ms = round((time.time() - start) * 1000, 1)
                ok = False
                last_test = {
                    "target": f"{ip}:{port}",
                    "target_ip": ip,
                    "port": port,
                    "type": "TCP通用协议配置测试",
                    "request": label,
                    "response": str(exc),
                    "status": "异常",
                    "summary": f"{profile['application_protocol']} {label}异常: {exc}",
                    "duration_ms": duration_ms,
                    "interface": iface,
                    **self._network_public_profile(profile),
                }
            finally:
                sock.close()
            self.connection_tests.append(last_test)
            matched_count += int(ok)
            if ok and match_mode != "all":
                return {"ok": True, **last_test}
            if not ok and match_mode == "all":
                return {"ok": False, **last_test}
        if match_mode == "all" and matched_count == len(probes):
            last_test["summary"] = f"全部 {matched_count} 项TCP协议规则通过"
            return {"ok": True, **last_test}
        return {"ok": False, **last_test}

    def _test_modbus_tcp_configured(self, ip: str, port: int, iface: str,
                                    protocol: dict, profile: Optional[dict] = None) -> dict:
        profile = profile or self._network_profile({"protocol": protocol}, {"network_config": {"transport": "tcp", "device_role": "server"}})
        unit_id = int(protocol.get("unit_id", 1) or 1)
        registers = protocol.get("probes", []) or protocol.get("test_registers", []) or [{"address": 0, "count": 1, "description": "默认只读寄存器"}]
        start = time.time()
        match_mode = str(protocol.get("match_mode") or "any").lower()
        matched_count = 0
        for index, reg in enumerate(registers, start=1):
            address = int(reg.get("address", 0) or 0)
            count = int(reg.get("count", 1) or 1)
            function_code = int(reg.get("function_code", protocol.get("function_code", 3)) or 3)
            label = str(reg.get("name") or reg.get("description") or f"数据地址{address}")
            frame = build_modbus_tcp_read(index, unit_id, function_code, address, count)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(float(reg.get("timeout", protocol.get("timeout", 3)) or 3))
            try:
                self._bind_network_socket(sock, iface)
                sock.connect((ip, port))
                sock.sendall(frame)
                response = receive_response(sock, float(reg.get("timeout", protocol.get("timeout", 3)) or 3),
                                            max_bytes=260, complete=modbus_tcp_complete)
                response_ok, response_note, valid_frame = validate_modbus_tcp_response(
                    response,
                    transaction_id=index,
                    unit_id=unit_id,
                    function_code=function_code,
                    accept_exception=bool(reg.get("accept_exception", protocol.get("accept_exception", False))),
                    expected_count=count,
                )
                rule = response_rule(protocol, reg)
                if response_ok and rule:
                    scope = str(reg.get("response_match_scope") or protocol.get("response_match_scope") or "data").lower()
                    match_data = valid_frame[9:] if scope == "data" and len(valid_frame) >= 9 else valid_frame
                    response_ok, rule_note = match_response(match_data, rule)
                    response_note = f"{response_note}；{rule_note}"
                duration_ms = round((time.time() - start) * 1000, 1)
                test = {
                    "target": f"{ip}:{port}",
                    "target_ip": ip,
                    "port": port,
                    "type": "Modbus TCP配置寄存器只读测试",
                    "request": f"{label}; 本机{profile['tester_role']} -> 设备{profile['device_role']}; {self._bytes_to_hex(frame)}",
                    "response": self._bytes_to_hex(response) if response else "超时",
                    "status": "有响应" if response_ok else "需确认",
                    "summary": f"{profile['application_protocol']} {label}：{response_note}",
                    "duration_ms": duration_ms,
                    "interface": self._socket_interface(sock, iface),
                    **self._network_public_profile(profile),
                }
                self.connection_tests.append(test)
                matched_count += int(response_ok)
                if response_ok and match_mode != "all":
                    return {"ok": True, **test}
                if not response_ok and match_mode == "all":
                    return {"ok": False, **test}
            except (OSError, ProtocolConfigError) as exc:
                duration_ms = round((time.time() - start) * 1000, 1)
                test = {
                    "target": f"{ip}:{port}",
                    "target_ip": ip,
                    "port": port,
                    "type": "Modbus TCP配置寄存器只读测试",
                    "request": f"本机{profile['tester_role']} -> 设备{profile['device_role']}; {self._bytes_to_hex(frame)}",
                    "response": str(exc),
                    "status": "异常",
                    "summary": f"Modbus TCP异常: {exc}",
                    "duration_ms": duration_ms,
                    "interface": iface,
                    **self._network_public_profile(profile),
                }
                self.connection_tests.append(test)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
        if match_mode == "all" and matched_count == len(registers):
            test["summary"] = f"全部 {matched_count} 项Modbus TCP只读规则通过"
            return {"ok": True, **test}
        return {"ok": False, **test}

    def _test_udp_server_configured(self, ip: str, port: int, iface: str,
                                    net_cfg: dict, protocol: dict, profile: dict) -> dict:
        start = time.time()
        probes = protocol_probes(protocol) if self._protocol_has_exchange(protocol) else [dict(net_cfg)]
        match_mode = str(protocol.get("match_mode") or "any").lower()
        matched_count = 0
        last_test = {}
        for index, probe in enumerate(probes, start=1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            timeout = float(probe.get("timeout", net_cfg.get("timeout", 3)) or 3)
            sock.settimeout(timeout)
            label = str(probe.get("name") or probe.get("description") or f"探测规则{index}")
            try:
                operation = str(probe.get("operation") or protocol.get("operation") or "read").lower()
                if operation == "write" and not self.config.allow_write_tests():
                    raise ProtocolConfigError("配置包含写操作，但settings.allow_write_tests未启用")
                payload = request_payload(probe)
                if not payload:
                    raise ProtocolConfigError("UDP设备服务端探测必须配置request_hex或request_text")
                self._bind_network_socket(sock, iface)
                sock.sendto(payload, (ip, port))
                response, source = sock.recvfrom(512)
                source_ok = not ip or source[0] == ip
                status, note = match_response(response, response_rule(protocol, probe))
                status = bool(status and source_ok)
                if not source_ok:
                    note = f"响应来源{source[0]}与配置目标{ip}不一致"
                response_text = f"{self._bytes_to_hex(response)} from {source[0]}:{source[1]}"
            except socket.timeout:
                status = False
                note = "未收到UDP响应"
                response_text = "超时"
            except (OSError, ProtocolConfigError) as exc:
                status = False
                note = f"UDP测试异常: {exc}"
                response_text = str(exc)
                payload = b""
            duration_ms = round((time.time() - start) * 1000, 1)
            last_test = {
                "target": f"{ip}:{port}",
                "target_ip": ip,
                "port": port,
                "type": "UDP通用协议配置测试",
                "request": f"{label}; {self._bytes_to_hex(payload) if payload else '未发送'}",
                "response": response_text,
                "status": "有响应" if status else "需确认",
                "summary": f"{profile['application_protocol']} {label}：{note}",
                "duration_ms": duration_ms,
                "interface": self._socket_interface(sock, iface),
                **self._network_public_profile(profile),
            }
            self.connection_tests.append(last_test)
            matched_count += int(status)
            try:
                sock.close()
            except OSError:
                pass
            if status and match_mode != "all":
                return {"ok": True, **last_test}
            if not status and match_mode == "all":
                return {"ok": False, **last_test}
        if match_mode == "all" and matched_count == len(probes):
            last_test["summary"] = f"全部 {matched_count} 项UDP协议规则通过"
            return {"ok": True, **last_test}
        return {"ok": False, **last_test}

    def _test_tcp_client_configured(self, iface: str, expected_ip: str, local_port: int,
                                    timeout: float, protocol: dict, profile: dict) -> dict:
        start = time.time()
        bind_ip = self._local_ipv4_for_iface(iface)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.settimeout(timeout)
        actual_iface = iface
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((bind_ip, local_port))
            server.listen(1)
            try:
                conn, source = server.accept()
                try:
                    actual_iface = iface or self._interface_for_local_ip(str(conn.getsockname()[0]))
                    source_ok = not expected_ip or source[0] == expected_ip
                    conn.settimeout(timeout)
                    incoming = b""
                    if self._protocol_has_exchange(protocol):
                        try:
                            incoming = conn.recv(65536)
                        except socket.timeout:
                            incoming = b""
                    payload_ok, payload_note = self._match_incoming_protocol_payload(protocol, incoming)
                    matched = bool(source_ok and payload_ok)
                    response = f"accepted {source[0]}:{source[1]}"
                    if incoming:
                        response += f"; {self._bytes_to_hex(incoming)}"
                    status_text = "有响应" if matched else "需确认"
                    if not source_ok:
                        summary = f"收到非目标地址连接: {source[0]}"
                    else:
                        summary = payload_note
                    first_probe = protocol_probes(protocol)[0]
                    reply_mapping = {}
                    if first_probe.get("reply_hex") not in (None, ""):
                        reply_mapping["request_hex"] = first_probe.get("reply_hex")
                    elif first_probe.get("reply_text") not in (None, ""):
                        reply_mapping["request_text"] = first_probe.get("reply_text")
                    if reply_mapping:
                        conn.sendall(request_payload(reply_mapping))
                finally:
                    conn.close()
            except socket.timeout:
                matched = False
                response = "监听超时"
                status_text = "需确认"
                summary = "未收到设备客户端连接"
            duration_ms = round((time.time() - start) * 1000, 1)
            test = {
                "target": f"{bind_ip}:{local_port}",
                "target_ip": expected_ip,
                "port": local_port,
                "type": "TCP设备客户端接入测试",
                "request": f"本机{profile['tester_role']}监听 {bind_ip}:{local_port}，等待设备{profile['device_role']}连接",
                "response": response,
                "status": status_text,
                "summary": summary,
                "duration_ms": duration_ms,
                "interface": actual_iface,
                **self._network_public_profile(profile),
            }
            self.connection_tests.append(test)
            return {"ok": matched, **test}
        except (OSError, ProtocolConfigError) as exc:
            duration_ms = round((time.time() - start) * 1000, 1)
            test = {
                "target": f"{bind_ip}:{local_port}",
                "target_ip": expected_ip,
                "port": local_port,
                "type": "TCP设备客户端接入测试",
                "request": f"本机{profile['tester_role']}监听 {bind_ip}:{local_port}",
                "response": str(exc),
                "status": "异常",
                "summary": f"TCP监听异常: {exc}",
                "duration_ms": duration_ms,
                "interface": iface,
                **self._network_public_profile(profile),
            }
            self.connection_tests.append(test)
            return {"ok": False, **test}
        finally:
            try:
                server.close()
            except OSError:
                pass

    def _test_udp_client_configured(self, iface: str, expected_ip: str, local_port: int,
                                    timeout: float, protocol: dict, profile: dict) -> dict:
        start = time.time()
        bind_ip = self._local_ipv4_for_iface(iface)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        actual_iface = iface or self._route_interface_for_target(expected_ip)
        try:
            sock.bind((bind_ip, local_port))
            try:
                payload, source = sock.recvfrom(512)
                source_ok = not expected_ip or source[0] == expected_ip
                payload_ok, payload_note = self._match_incoming_protocol_payload(protocol, payload)
                matched = bool(source_ok and payload_ok)
                response = f"{self._bytes_to_hex(payload)} from {source[0]}:{source[1]}"
                status_text = "有响应" if matched else "需确认"
                summary = payload_note if source_ok else f"收到非目标地址UDP报文: {source[0]}"
            except socket.timeout:
                matched = False
                response = "监听超时"
                status_text = "需确认"
                summary = "未收到设备客户端UDP报文"
            duration_ms = round((time.time() - start) * 1000, 1)
            test = {
                "target": f"{bind_ip}:{local_port}",
                "target_ip": expected_ip,
                "port": local_port,
                "type": "UDP设备客户端报文测试",
                "request": f"本机{profile['tester_role']}监听 {bind_ip}:{local_port}，等待设备{profile['device_role']}发送UDP",
                "response": response,
                "status": status_text,
                "summary": summary,
                "duration_ms": duration_ms,
                "interface": actual_iface,
                **self._network_public_profile(profile),
            }
            self.connection_tests.append(test)
            return {"ok": matched, **test}
        except (OSError, ProtocolConfigError) as exc:
            duration_ms = round((time.time() - start) * 1000, 1)
            test = {
                "target": f"{bind_ip}:{local_port}",
                "target_ip": expected_ip,
                "port": local_port,
                "type": "UDP设备客户端报文测试",
                "request": f"本机{profile['tester_role']}监听 {bind_ip}:{local_port}",
                "response": str(exc),
                "status": "异常",
                "summary": f"UDP监听异常: {exc}",
                "duration_ms": duration_ms,
                "interface": iface,
                **self._network_public_profile(profile),
            }
            self.connection_tests.append(test)
            return {"ok": False, **test}
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _udp_payload(self, net_cfg: dict) -> bytes:
        payload_hex = str(net_cfg.get("test_payload_hex") or "").strip()
        if payload_hex:
            try:
                return bytes.fromhex(payload_hex)
            except ValueError:
                pass
        payload = str(net_cfg.get("test_payload") or "PING")
        return payload.encode("utf-8")

    def _test_configured_pci(self, config: dict) -> dict:
        configured_slot = str(config.get("pci_slot", ""))
        matches = self._matching_pci_devices(config)
        required_count = max(1, int(config.get("required_count", 1) or 1))
        if len(matches) < required_count:
            target = configured_slot or json.dumps(config.get("pci_match", {}), ensure_ascii=False)
            return {
                "ok": False,
                "summary": f"符合配置规则的PCI设备 {len(matches)} 个，少于要求的 {required_count} 个",
                "request": f"lspci/sysfs match {target}",
                "response": "not found",
                "duration_ms": 0,
            }

        checked = []
        failures = []
        for dev in matches:
            checks = self._pci_passive_checks(dev)
            requirement_failures = self._pci_requirement_failures(config, checks)
            checked.append({**dev, **checks})
            if requirement_failures:
                failures.append(f"{dev.get('slot')}: {','.join(requirement_failures)}")
        selected = checked[0]
        slots = [str(item.get("slot") or "") for item in checked]
        detail = f"匹配PCI设备 {len(checked)} 个: {','.join(slots)}"
        passive = selected.get("passive_checks", []) or []
        if passive:
            detail += "；" + "；".join(passive)
        if failures:
            detail += "；要求未满足: " + "；".join(failures)
        return {
            "ok": not failures,
            "summary": detail,
            "request": "lspci/sysfs + 配置标识与状态要求匹配",
            "response": json.dumps(checked, ensure_ascii=False),
            "duration_ms": 0,
            "interface": str(selected.get("slot") or ""),
            "matched_slots": slots,
        }

    def _all_pci_devices(self) -> List[dict]:
        if self._pci_scan_cache is None:
            self._pci_scan_cache = self._lspci_devices() or self._sysfs_pci_devices()
        return self._pci_scan_cache

    def _matching_pci_devices(self, config: dict) -> List[dict]:
        slot = str(config.get("pci_slot") or "")
        if slot:
            dev = self._find_pci_by_slot(slot)
            if not dev:
                return []
            checks = self._pci_passive_checks(dev)
            return [dev] if self._pci_matches_config(config, dev, checks) else []
        matches = []
        for dev in self._all_pci_devices():
            if self._pci_matches_config(config, dev, {}):
                matches.append(dev)
        return matches

    def _pci_matches_config(self, config: dict, dev: dict, checks: dict) -> bool:
        match = config.get("pci_match", {}) or {}
        if match:
            exact_fields = {
                "vendor_id": dev.get("vendor_id") or dev.get("vendor"),
                "device_id": dev.get("device_id") or dev.get("device"),
                "subsystem_vendor_id": dev.get("subsystem_vendor_id"),
                "subsystem_device_id": dev.get("subsystem_device_id"),
            }
            for key, actual in exact_fields.items():
                expected = match.get(key)
                if expected not in (None, "") and self._normalize_pci_id(actual) != self._normalize_pci_id(expected):
                    return False
            expected_class = self._normalize_pci_id(match.get("class_code"))
            actual_class = self._normalize_pci_id(dev.get("class_code"))
            if expected_class and not actual_class.startswith(expected_class):
                return False
            expected_driver = match.get("driver")
            if expected_driver not in (None, "", []):
                allowed = expected_driver if isinstance(expected_driver, list) else [expected_driver]
                if str(checks.get("driver") or dev.get("driver") or "").lower() not in {str(item).lower() for item in allowed}:
                    return False
            text = " ".join(str(value).lower() for value in {**dev, **checks}.values())
            any_keywords = match.get("keywords_any", []) or []
            all_keywords = match.get("keywords_all", []) or []
            if any_keywords and not any(str(keyword).lower() in text for keyword in any_keywords):
                return False
            if all_keywords and not all(str(keyword).lower() in text for keyword in all_keywords):
                return False
            return True

        device_type = str(config.get("device_type") or "").lower()
        class_code = str(dev.get("class_code", "")).lower().replace("0x", "")
        text = " ".join(str(value).lower() for value in {**dev, **checks}.values())
        if "serial" in device_type or "pci_serial" in device_type:
            if class_code.startswith("07"):
                return True
            if checks.get("tty_nodes"):
                return True
            return any(keyword in text for keyword in ("serial", "rs232", "rs485", "uart"))
        if any(keyword in device_type for keyword in ("ethernet", "network", "nic")):
            return class_code.startswith("02") or bool(checks.get("net_interfaces"))
        if "can" in device_type:
            return "can" in text
        return True

    def _pci_requirement_failures(self, config: dict, checks: dict) -> List[str]:
        requirements = config.get("pci_requirements", {}) or config.get("requirements", {}) or {}
        failures = []
        if bool(requirements.get("driver_bound", False)) and not checks.get("driver"):
            failures.append("未绑定驱动")
        if bool(requirements.get("enabled", False)) and checks.get("enabled") != "1":
            failures.append("设备未启用")
        net_min = int(requirements.get("net_interfaces_min", 0) or 0)
        tty_min = int(requirements.get("tty_nodes_min", 0) or 0)
        if len(checks.get("net_interfaces", []) or []) < net_min:
            failures.append(f"网口节点少于{net_min}")
        if len(checks.get("tty_nodes", []) or []) < tty_min:
            failures.append(f"串口节点少于{tty_min}")
        missing = [
            iface for iface in config.get("provided_interfaces", []) or []
            if not os.path.exists(iface)
        ]
        if missing:
            failures.append(f"缺少子接口{','.join(missing)}")
        return failures

    def _normalize_pci_id(self, value: object) -> str:
        text = str(value or "").lower().replace("0x", "")
        matches = re.findall(r"[0-9a-f]+", text)
        return matches[-1] if matches else ""

    def _find_pci_by_slot(self, slot: str) -> Optional[dict]:
        if not slot:
            return None
        wanted = {slot, slot.replace("0000:", "")}
        if not slot.startswith("0000:"):
            wanted.add(f"0000:{slot}")
        for dev in self._all_pci_devices():
            dev_slot = str(dev.get("slot", ""))
            if dev_slot in wanted or dev_slot.replace("0000:", "") in wanted:
                return dev
        return None

    def _configured_direct_summary(self, devices: List[dict]) -> str:
        direct = [item for item in devices if item.get("test_phase") in ("直连设备测试", "中间模块测试")]
        names = [f"{item.get('name')}({item.get('test_result')})" for item in direct]
        return "直连/中间模块测试：" + "，".join(names) if names else "未配置直连设备"

    def _configured_child_summary(self, devices: List[dict]) -> str:
        children = [item for item in devices if item.get("test_phase") == "子设备测试"]
        if not children:
            return "未配置子设备"
        names = [f"{item.get('name')}({item.get('test_result')})" for item in children]
        return "子设备测试：" + "，".join(names)

    def _configured_protocol_summary(self) -> str:
        passed = sum(1 for item in self.connection_tests if item.get("status") in ("连通", "有响应"))
        no_response = sum(1 for item in self.connection_tests if item.get("status") == "需确认")
        failed = sum(1 for item in self.connection_tests if item.get("status") == "异常")
        return f"通信协议交互验证完成：通过 {passed} 条，无响应 {no_response} 条，异常 {failed} 条"

    def _interface_summary(self, serial_ports: List[CheckItem],
                           network_interfaces: List[CheckItem],
                           pci_devices: List[CheckItem]) -> str:
        parts = []
        if serial_ports:
            parts.append("串口 " + ",".join(item.name for item in serial_ports[:4]))
        if network_interfaces:
            parts.append("网口 " + ",".join(item.name for item in network_interfaces[:4]))
        if pci_devices:
            parts.append("PCI " + ",".join(item.name for item in pci_devices[:4]))
        return "发现" + "，".join(parts) if parts else "未发现可用接口"

    def _direct_device_summary(self, devices: List[dict]) -> str:
        direct = [item for item in devices if item.get("type") != "PCI通信硬件"]
        if not direct:
            return "未识别到直连设备"
        names = [self._display_device_name(item) for item in direct[:5]]
        return "识别到直连设备：" + "，".join(names)

    def _middle_module_summary(self, devices: List[dict]) -> str:
        modules = [
            item for item in devices
            if item.get("is_intermediate_module")
            or item.get("role") == "middle_module"
            or item.get("standard_type") == "IO模块"
        ]
        if not modules:
            return "未发现中间模块"
        names = [self._display_device_name(item) for item in modules[:5]]
        return "识别到中间模块：" + "，".join(names)

    def _child_search_summary(self, devices: List[dict]) -> str:
        modules = [
            item for item in devices
            if item.get("is_intermediate_module")
            or item.get("role") == "middle_module"
        ]
        if not modules:
            return "中间模块二次设备搜索：无可用中间模块，跳过子设备测试"
        child_count = sum(len(item.get("child_device_templates", []) or []) for item in modules)
        if child_count:
            return f"中间模块二次设备搜索：已加载 {child_count} 个子设备模板，等待模块协议响应后按模板验证"
        return "中间模块二次设备搜索：发现中间模块，但标准列表未配置子设备模板"

    def _protocol_summary(self) -> str:
        if any("Modbus RTU" in item.get("type", "") and item.get("status") == "有响应" for item in self.connection_tests):
            return "Modbus RTU 通信测试通过 (功能码 0x03)"
        if any("Modbus TCP" in item.get("type", "") and item.get("status") == "有响应" for item in self.connection_tests):
            return "Modbus TCP 通信测试通过 (功能码 0x2B/0x0E)"
        industrial_tests = [item for item in self.connection_tests if self._is_industrial_link_test(item)]
        passed = sum(1 for item in industrial_tests if item.get("status") in ("连通", "有响应"))
        return f"通信链路验证完成：工业协议链路通过 {passed} 条"

    def _response_decision_summary(self, passed: int, no_response: int, failed: int) -> str:
        if not self.connection_tests:
            return "设备响应判断：未产生主动链路探测项，依据接口枚举和 PCI 被动读取输出结果"
        if no_response or failed:
            return f"设备响应判断：{passed} 条有响应/连通，{no_response} 条无响应，{failed} 条异常；未通过设备已在配置设备结果中标记"
        return f"设备响应判断：{passed} 条有响应/连通，未发现无响应或异常项"

    def _display_device_name(self, item: dict) -> str:
        if item.get("standard_type") and item.get("standard_model"):
            return f"{item['standard_type']} {item['standard_model']}"
        return str(item.get("name", "未知设备"))

    def _discover_devices(
        self,
        serial_ports: List[CheckItem],
        network_interfaces: List[CheckItem],
        pci_devices: List[CheckItem],
    ) -> List[dict]:
        devices: List[dict] = []
        network_devices: Dict[str, dict] = {}

        for item in serial_ports:
            probe = item.details.get("link_probe", {})
            if probe.get("status") == "有响应":
                devices.append({
                    "name": f"Modbus RTU @ {item.name}",
                    "type": "Modbus RTU设备",
                    "interface": item.name,
                    "address": item.name,
                    "protocol": "Modbus RTU",
                    "status": "在线",
                    "evidence": probe.get("summary", ""),
                    "request": probe.get("request", ""),
                    "response": probe.get("response", ""),
                })

        for item in network_interfaces:
            details = item.details or {}
            iface_name = item.name
            for test in details.get("link_tests", []) or []:
                if not self._is_industrial_link_test(test):
                    continue
                ip = test.get("target_ip") or str(test.get("target", "")).split(":", 1)[0]
                if not ip:
                    continue
                key = f"{iface_name}:{ip}"
                entry = network_devices.setdefault(key, {
                    "name": ip,
                    "type": "网络设备",
                    "interface": iface_name,
                    "address": ip,
                    "protocol": "",
                    "status": "已发现",
                    "evidence": "",
                    "ports": [],
                })
                port = test.get("port")
                if port and port not in entry["ports"]:
                    entry["ports"].append(port)
                protocol = self._protocol_name(port)
                if test.get("status") == "有响应" and port == 502:
                    entry["type"] = "Modbus TCP设备"
                    entry["protocol"] = "Modbus TCP"
                    entry["status"] = "在线"
                elif test.get("status") == "连通" and port in INDUSTRIAL_TCP_PORTS:
                    entry["type"] = "TCP服务设备"
                    entry["protocol"] = protocol
                    entry["status"] = "在线"
                entry["evidence"] = test.get("summary", "") or entry.get("evidence", "")
                entry["request"] = test.get("request", "")
                entry["response"] = test.get("response", "")

        for entry in network_devices.values():
            ports = entry.get("ports") or []
            if ports:
                entry["evidence"] = f"端口 {','.join(str(port) for port in sorted(ports))}；{entry.get('evidence', '')}".strip("；")
            devices.append(entry)

        for item in pci_devices:
            details = item.details or {}
            device_name = details.get("description") or details.get("device") or item.name
            evidence = item.summary
            devices.append({
                "name": device_name,
                "type": "PCI通信硬件",
                "interface": item.name,
                "address": item.name,
                "protocol": details.get("driver") or details.get("class") or "PCI",
                "status": "正常" if item.status == "正常" else item.status,
                "evidence": evidence,
                "request": "lspci/sysfs",
                "response": "; ".join(details.get("passive_checks", []) or []),
            })

        for device in devices:
            self._apply_standard_match(device)
        devices.sort(key=lambda item: (item.get("type", ""), item.get("interface", ""), item.get("address", "")))
        return devices

    def _is_industrial_link_test(self, test: dict) -> bool:
        port = test.get("port")
        if port in INDUSTRIAL_TCP_PORTS:
            return True
        test_type = str(test.get("type", ""))
        return "Modbus" in test_type or "S7" in test_type or "OPC" in test_type or "EtherNet/IP" in test_type

    def _apply_standard_match(self, device: dict):
        best = None
        best_score = 0
        for standard in self.standard_devices:
            score = self._standard_match_score(device, standard)
            if score > best_score:
                best = standard
                best_score = score
        if best and best_score > 0:
            device["matched"] = True
            device["standard_type"] = best.get("device_type", "")
            device["standard_model"] = best.get("model", "")
            device["standard_connection"] = best.get("connection", "")
            device["standard_parameters"] = best.get("parameters", "")
            device["standard_protocols"] = best.get("protocols", [])
            device["standard_id"] = best.get("device_id", "")
            device["connection_type"] = best.get("connection_type", "")
            device["is_intermediate_module"] = bool(best.get("is_intermediate_module") or best.get("role") == "middle_module")
            device["role"] = best.get("role", "")
            device["child_device_templates"] = best.get("child_devices", []) or []
            device["protocol_probe"] = best.get("probe", {}) or {}
            device["test_phase"] = "中间模块测试" if device["is_intermediate_module"] else "直连设备测试"
            device["match_score"] = best_score
        else:
            device["matched"] = False
            device["is_intermediate_module"] = False
            device["child_device_templates"] = []
            device["test_phase"] = "设备搜索"
            device["match_score"] = 0

    def _standard_match_score(self, device: dict, standard: dict) -> int:
        match = standard.get("match", {}) or {}
        score = 0
        device_text = " ".join(str(device.get(key, "")).lower() for key in (
            "name", "type", "interface", "protocol", "evidence"
        ))
        protocols = [str(item).lower() for item in standard.get("protocols", [])]
        for protocol in protocols + [str(item).lower() for item in match.get("protocols", [])]:
            if protocol and protocol in device_text:
                score += 4
        device_ports = set(device.get("ports", []) or [])
        for port in match.get("ports", []) or []:
            if port in device_ports:
                score += 3
        for keyword in match.get("keywords", []) or []:
            keyword_text = str(keyword).lower()
            if len(keyword_text) <= 2:
                continue
            if keyword_text in device_text:
                score += 2
        for interface in match.get("interface", []) or []:
            if str(interface).lower() in device_text:
                score += 1
        return score

    def _host_evidence(self, host: dict) -> str:
        parts = []
        if host.get("source"):
            parts.append(str(host["source"]))
        if host.get("mac"):
            parts.append(f"MAC {host['mac']}")
        if host.get("state"):
            parts.append(str(host["state"]))
        return " ".join(parts)

    def _protocol_name(self, port: Optional[int]) -> str:
        names = {
            502: "Modbus TCP",
            102: "S7/ISO-on-TCP",
            44818: "EtherNet/IP",
            4840: "OPC UA",
            80: "HTTP",
        }
        return names.get(port, f"TCP {port}" if port else "TCP")

    def _scan_serial_ports(self) -> List[CheckItem]:
        self._log("扫描串口：pyserial枚举 + USB/ACM补充 + PCI串口节点映射")
        items: List[CheckItem] = []
        seen = set()

        for port in serial.tools.list_ports.comports():
            device = port.device
            if not device or device in seen:
                continue
            if not self._is_meaningful_serial_port(device, port.description, port.hwid):
                continue
            seen.add(device)
            items.append(self._check_serial_port(device, port.description, port.hwid))

        for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
            for device in sorted(glob.glob(pattern)):
                if device not in seen:
                    seen.add(device)
                    items.append(self._check_serial_port(device, "USB/ACM串口", ""))

        for device in self._pci_tty_nodes():
            if device not in seen:
                seen.add(device)
                items.append(self._check_serial_port(device, "PCI串口节点", ""))

        self._log(f"串口检测完成：展示 {len(items)} 个串口节点")
        return items

    def _is_meaningful_serial_port(self, device: str, description: str, hwid: str) -> bool:
        if os.name == "nt":
            return True
        name = os.path.basename(device)
        text = f"{device} {description or ''} {hwid or ''}".lower()
        if name.startswith(("ttyUSB", "ttyACM")):
            return True
        if any(marker in text for marker in ("usb", "vid:pid", "rs485", "rs232", "ftdi")):
            return True

        sys_info = self._serial_sysfs_info(device)
        realpath = str(sys_info.get("realpath", "")).lower()
        driver = str(sys_info.get("driver", "")).lower()
        if not realpath:
            return False
        if "serial8250" in realpath and not driver:
            return False
        return any(marker in realpath for marker in ("/pci", "/usb", "/pnp")) or bool(driver)

    def _serial_sysfs_info(self, device: str) -> dict:
        name = os.path.basename(device)
        sys_device = f"/sys/class/tty/{name}/device"
        if not os.path.exists(sys_device):
            return {}
        realpath = os.path.realpath(sys_device)
        driver = ""
        driver_link = os.path.join(sys_device, "driver")
        if os.path.islink(driver_link):
            driver = os.path.basename(os.path.realpath(driver_link))
        transport = "系统串口"
        low = realpath.lower()
        if "/usb" in low:
            transport = "USB串口"
        elif "/pci" in low:
            transport = "PCI串口"
        elif "/pnp" in low:
            transport = "板载串口"
        return {
            "sysfs": sys_device,
            "realpath": realpath,
            "driver": driver,
            "transport": transport,
        }

    def _check_serial_port(self, device: str, description: str, hwid: str) -> CheckItem:
        sys_info = self._serial_sysfs_info(device)
        details = {
            "description": description or "未知串口",
            "hwid": hwid or "",
            "access": "未测试",
            "transport": sys_info.get("transport", ""),
            "driver": sys_info.get("driver", ""),
        }
        status = "需确认"
        summary = "串口节点存在，尚未确认可打开"
        try:
            ser = serial.Serial(device, baudrate=9600, timeout=0.2)
            ser.close()
            status = "正常"
            details["access"] = "可打开"
            summary = "串口可打开，基础访问正常"
            if self.serial_probe:
                probe = self._probe_modbus_rtu(device)
                details["link_probe"] = probe
                self.connection_tests.append(probe)
                if probe["status"] == "有响应":
                    summary += "，Modbus RTU只读探测有响应"
                else:
                    summary += "，默认Modbus RTU只读探测未收到响应"
        except PermissionError as exc:
            status = "异常"
            details["access"] = f"权限不足: {exc}"
            summary = "串口权限不足，无法打开"
        except serial.SerialException as exc:
            status = "异常"
            details["access"] = f"打开失败: {exc}"
            summary = "串口打开失败，可能被占用或权限不足"

        return CheckItem(
            name=device,
            category="串口",
            status=status,
            summary=summary,
            details=details,
            method="串口节点枚举 + open/close访问测试 + Modbus RTU只读探测",
            request="先打开/关闭串口；随后按标准列表的Modbus RTU只读寄存器模板发送0x03读请求",
            risk_note="只使用0x03读保持寄存器探测，不发送写线圈或写寄存器命令",
        )

    def _probe_modbus_rtu(self, device: str) -> dict:
        last_error = ""
        frames = self._modbus_rtu_probe_frames()
        profiles = [(9600, "N"), (9600, "E"), (19200, "N"), (19200, "E"), (115200, "N")]
        for baudrate, parity in profiles:
            for frame, label in frames:
                start = time.time()
                ser = None
                try:
                    ser = serial.Serial(device, baudrate=baudrate, parity=parity, timeout=0.35)
                    ser.reset_input_buffer()
                    ser.write(frame)
                    ser.flush()
                    response = ser.read(256)
                    duration_ms = round((time.time() - start) * 1000, 1)
                    if response:
                        return {
                            "target": device,
                            "type": "Modbus RTU标准参数只读验证",
                            "request": f"{baudrate},8,{parity},1; {self._bytes_to_hex(frame)}",
                            "response": self._bytes_to_hex(response),
                            "status": "有响应",
                            "summary": f"{baudrate}bps/{parity}校验收到响应：{label}",
                            "duration_ms": duration_ms,
                        }
                except Exception as exc:
                    last_error = str(exc)
                    break
                finally:
                    if ser:
                        try:
                            ser.close()
                        except OSError:
                            pass
            if last_error:
                break

        if last_error:
            return {
                "target": device,
                "type": "Modbus RTU标准参数只读验证",
                "request": "标准列表Modbus RTU 0x03只读模板",
                "response": last_error,
                "status": "异常",
                "summary": "发送只读探测帧失败",
                "duration_ms": 0,
            }
        return {
            "target": device,
            "type": "Modbus RTU标准参数只读验证",
            "request": "标准列表Modbus RTU 0x03只读模板",
            "response": "超时",
            "status": "需确认",
            "summary": "常见串口参数下未收到Modbus响应",
            "duration_ms": 0,
        }

    def _modbus_rtu_probe_frames(self) -> List[tuple]:
        frames = []
        for probe in self.config.protocol_probes("modbus_rtu"):
            slave_id = int(probe.get("slave_id", 1) or 1)
            registers = probe.get("test_registers", []) or [{"address": 0, "count": 1, "description": "默认只读探测"}]
            for reg in registers:
                address = int(reg.get("address", 0) or 0)
                count = int(reg.get("count", 1) or 1)
                function_code = int(reg.get("function_code", probe.get("function_code", 3)) or 3)
                if function_code not in (1, 2, 3, 4):
                    continue
                label = str(reg.get("description") or probe.get("device_type") or "标准参数")
                frames.append((self._modbus_rtu_read_frame(slave_id, address, count, function_code), label))
        if not frames:
            frames.append((MODBUS_RTU_READ_HOLDING, "默认保持寄存器0"))
        return frames[:6]

    def _modbus_rtu_read_frame(self, slave_id: int, address: int, count: int,
                               function_code: int = 3) -> bytes:
        return build_modbus_rtu_read(slave_id, function_code, address, count)

    def _crc16_modbus(self, data: bytes) -> int:
        return crc16_modbus(data)

    def _scan_network_interfaces(self) -> List[CheckItem]:
        self._log("扫描网口：读取网卡状态、邻居表，并测试工业常用TCP端口")
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        neighbors = self._read_ip_neighbors()
        items: List[CheckItem] = []

        for name, iface_addrs in addrs.items():
            name_low = name.lower()
            if name_low == "lo" or "loopback" in name_low:
                continue

            iface_stat = stats.get(name)
            is_up = bool(iface_stat and iface_stat.isup)
            ipv4 = ""
            netmask = ""
            mac = ""
            for addr in iface_addrs:
                family_name = getattr(addr.family, "name", str(addr.family))
                if addr.family == socket.AF_INET or family_name == "AF_INET":
                    ipv4 = addr.address
                    netmask = addr.netmask or ""
                elif family_name in ("AF_PACKET", "AF_LINK"):
                    mac = addr.address

            iface_neighbors = neighbors.get(name, [])
            if ipv4.startswith("127."):
                continue
            if ipv4.startswith("169.254.") and not iface_neighbors:
                continue
            discovered_hosts = self._discover_network_hosts(name, ipv4, netmask, iface_neighbors)
            link_tests = self._test_network_links(name, ipv4, netmask, discovered_hosts)
            if not is_up and not iface_neighbors and not link_tests:
                continue
            if not ipv4 and not iface_neighbors and not link_tests:
                continue

            open_ports_count = len(link_tests)
            modbus_count = sum(1 for item in link_tests if item.get("status") == "有响应")
            if link_tests:
                status = "正常"
                summary = f"网口UP，发现 {open_ports_count} 条可连通工业端口"
                if modbus_count:
                    summary += f"，其中 {modbus_count} 条Modbus TCP有响应"
            elif discovered_hosts:
                status = "正常"
                summary = f"网口UP，发现 {len(discovered_hosts)} 个同网段设备"
            elif is_up:
                status = "需确认"
                summary = "网口UP，但暂未发现外部设备或开放工业端口"
            else:
                status = "异常"
                summary = "网口DOWN"

            items.append(CheckItem(
                name=name,
                category="网口",
                status=status,
                summary=summary,
                details={
                    "ip": ipv4,
                    "mac": mac,
                    "netmask": netmask,
                    "speed": f"{iface_stat.speed}Mbps" if iface_stat and iface_stat.speed else "",
                    "is_up": is_up,
                    "neighbors_count": len(iface_neighbors),
                    "device_count": len(discovered_hosts),
                    "open_ports_count": open_ports_count,
                    "modbus_responses": modbus_count,
                    "neighbors": iface_neighbors,
                    "discovered_hosts": discovered_hosts,
                    "link_tests": link_tests,
                },
                method="网口状态 + ARP/邻居表 + ICMP主机发现 + TCP端口连通 + Modbus TCP设备识别",
                request=f"读取网卡状态和邻居表；小网段执行ICMP主机发现；测试端口 {self.tcp_ports}；502连通时发送 00 01 00 00 00 05 01 2B 0E 01 00",
                risk_note="TCP连接和只读设备识别，不发送写寄存器命令",
            ))

        self._log(f"网口检测完成：展示 {len(items)} 个有效网口")
        return items

    def _discover_network_hosts(self, iface_name: str, ipv4: str, netmask: str,
                                iface_neighbors: List[dict]) -> List[dict]:
        hosts: Dict[str, dict] = {}
        for item in iface_neighbors:
            ip = item.get("ip", "")
            if not ip:
                continue
            hosts[ip] = {
                "ip": ip,
                "mac": item.get("mac", ""),
                "state": item.get("state", ""),
                "source": "ARP/邻居表",
            }

        candidates = self._subnet_candidates(ipv4, netmask) if self.subnet_probe and ipv4 and netmask else []
        candidates = [ip for ip in candidates if ip != ipv4 and ip not in hosts]
        if candidates:
            self._log(f"{iface_name}: 执行ICMP主机发现 {len(candidates)} 个目标")
            max_workers = min(64, max(8, len(candidates)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                jobs = {executor.submit(self._ping_probe, ip): ip for ip in candidates}
                for job in as_completed(jobs):
                    ip = jobs[job]
                    if job.result():
                        hosts[ip] = {
                            "ip": ip,
                            "mac": "",
                            "state": "REACHABLE",
                            "source": "ICMP",
                        }

        return [hosts[ip] for ip in sorted(hosts, key=self._ip_sort_key)]

    def _ping_probe(self, ip: str) -> bool:
        if os.name == "nt":
            cmd = ["ping", "-n", "1", "-w", "350", ip]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", ip]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5)
            return result.returncode == 0
        except Exception:
            return False

    def _test_network_links(self, iface_name: str, ipv4: str, netmask: str, iface_neighbors: List[dict]) -> List[dict]:
        targets = {item.get("ip") for item in iface_neighbors if item.get("ip")}
        if self.subnet_probe and ipv4 and netmask:
            targets.update(self._subnet_candidates(ipv4, netmask))
        targets.discard("")
        targets.discard(ipv4)
        if not targets:
            return []

        jobs = []
        tests = []
        max_workers = min(96, max(8, len(targets) * len(self.tcp_ports)))
        self._log(f"{iface_name}: 测试 {len(targets)} 个目标的TCP端口 {self.tcp_ports}")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for ip in sorted(targets, key=self._ip_sort_key):
                for port in self.tcp_ports:
                    jobs.append(executor.submit(self._tcp_probe, ip, port))
            for job in as_completed(jobs):
                result = job.result()
                if result:
                    tests.append(result)
                    self.connection_tests.append(result)

        tests.sort(key=lambda item: (self._ip_sort_key(item.get("target_ip", "")), item.get("port", 0)))
        return tests

    def _subnet_candidates(self, ipv4: str, netmask: str) -> List[str]:
        try:
            network = ipaddress.ip_network(f"{ipv4}/{netmask}", strict=False)
        except ValueError:
            return []
        if network.num_addresses > 512:
            self._log(f"跳过大网段自动端口探测: {network}")
            return []
        return [str(host) for host in network.hosts()]

    def _tcp_probe(self, ip: str, port: int) -> Optional[dict]:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.22)
        try:
            code = sock.connect_ex((ip, port))
            duration_ms = round((time.time() - start) * 1000, 1)
            if code != 0:
                return None
            result = {
                "target": f"{ip}:{port}",
                "target_ip": ip,
                "port": port,
                "type": "TCP端口连通",
                "request": "TCP connect",
                "response": "connected",
                "status": "连通",
                "summary": f"{ip}:{port} 可连接",
                "duration_ms": duration_ms,
            }
            if port == 502:
                modbus = self._modbus_tcp_standard_probe(ip, port) or self._modbus_tcp_identify(ip, port)
                if modbus:
                    result.update(modbus)
            return result
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _modbus_tcp_identify(self, ip: str, port: int) -> Optional[dict]:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.45)
        try:
            sock.connect((ip, port))
            sock.sendall(MODBUS_TCP_DEVICE_ID)
            response = sock.recv(260)
            duration_ms = round((time.time() - start) * 1000, 1)
            if response:
                return {
                    "type": "TCP连通 + Modbus TCP设备识别",
                    "request": f"TCP connect; {self._bytes_to_hex(MODBUS_TCP_DEVICE_ID)}",
                    "response": self._bytes_to_hex(response),
                    "status": "有响应",
                    "summary": f"{ip}:{port} 对Modbus TCP设备识别请求有响应",
                    "duration_ms": duration_ms,
                }
        except OSError:
            return None
        finally:
            try:
                sock.close()
            except OSError:
                pass
        return None

    def _modbus_tcp_standard_probe(self, ip: str, port: int) -> Optional[dict]:
        probes = self.config.protocol_probes("modbus_tcp")
        if not probes:
            probes = [{
                "unit_id": 1,
                "device_type": "Modbus TCP设备",
                "test_registers": [{"address": 0, "count": 1, "description": "默认只读探测"}],
            }]
        transaction_id = 2
        for probe in probes[:8]:
            unit_id = int(probe.get("unit_id", 1) or 1)
            registers = probe.get("test_registers", []) or [{"address": 0, "count": 1, "description": "默认只读探测"}]
            for reg in registers[:2]:
                address = int(reg.get("address", 0) or 0)
                count = int(reg.get("count", 1) or 1)
                label = str(reg.get("description") or probe.get("device_type") or "标准参数")
                frame = self._modbus_tcp_read_frame(transaction_id, unit_id, address, count)
                transaction_id += 1
                start = time.time()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.45)
                try:
                    sock.connect((ip, port))
                    sock.sendall(frame)
                    response = sock.recv(260)
                    duration_ms = round((time.time() - start) * 1000, 1)
                    if response:
                        status_note = self._modbus_tcp_response_note(response)
                        return {
                            "type": "TCP连通 + Modbus TCP标准参数只读验证",
                            "request": f"{label}; {self._bytes_to_hex(frame)}",
                            "response": self._bytes_to_hex(response),
                            "status": "有响应",
                            "summary": f"{ip}:{port} 对标准只读寄存器请求有响应；{status_note}",
                            "duration_ms": duration_ms,
                        }
                except OSError:
                    continue
                finally:
                    try:
                        sock.close()
                    except OSError:
                        pass
        return None

    def _modbus_tcp_read_frame(self, transaction_id: int, unit_id: int, address: int, count: int) -> bytes:
        return build_modbus_tcp_read(transaction_id, unit_id, 3, address, count)

    def _modbus_tcp_response_note(self, response: bytes) -> str:
        if len(response) > 7 and response[7] == 0x03:
            return "功能码0x03读取成功"
        if len(response) > 8 and response[7] == 0x83:
            return f"设备返回Modbus异常码0x{response[8]:02X}，链路和协议可达"
        return "收到协议响应"

    def _read_ip_neighbors(self) -> Dict[str, List[dict]]:
        neighbors: Dict[str, List[dict]] = {}
        try:
            result = subprocess.run(["ip", "-json", "neigh"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and result.stdout.strip():
                for item in json.loads(result.stdout):
                    dev = item.get("dev", "")
                    state = item.get("state", "")
                    if state in ("FAILED", "INCOMPLETE"):
                        continue
                    neighbors.setdefault(dev, []).append({
                        "ip": item.get("dst", ""),
                        "mac": item.get("lladdr", ""),
                        "state": state,
                    })
                return neighbors
        except Exception as exc:
            self._log(f"ip -json neigh不可用，尝试普通ip neigh: {exc}")

        try:
            result = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=3)
            if result.returncode != 0:
                return neighbors
            for line in result.stdout.splitlines():
                parsed = self._parse_ip_neigh_line(line)
                if parsed:
                    neighbors.setdefault(parsed["dev"], []).append({
                        "ip": parsed["ip"],
                        "mac": parsed["mac"],
                        "state": parsed["state"],
                    })
        except Exception as exc:
            self._log(f"读取邻居表失败: {exc}")
        return neighbors

    def _parse_ip_neigh_line(self, line: str) -> Optional[dict]:
        parts = line.split()
        if not parts or "dev" not in parts:
            return None
        try:
            ip = parts[0]
            dev = parts[parts.index("dev") + 1]
            mac = parts[parts.index("lladdr") + 1] if "lladdr" in parts else ""
            state = parts[-1]
            if state in ("FAILED", "INCOMPLETE"):
                return None
            return {"ip": ip, "dev": dev, "mac": mac, "state": state}
        except (ValueError, IndexError):
            return None

    def _scan_pci_devices(self) -> List[CheckItem]:
        self._log("扫描PCI通信类硬件：枚举、驱动绑定、sysfs被动状态")
        devices = self._lspci_devices() or self._sysfs_pci_devices()
        items: List[CheckItem] = []
        for dev in devices:
            if not self._is_relevant_pci(dev):
                continue
            checks = self._pci_passive_checks(dev)
            details = {**dev, **checks}
            driver = details.get("driver", "")
            net_interfaces = details.get("net_interfaces", [])
            tty_nodes = details.get("tty_nodes", [])
            passive_bits = []
            if driver:
                passive_bits.append(f"驱动已绑定: {driver}")
            else:
                passive_bits.append("未发现驱动绑定")
            if net_interfaces:
                passive_bits.append(f"关联网口: {', '.join(item.get('name', '') for item in net_interfaces)}")
            if tty_nodes:
                passive_bits.append(f"关联串口: {', '.join(tty_nodes)}")
            if details.get("pcie_link"):
                passive_bits.append(f"PCIe链路: {details['pcie_link']}")
            if details.get("enabled") == "1":
                passive_bits.append("设备处于启用状态")

            status = "正常" if driver or net_interfaces or tty_nodes else "需确认"
            summary = "；".join(passive_bits)
            if not summary:
                summary = dev.get("description") or dev.get("class") or "发现PCI通信类设备"

            items.append(CheckItem(
                name=dev.get("slot", "未知PCI设备"),
                category="PCI",
                status=status,
                summary=summary,
                details=details,
                method="PCI硬件枚举 + 驱动绑定 + sysfs被动状态检查",
                request="读取 lspci、/sys/bus/pci/devices、driver、net、tty、enable、irq、PCIe链路状态",
                risk_note="PCI被动读取，不向外设发送通信帧，不改变设备状态",
            ))

        self._log(f"PCI检测完成：展示 {len(items)} 个通信相关PCI设备")
        return items

    def _lspci_devices(self) -> List[dict]:
        try:
            result = subprocess.run(["lspci", "-vmm", "-nn"], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return []
            return self._parse_lspci_vmm(result.stdout)
        except Exception as exc:
            self._log(f"lspci不可用: {exc}")
            return []

    def _parse_lspci_vmm(self, output: str) -> List[dict]:
        devices = []
        current: Dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line:
                if current:
                    devices.append(self._normalize_lspci_record(current))
                    current = {}
                continue
            if ":\t" in line:
                key, value = line.split(":\t", 1)
                current[key] = value
        if current:
            devices.append(self._normalize_lspci_record(current))
        return devices

    def _normalize_lspci_record(self, record: Dict[str, str]) -> dict:
        class_text = record.get("Class", "")
        class_code = self._extract_class_code(class_text)
        return {
            "slot": record.get("Slot", ""),
            "class": class_text,
            "class_code": class_code,
            "vendor": record.get("Vendor", ""),
            "vendor_id": self._extract_bracket_id(record.get("Vendor", "")),
            "device": record.get("Device", ""),
            "device_id": self._extract_bracket_id(record.get("Device", "")),
            "subsystem_vendor_id": self._extract_bracket_id(record.get("SVendor", "")),
            "subsystem_device_id": self._extract_bracket_id(record.get("SDevice", "")),
            "driver": record.get("Driver", ""),
            "module": record.get("Module", ""),
            "description": record.get("Device", "") or class_text,
        }

    def _sysfs_pci_devices(self) -> List[dict]:
        base = "/sys/bus/pci/devices"
        if not os.path.isdir(base):
            return []
        devices = []
        for slot in sorted(os.listdir(base)):
            path = os.path.join(base, slot)
            class_code = self._read_file(os.path.join(path, "class"))
            vendor = self._read_file(os.path.join(path, "vendor"))
            device = self._read_file(os.path.join(path, "device"))
            subsystem_vendor = self._read_file(os.path.join(path, "subsystem_vendor"))
            subsystem_device = self._read_file(os.path.join(path, "subsystem_device"))
            driver = ""
            driver_link = os.path.join(path, "driver")
            if os.path.islink(driver_link):
                driver = os.path.basename(os.path.realpath(driver_link))
            class_name = self._class_code_name(class_code)
            devices.append({
                "slot": slot,
                "class": class_name,
                "class_code": class_code,
                "vendor": vendor,
                "vendor_id": self._normalize_pci_id(vendor),
                "device": device,
                "device_id": self._normalize_pci_id(device),
                "subsystem_vendor_id": self._normalize_pci_id(subsystem_vendor),
                "subsystem_device_id": self._normalize_pci_id(subsystem_device),
                "driver": driver,
                "module": "",
                "description": f"{class_name} {slot}".strip(),
            })
        return devices

    def _is_relevant_pci(self, dev: dict) -> bool:
        text = " ".join(str(value).lower() for value in dev.values())
        keywords = (
            "serial", "communication", "ethernet", "network", "fieldbus",
            "can", "modbus", "rs232", "rs485", "profinet", "ethercat",
        )
        if any(keyword in text for keyword in keywords):
            return True
        class_code = str(dev.get("class_code", "")).lower().replace("0x", "")
        return class_code.startswith(("02", "07"))

    def _pci_passive_checks(self, dev: dict) -> dict:
        slot = dev.get("slot", "")
        path = self._pci_sysfs_path(slot)
        checks = {
            "enabled": "",
            "irq": "",
            "driver": dev.get("driver", ""),
            "net_interfaces": [],
            "tty_nodes": [],
            "pcie_link": "",
            "passive_checks": [],
        }
        if path:
            checks["enabled"] = self._read_file(os.path.join(path, "enable"))
            checks["irq"] = self._read_file(os.path.join(path, "irq"))
            if not checks["driver"]:
                driver_link = os.path.join(path, "driver")
                if os.path.islink(driver_link):
                    checks["driver"] = os.path.basename(os.path.realpath(driver_link))
            net_dir = os.path.join(path, "net")
            if os.path.isdir(net_dir):
                checks["net_interfaces"] = [
                    self._net_passive_status(path, name)
                    for name in sorted(os.listdir(net_dir))
                ]
            checks["tty_nodes"] = self._tty_nodes_for_pci(os.path.basename(path))
        checks["pcie_link"] = self._pci_link_status(slot)

        if checks["enabled"] == "1":
            checks["passive_checks"].append("sysfs enable=1")
        if checks["driver"]:
            checks["passive_checks"].append(f"driver={checks['driver']}")
        if checks["irq"]:
            checks["passive_checks"].append(f"irq={checks['irq']}")
        if checks["net_interfaces"]:
            checks["passive_checks"].append("net接口节点存在")
        if checks["tty_nodes"]:
            checks["passive_checks"].append("tty设备节点存在")
        if checks["pcie_link"]:
            checks["passive_checks"].append("lspci -vv读取PCIe链路状态")
        return checks

    def _pci_sysfs_path(self, slot: str) -> str:
        if not slot:
            return ""
        candidates = [slot]
        if not slot.startswith("0000:"):
            candidates.insert(0, f"0000:{slot}")
        for candidate in candidates:
            path = os.path.join("/sys/bus/pci/devices", candidate)
            if os.path.exists(path):
                return path
        return ""

    def _net_passive_status(self, pci_path: str, name: str) -> dict:
        net_path = os.path.join(pci_path, "net", name)
        return {
            "name": name,
            "operstate": self._read_file(os.path.join(net_path, "operstate")),
            "carrier": self._read_file(os.path.join(net_path, "carrier")),
            "speed": self._read_file(os.path.join(net_path, "speed")),
        }

    def _pci_tty_nodes(self) -> List[str]:
        nodes = []
        for tty_path in glob.glob("/sys/class/tty/*"):
            device_link = os.path.join(tty_path, "device")
            if not os.path.exists(device_link):
                continue
            realpath = os.path.realpath(device_link).lower()
            if "/pci" in realpath:
                nodes.append(f"/dev/{os.path.basename(tty_path)}")
        return sorted(set(nodes))

    def _tty_nodes_for_pci(self, slot: str) -> List[str]:
        nodes = []
        if not slot:
            return nodes
        slot_low = slot.lower()
        short_low = slot_low.replace("0000:", "")
        for tty_path in glob.glob("/sys/class/tty/*"):
            device_link = os.path.join(tty_path, "device")
            if not os.path.exists(device_link):
                continue
            realpath = os.path.realpath(device_link).lower()
            if slot_low in realpath or short_low in realpath:
                nodes.append(f"/dev/{os.path.basename(tty_path)}")
        return sorted(set(nodes))

    def _pci_link_status(self, slot: str) -> str:
        if not slot:
            return ""
        try:
            result = subprocess.run(["lspci", "-s", slot, "-vv"], capture_output=True, text=True, timeout=4)
            if result.returncode != 0:
                return ""
            for line in result.stdout.splitlines():
                text = line.strip()
                if text.startswith("LnkSta:"):
                    return text.replace("LnkSta:", "", 1).strip()
        except Exception:
            return ""
        return ""

    def _extract_class_code(self, class_text: str) -> str:
        match = re.search(r"\[([0-9a-fA-F]{4})\]", class_text or "")
        return match.group(1) if match else ""

    def _extract_bracket_id(self, text: str) -> str:
        matches = re.findall(r"\[([0-9a-fA-F]{4})\]", text or "")
        return matches[-1].lower() if matches else ""

    def _class_code_name(self, class_code: str) -> str:
        code = (class_code or "").lower().replace("0x", "")
        if code.startswith("02"):
            return "Network controller"
        if code.startswith("07"):
            return "Communication controller"
        return "PCI device"

    def _read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _ip_sort_key(self, ip: str):
        try:
            return tuple(int(part) for part in ip.split("."))
        except ValueError:
            return (999, 999, 999, 999)

    def _system_info(self) -> Dict[str, str]:
        return {
            "os": platform.platform(),
            "kernel": platform.release(),
            "python": platform.python_version(),
        }

    def _now(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _bytes_to_hex(self, payload: bytes) -> str:
        return " ".join(f"{byte:02X}" for byte in payload)

    def _log(self, message: str):
        self.logs.append(f"[{self._now()}] {message}")
