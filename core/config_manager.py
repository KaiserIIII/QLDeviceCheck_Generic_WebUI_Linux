import copy
import json
import os
import re
from typing import Dict, Iterable, List, Optional

from .protocol_engine import ProtocolConfigError, decode_hex


DEFAULT_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "device_list.json",
)


class StandardDeviceConfig:
    """Loads the teacher-provided device_list.json format.

    The file is a catalog of known device communication methods. Runtime
    scanning only displays catalog devices that produce matching evidence.
    """

    def __init__(self, path: str = DEFAULT_CONFIG, data: Optional[Dict[str, object]] = None):
        self.path = path
        self.data = copy.deepcopy(data) if data is not None else self._load()
        self._validate()

    def selected(self, device_ids: Iterable[str], interfaces: Optional[Dict[str, str]] = None):
        """Return a validated config containing only selected devices.

        Interfaces discovered during scanning can be pinned for a faster and
        deterministic follow-up connectivity test.
        """
        selected_ids = {str(item) for item in device_ids if str(item)}
        interface_map = {str(key): str(value) for key, value in (interfaces or {}).items() if value}
        selected_devices = []
        for raw in self.raw_devices():
            parent_id = str(raw.get("device_id") or "")
            children = raw.get("child_devices", []) or []
            selected_children = [
                copy.deepcopy(child)
                for child in children
                if str(child.get("device_id") or "") in selected_ids
            ]
            if parent_id not in selected_ids and not selected_children:
                continue
            item = copy.deepcopy(raw)
            item["child_devices"] = selected_children
            if parent_id in interface_map:
                item["interface"] = interface_map[parent_id]
            for child in item.get("child_devices", []) or []:
                child_id = str(child.get("device_id") or "")
                if child_id in interface_map:
                    child["interface"] = interface_map[child_id]
            selected_devices.append(item)
        data = copy.deepcopy(self.data)
        data["devices"] = selected_devices
        return StandardDeviceConfig(self.path, data=data)

    def for_interface(self, connection_type: str, interface: str):
        """Return catalog devices that can be checked through one interface.

        Serial and network devices are pinned to the selected Linux interface.
        PCI catalog rules are retained but evaluated only against the selected
        slot. None is returned when the catalog has no devices of that type.
        """
        connection_type = str(connection_type or "").strip().lower()
        interface = str(interface or "").strip()
        if connection_type not in ("serial", "network", "pci"):
            raise ValueError(f"不支持的接口类型: {connection_type}")
        if not interface:
            raise ValueError("接口名称不能为空")

        selected_devices = []
        for raw in self.raw_devices():
            if self._infer_connection_type(raw) != connection_type:
                continue
            item = copy.deepcopy(raw)
            item["child_devices"] = [
                copy.deepcopy(child)
                for child in raw.get("child_devices", []) or []
                if self._infer_connection_type(child, raw) == connection_type
            ]
            self._pin_interface(item, connection_type, interface)
            for child in item.get("child_devices", []) or []:
                if self._device_has_own_carrier(child):
                    self._pin_interface(child, connection_type, interface)
            selected_devices.append(item)

        if not selected_devices:
            return None
        data = copy.deepcopy(self.data)
        data["devices"] = selected_devices
        return StandardDeviceConfig(self.path, data=data)

    def workstation_id(self) -> str:
        return str(self.data.get("workstation_id", ""))

    def description(self) -> str:
        return str(self.data.get("description", ""))

    def settings(self) -> Dict[str, object]:
        return copy.deepcopy(self.data.get("settings", {}) or {})

    def allow_write_tests(self) -> bool:
        return bool(self.data.get("settings", {}).get("allow_write_tests", False))

    def system(self) -> str:
        return "银河麒麟 OS V11"

    def architecture(self) -> List[str]:
        return ["接口识别层", "设备搜索层", "通信测试层", "配置管理"]

    def raw_devices(self) -> List[Dict[str, object]]:
        return copy.deepcopy(self.data.get("devices", []))

    def devices(self) -> List[Dict[str, object]]:
        return [self._normalize_device(item) for item in self.raw_devices()]

    def direct_devices(self) -> List[Dict[str, object]]:
        return [
            item for item in self.devices()
            if not item.get("is_intermediate_module")
        ]

    def intermediate_modules(self) -> List[Dict[str, object]]:
        return [
            item for item in self.devices()
            if item.get("is_intermediate_module")
        ]

    def child_device_templates(self) -> List[Dict[str, object]]:
        children: List[Dict[str, object]] = []
        for device in self.raw_devices():
            for child in device.get("child_devices", []) or []:
                children.append(self._normalize_device(child, parent=device))
        return children

    def all_configured_devices(self) -> List[Dict[str, object]]:
        items: List[Dict[str, object]] = []
        for device in self.raw_devices():
            items.append(self._normalize_device(device))
            for child in device.get("child_devices", []) or []:
                items.append(self._normalize_device(child, parent=device))
        return items

    def protocol_probes(self, protocol_type: str) -> List[Dict[str, object]]:
        probes: List[Dict[str, object]] = []
        for device in self.raw_devices():
            probes.extend(self._collect_protocol_probes(device, protocol_type))
        return probes

    def _collect_protocol_probes(self, device: Dict[str, object], protocol_type: str) -> Iterable[Dict[str, object]]:
        protocol = device.get("protocol", {}) or {}
        if str(protocol.get("type", "")).lower() == protocol_type.lower():
            item = copy.deepcopy(protocol)
            item["device_type"] = device.get("device_type", "")
            item["model"] = device.get("device_name", "")
            yield item
        for child in device.get("child_devices", []) or []:
            yield from self._collect_protocol_probes(child, protocol_type)

    def _normalize_device(self, raw: Dict[str, object], parent: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        device = copy.deepcopy(raw)
        protocol = device.get("protocol", {}) or {}
        connection_type = self._infer_connection_type(device, parent)
        device["model"] = device.get("device_name", "")
        device["connection_type"] = connection_type
        device["connection"] = self._connection_label(device, parent)
        device["parameters"] = self._parameter_label(device, parent)
        device["protocols"] = [str(protocol.get("type", "未配置"))] if protocol else []
        device["role"] = "middle_module" if device.get("is_intermediate_module") else ""
        device["match"] = {"device_id": device.get("device_id", "")}
        if parent:
            device["parent_id"] = parent.get("device_id", "")
            device["parent_name"] = parent.get("device_name", "")
            device["is_child_device"] = True
            device["child_devices"] = []
        else:
            device["child_devices"] = [
                self._normalize_device(child, parent=raw)
                for child in raw.get("child_devices", []) or []
            ]
        return device

    def _connection_label(self, device: Dict[str, object], parent: Optional[Dict[str, object]]) -> str:
        connection_type = self._infer_connection_type(device, parent)
        if parent:
            return f"通过{parent.get('device_name', '中间模块')}"
        labels = {
            "serial": "串口",
            "network": "网口",
            "pci": "PCI",
        }
        return labels.get(connection_type, connection_type or "未配置")

    def _parameter_label(self, device: Dict[str, object], parent: Optional[Dict[str, object]]) -> str:
        connection_type = self._infer_connection_type(device, parent)
        target = device if (
            not parent
            or device.get("interface")
            or device.get("serial_config")
            or device.get("network_config")
            or device.get("pci_slot")
            or device.get("pci_match")
        ) else parent
        if connection_type == "serial":
            serial_cfg = target.get("serial_config", {}) or {}
            iface = target.get("interface") or "全串口扫描"
            return (
                f"{iface}; "
                f"{serial_cfg.get('baudrate', 9600)}, "
                f"{serial_cfg.get('bytesize', 8)}, "
                f"{serial_cfg.get('parity', 'N')}, "
                f"{serial_cfg.get('stopbits', 1)}"
            )
        if connection_type == "network":
            net_cfg = target.get("network_config", {}) or {}
            transport = str(net_cfg.get("transport") or net_cfg.get("transport_protocol") or "tcp").upper()
            role = str(net_cfg.get("device_role") or net_cfg.get("role") or "server").lower()
            role_text = "客户端" if role in ("client", "device_client", "客户端") else "服务端"
            return f"{target.get('interface') or '自动选择网口'}; {net_cfg.get('ip', '')}:{net_cfg.get('port', '')}; {transport}; 设备{role_text}"
        if connection_type == "pci":
            provided = ",".join(target.get("provided_interfaces", []) or [])
            match = target.get("pci_match", {}) or {}
            identity = str(target.get("pci_slot") or "")
            if not identity and match:
                identity = ", ".join(f"{key}={value}" for key, value in match.items() if value not in (None, "", []))
            return "; ".join(part for part in (identity or "自动匹配PCI", provided) if part)
        return ""

    def _infer_connection_type(self, device: Dict[str, object], parent: Optional[Dict[str, object]] = None) -> str:
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

    def _device_has_own_carrier(self, device: Dict[str, object]) -> bool:
        return any(
            device.get(key)
            for key in (
                "interface",
                "serial_config",
                "network_config",
                "pci_slot",
                "pci_match",
                "provided_interfaces",
            )
        )

    def _pin_interface(self, device: Dict[str, object], connection_type: str, interface: str):
        if connection_type in ("serial", "network"):
            device["interface"] = interface
        else:
            device["pci_slot"] = interface

    def _load(self) -> Dict[str, object]:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _validate(self):
        errors = []
        try:
            schema_version = int(self.data.get("schema_version", 1) or 1)
            if schema_version not in (1, 2):
                errors.append(f"不支持的schema_version: {schema_version}")
        except (TypeError, ValueError):
            errors.append("schema_version必须是整数")
        if not self.data.get("workstation_id"):
            errors.append("缺少 workstation_id")
        devices = self.data.get("devices", [])
        if not isinstance(devices, list) or not devices:
            errors.append("devices 必须是非空列表")
        try:
            report_count = int((self.data.get("settings", {}) or {}).get("max_report_count", 20) or 20)
            if not 1 <= report_count <= 1000:
                errors.append("settings.max_report_count必须在1到1000之间")
        except (TypeError, ValueError):
            errors.append("settings.max_report_count必须是整数")
        seen_ids = set()
        for index, device in enumerate(devices, start=1):
            self._validate_device(device, errors, f"第{index}项", seen_ids=seen_ids)
        if errors:
            raise ValueError("设备配置验证失败:\n" + "\n".join(errors))

    def _validate_device(
        self,
        device: Dict[str, object],
        errors: List[str],
        label: str,
        parent: Optional[Dict[str, object]] = None,
        seen_ids: Optional[set] = None,
    ):
        device_id = device.get("device_id") or label
        for key in ("device_id", "device_name", "device_type"):
            if not device.get(key):
                errors.append(f"{device_id} 缺少 {key}")
        if seen_ids is not None and device.get("device_id"):
            if device_id in seen_ids:
                errors.append(f"device_id 重复: {device_id}")
            seen_ids.add(device_id)
        connection_type = self._infer_connection_type(device, parent)
        carrier = device if (
            device.get("interface")
            or device.get("serial_config")
            or device.get("network_config")
            or device.get("pci_slot")
            or device.get("pci_match")
        ) else (parent or device)
        if not connection_type and not device.get("module_address"):
            errors.append(f"{device_id} 缺少 connection_type")
        if connection_type and connection_type not in ("serial", "network", "pci"):
            errors.append(f"{device_id} connection_type仅支持serial、network、pci")
        if connection_type == "serial" and not (device.get("serial_config") or carrier.get("serial_config")):
            errors.append(f"{device_id} 缺少 serial_config")
        if connection_type == "serial":
            self._validate_serial_config(device.get("serial_config", {}) or carrier.get("serial_config", {}), errors, str(device_id))
            self._validate_protocol(device.get("protocol", {}) or carrier.get("protocol", {}), errors, str(device_id))
        if connection_type == "network":
            net_cfg = device.get("network_config", {}) or carrier.get("network_config", {}) or {}
            if not net_cfg:
                errors.append(f"{device_id} 缺少 network_config")
            else:
                self._validate_network_config(net_cfg, errors, str(device_id))
            self._validate_protocol(device.get("protocol", {}) or carrier.get("protocol", {}), errors, str(device_id))
        if connection_type == "pci" and not (carrier.get("pci_slot") or carrier.get("pci_match")):
            errors.append(f"{device_id} 必须配置 pci_slot 或 pci_match")
        for child in device.get("child_devices", []) or []:
            self._validate_device(child, errors, f"{device_id} 的子设备", parent=device, seen_ids=seen_ids)

    def _validate_serial_config(self, config: Dict[str, object], errors: List[str], device_id: str):
        try:
            baudrate = int(config.get("baudrate", 0) or 0)
            bytesize = int(config.get("bytesize", 8) or 8)
            stopbits = float(config.get("stopbits", 1) or 1)
            timeout = float(config.get("timeout", 2) or 2)
        except (TypeError, ValueError):
            errors.append(f"{device_id} 串口参数必须是数字")
            return
        if baudrate <= 0:
            errors.append(f"{device_id} baudrate必须大于0")
        if bytesize not in (5, 6, 7, 8):
            errors.append(f"{device_id} bytesize必须是5、6、7或8")
        if str(config.get("parity", "N")).upper() not in ("N", "E", "O", "M", "S"):
            errors.append(f"{device_id} parity必须是N、E、O、M或S")
        if stopbits not in (1, 1.5, 2):
            errors.append(f"{device_id} stopbits必须是1、1.5或2")
        if timeout <= 0 or timeout > 60:
            errors.append(f"{device_id} timeout必须在0到60秒之间")

    def _validate_network_config(self, config: Dict[str, object], errors: List[str], device_id: str):
        transport = str(config.get("transport") or "tcp").lower()
        role = str(config.get("device_role") or "server").lower()
        if transport not in ("tcp", "udp"):
            errors.append(f"{device_id} network_config.transport必须是tcp或udp")
        if role not in ("server", "client", "device_server", "device_client", "服务端", "客户端"):
            errors.append(f"{device_id} network_config.device_role必须是server或client")
        if role in ("server", "device_server", "服务端") and not config.get("ip"):
            errors.append(f"{device_id} 设备作为服务端时必须配置ip")
        port_value = (config.get("local_port") or config.get("port")) if role in ("client", "device_client", "客户端") else config.get("port")
        try:
            port = int(port_value or 0)
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            errors.append(f"{device_id} 网络端口必须在1到65535之间")

    def _validate_protocol(self, protocol: Dict[str, object], errors: List[str], device_id: str):
        if not protocol:
            return
        protocol_type = str(protocol.get("type") or "custom").lower()
        probes = protocol.get("probes", []) or protocol.get("test_registers", []) or [protocol]
        if not isinstance(probes, list):
            errors.append(f"{device_id} protocol.probes必须是数组")
            return
        for index, probe in enumerate(probes, start=1):
            if not isinstance(probe, dict):
                errors.append(f"{device_id} 第{index}个协议规则必须是对象")
                continue
            if protocol_type in ("modbus_rtu", "modbus_tcp"):
                try:
                    function_code = int(probe.get("function_code", protocol.get("function_code", 3)) or 3)
                    address = int(probe.get("address", 0) or 0)
                    count = int(probe.get("count", 1) or 1)
                except (TypeError, ValueError):
                    errors.append(f"{device_id} 第{index}个Modbus规则参数必须是整数")
                    continue
                if function_code not in (1, 2, 3, 4):
                    errors.append(f"{device_id} 自动检测只允许Modbus只读功能码1、2、3、4")
                if not 0 <= address <= 65535 or not 1 <= count <= 2000:
                    errors.append(f"{device_id} 第{index}个Modbus规则address/count超出范围")
            operation = str(probe.get("operation") or protocol.get("operation") or "read").lower()
            if operation == "write" and not self.allow_write_tests():
                errors.append(f"{device_id} 配置了写测试，但settings.allow_write_tests不是true")
            for key in ("request_hex", "test_command_hex", "expected_response_hex"):
                if probe.get(key) not in (None, ""):
                    try:
                        decode_hex(probe[key], key)
                    except ProtocolConfigError as exc:
                        errors.append(f"{device_id} 第{index}个规则: {exc}")
            rule = dict(protocol.get("response_match", {}) or {})
            rule.update(dict(probe.get("response_match", {}) or {}))
            for key in ("exact_hex", "prefix_hex", "suffix_hex", "contains_hex"):
                if rule.get(key) not in (None, ""):
                    try:
                        decode_hex(rule[key], f"response_match.{key}")
                    except ProtocolConfigError as exc:
                        errors.append(f"{device_id} 第{index}个规则: {exc}")
            if rule.get("regex_text") not in (None, ""):
                try:
                    re.compile(str(rule["regex_text"]))
                except re.error as exc:
                    errors.append(f"{device_id} 第{index}个规则regex_text无效: {exc}")
            if rule.get("regex_hex") not in (None, ""):
                try:
                    re.compile(str(rule["regex_hex"]))
                except re.error as exc:
                    errors.append(f"{device_id} 第{index}个规则regex_hex无效: {exc}")
