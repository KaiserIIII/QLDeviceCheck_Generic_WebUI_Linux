
#!/usr/bin/env python3
"""
Industrial PC generic test system - local Web UI.

The page runs real local detection and matches devices against a configurable
standardized device list. Results are shown directly in the Web UI.
"""

import argparse
import copy
import html as html_lib
import json
import os
import shutil
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from core.config_manager import StandardDeviceConfig
from core.generic_detector import DEFAULT_TCP_PORTS, GenericDetector
from inspection.domain import config_hash

REPORT_DIR = os.path.join(BASE_DIR, "report")
WEB_INDEX_PATH = os.path.join(BASE_DIR, "web", "index.html")
REPORT_KEEP_LIMIT = 20
RUN_LOCK = threading.Lock()
SCAN_STATE = {"devices": {}, "updated_at": ""}
CONNECTED_STATUSES = {"在线", "已发现", "正常"}
INTERFACE_GROUP_KEYS = {
    "serial": "serial_ports",
    "network": "network_interfaces",
    "pci": "pci_devices",
}
INTERFACE_CATEGORIES = {"serial": "串口", "network": "网口", "pci": "PCI"}

def detector_from_payload(payload: dict, config: StandardDeviceConfig = None) -> GenericDetector:
    raw_ports = payload.get("tcp_ports", ",".join(str(port) for port in DEFAULT_TCP_PORTS))
    return GenericDetector(
        serial_probe=bool(payload.get("serial_probe", True)),
        subnet_probe=bool(payload.get("subnet_probe", True)),
        tcp_ports=parse_ports(raw_ports),
        config=config,
    )


def parse_ports(value):
    raw_parts = value if isinstance(value, list) else str(value).replace("，", ",").replace(";", ",").split(",")
    ports = []
    for part in raw_parts:
        try:
            port = int(str(part).strip())
        except ValueError:
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports or DEFAULT_TCP_PORTS


def connected_devices(data: dict):
    return [item for item in data.get("devices", []) or [] if item.get("status") in CONNECTED_STATUSES]


def scan_state(config, state=None):
    state = SCAN_STATE if state is None else state
    fingerprint = config_hash(config.data)
    if state.get('config_hash') != fingerprint:
        state.update(devices={}, updated_at='', config_hash=fingerprint)
    return state


def cache_scan_devices(devices, state=None):
    state = SCAN_STATE if state is None else state
    state["devices"] = {
        str(item.get("device_id")): copy.deepcopy(item)
        for item in devices
        if item.get("device_id")
    }
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def same_interface(connection_type: str, left: str, right: str) -> bool:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if connection_type == "pci":
        return left.replace("0000:", "") == right.replace("0000:", "")
    return left == right


def cache_interface_devices(connection_type: str, interface: str, devices, state=None):
    state = SCAN_STATE if state is None else state
    retained = {
        device_id: item
        for device_id, item in state.get("devices", {}).items()
        if not (
            str(item.get("connection_type") or "") == connection_type
            and same_interface(connection_type, item.get("interface", ""), interface)
        )
    }
    for item in devices:
        device_id = str(item.get("device_id") or "")
        if device_id:
            retained[device_id] = copy.deepcopy(item)
    state["devices"] = retained
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def selected_catalog(device_ids, config=None, state=None):
    config = config if config is not None else StandardDeviceConfig()
    state = scan_state(config, state)
    interfaces = {
        device_id: str(item.get("interface") or "")
        for device_id, item in state.get("devices", {}).items()
        if item.get("interface")
    }
    # A child using its parent's carrier must stay inherited. Pinning only the
    # child's interface would turn it into an independent, incomplete carrier.
    carrier_fields = ('interface', 'serial_config', 'network_config', 'pci_slot', 'pci_match', 'provided_interfaces')
    for parent in config.raw_devices():
        for child in parent.get('child_devices', []) or []:
            if not any(child.get(key) for key in carrier_fields):
                discovered = interfaces.pop(str(child.get('device_id')), '')
                if discovered:
                    interfaces.setdefault(str(parent.get('device_id')), discovered)
    return config.selected(device_ids, interfaces)


def update_interface_counts(data: dict):
    counts = {
        connection_type: len(data.get(key, []) or [])
        for connection_type, key in INTERFACE_GROUP_KEYS.items()
    }
    data.setdefault("summary", {}).update({
        "interface_total": sum(counts.values()),
        "serial_count": counts["serial"],
        "network_count": counts["network"],
        "pci_count": counts["pci"],
    })


def prepare_ui_result(data: dict, mode: str, connected_only: bool = False, config=None) -> dict:
    result = copy.deepcopy(data)
    catalog_count = len((config if config is not None else StandardDeviceConfig()).all_configured_devices())
    devices = result.get("devices", []) or []
    if connected_only:
        devices = [item for item in devices if item.get("status") in CONNECTED_STATUSES]
    result["devices"] = devices
    result["mode"] = mode

    passed = sum(1 for item in devices if item.get("status") in CONNECTED_STATUSES)
    warning = sum(1 for item in devices if item.get("status") == "需确认")
    failed = sum(1 for item in devices if item.get("status") == "异常")
    summary = result.setdefault("summary", {})
    summary.update({
        "total": len(devices),
        "catalog_count": catalog_count,
        "device_count": len(devices),
        "connected_count": passed,
        "passed": passed,
        "warning": warning,
        "failed": failed,
        "fault_count": failed,
        "matched_count": passed,
    })
    _rebuild_interface_devices(result, devices)
    update_interface_counts(result)
    return result


def _rebuild_interface_devices(data: dict, devices):
    groups = {
        connection_type: data.get(key, []) or []
        for connection_type, key in INTERFACE_GROUP_KEYS.items()
    }
    index = {}
    for connection_type, items in groups.items():
        for item in items:
            details = item.setdefault("details", {})
            details["configured_devices"] = []
            index[(connection_type, str(item.get("name") or ""))] = item

    for device in devices:
        connection_type = str(device.get("connection_type") or "")
        interface = str(device.get("interface") or "")
        item = index.get((connection_type, interface))
        if not item:
            continue
        name = str(device.get("name") or device.get("device_id") or "")
        if name:
            item["details"]["configured_devices"].append(name)

    for items in groups.values():
        for item in items:
            names = item.get("details", {}).get("configured_devices", []) or []
            if names:
                item["summary"] = f"已发现 {len(names)} 个已知设备"
            elif item.get("status") == "正常":
                item["summary"] = "接口可用，未发现已知设备"

    data["pci_devices"] = [
        item for item in groups["pci"]
        if not str(item.get("name") or "").startswith("匹配规则:")
        or (item.get("details", {}).get("configured_devices", []) or [])
    ]


def preserve_scan_interfaces(result: dict, scan_raw: dict):
    """Keep every first-pass interface after testing discovered devices."""
    for connection_type, key in INTERFACE_GROUP_KEYS.items():
        scanned = copy.deepcopy(scan_raw.get(key, []) or [])
        tested = copy.deepcopy(result.get(key, []) or [])
        merged = []
        used = set()
        for scanned_item in scanned:
            match_index = next((
                index for index, tested_item in enumerate(tested)
                if same_interface(
                    connection_type,
                    scanned_item.get("name", ""),
                    tested_item.get("name", ""),
                )
            ), None)
            if match_index is None:
                merged.append(scanned_item)
                continue
            used.add(match_index)
            tested_item = tested[match_index]
            combined = copy.deepcopy(scanned_item)
            combined.update(tested_item)
            details = copy.deepcopy(scanned_item.get("details", {}) or {})
            details.update(copy.deepcopy(tested_item.get("details", {}) or {}))
            combined["details"] = details
            merged.append(combined)
        merged.extend(item for index, item in enumerate(tested) if index not in used)
        result[key] = merged

    _rebuild_interface_devices(result, result.get("devices", []) or [])
    update_interface_counts(result)
    return result


def scan_catalog(payload: dict, config=None, state=None) -> dict:
    config = config if config is not None else StandardDeviceConfig()
    state = scan_state(config, state)
    raw = detector_from_payload(payload, config=config).run().to_dict()
    discovered = connected_devices(raw)
    cache_scan_devices(discovered, state)
    return prepare_ui_result(raw, "scan", connected_only=True, config=config)


def scan_interface(payload: dict, config=None, state=None) -> dict:
    catalog = config if config is not None else StandardDeviceConfig()
    state = scan_state(catalog, state)
    connection_type = str(payload.get("interface_type") or "").strip().lower()
    interface = str(payload.get("interface") or "").strip()
    if connection_type not in ("serial", "network", "pci"):
        raise ValueError("interface_type必须是serial、network或pci")
    if not interface:
        raise ValueError("缺少接口名称")

    config = catalog.for_interface(connection_type, interface)
    if config is None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        raw = {
            "started_at": now,
            "finished_at": now,
            "duration_ms": 0,
            "summary": {},
            "serial_ports": [],
            "network_interfaces": [],
            "pci_devices": [],
            "devices": [],
            "connection_tests": [],
            "logs": [],
        }
        key = INTERFACE_GROUP_KEYS[connection_type]
        category = INTERFACE_CATEGORIES[connection_type]
        raw[key] = [{
            "name": interface,
            "category": category,
            "status": "正常",
            "summary": "配置库中没有此类已知设备",
            "details": {"configured_devices": []},
        }]
    else:
        raw = detector_from_payload(payload, config=config).run().to_dict()

    devices = [
        item for item in connected_devices(raw)
        if str(item.get("connection_type") or "") == connection_type
        and same_interface(connection_type, item.get("interface", ""), interface)
    ]
    raw["devices"] = devices
    target_key = INTERFACE_GROUP_KEYS[connection_type]
    for key in INTERFACE_GROUP_KEYS.values():
        items = raw.get(key, []) or []
        raw[key] = [
            item for item in items
            if key == target_key and same_interface(connection_type, item.get("name", ""), interface)
        ]
    cache_interface_devices(connection_type, interface, devices, state)
    result = prepare_ui_result(raw, "scan", connected_only=True, config=catalog)
    result["interface_type"] = connection_type
    result["interface_name"] = interface
    return result


def test_all_discovered(payload: dict, config=None, state=None) -> dict:
    catalog = config if config is not None else StandardDeviceConfig()
    state = scan_state(catalog, state)
    scan_raw = detector_from_payload(payload, config=catalog).run().to_dict()
    discovered = connected_devices(scan_raw)
    cache_scan_devices(discovered, state)
    device_ids = [str(item.get("device_id")) for item in discovered if item.get("device_id")]
    if not device_ids:
        result = prepare_ui_result(scan_raw, "test_all", connected_only=True, config=catalog)
    else:
        config = selected_catalog(device_ids, catalog, state)
        tested = detector_from_payload(payload, config=config).run().to_dict()
        result = prepare_ui_result(tested, "test_all", config=catalog)
        preserve_scan_interfaces(result, scan_raw)
        result["summary"]["scan_connected_count"] = len(discovered)
    keep_limit = catalog.settings().get("max_report_count", REPORT_KEEP_LIMIT)
    save_report(result, keep_limit)
    return result


def test_single_device(payload: dict, config=None, state=None) -> dict:
    catalog = config if config is not None else StandardDeviceConfig()
    state = scan_state(catalog, state)
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        raise ValueError("缺少device_id")
    if device_id not in state.get("devices", {}):
        raise ValueError("设备不在当前扫描结果中，请先扫描设备")
    config = selected_catalog([device_id], catalog, state)
    tested = detector_from_payload(payload, config=config).run().to_dict()
    result = prepare_ui_result(tested, "single", config=catalog)
    target = next((item for item in result.get("devices", []) if str(item.get("device_id")) == device_id), None)
    if target is None:
        raise ValueError("未生成该设备的测试结果")
    return {"ok": True, "mode": "single", "device": target, "finished_at": result.get("finished_at", "")}


def save_report(data: dict, keep_limit: int = REPORT_KEEP_LIMIT) -> dict:
    keep_limit = max(1, min(1000, int(keep_limit or REPORT_KEEP_LIMIT)))
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = int((time.time() * 1000) % 1000)
    folder = f"{stamp}_{suffix:03d}"
    folder_path = os.path.join(REPORT_DIR, folder)
    os.makedirs(folder_path, exist_ok=True)
    html_name = "report.html"
    txt_name = "report.txt"
    json_name = "report.json"
    report_info = {
        "folder": folder,
        "html": html_name,
        "txt": txt_name,
        "json": json_name,
        "url": f"/report/{folder}/{html_name}",
        "html_url": f"/report/{folder}/{html_name}",
        "txt_url": f"/report/{folder}/{txt_name}",
        "json_url": f"/report/{folder}/{json_name}",
        "keep_limit": keep_limit,
    }
    logs = data.setdefault("logs", [])
    logs.append(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 生成测试报告：report/{folder}/report.html、report.txt、report.json，report目录最多保留 {keep_limit} 个报告文件夹")
    data["report"] = report_info
    data.setdefault("summary", {})["report_folder"] = folder
    data["summary"]["report_keep_limit"] = keep_limit
    with open(os.path.join(folder_path, html_name), "w", encoding="utf-8") as f:
        f.write(render_compact_report_html(data))
    with open(os.path.join(folder_path, txt_name), "w", encoding="utf-8") as f:
        f.write(render_compact_report_txt(data))
    with open(os.path.join(folder_path, json_name), "w", encoding="utf-8") as f:
        json.dump(compact_report_data(data), f, ensure_ascii=False, indent=2)
    prune_reports(keep_limit)
    return report_info


def prune_reports(keep_limit: int = REPORT_KEEP_LIMIT):
    if not os.path.isdir(REPORT_DIR):
        return
    reports = []
    for name in os.listdir(REPORT_DIR):
        path = os.path.join(REPORT_DIR, name)
        if name.startswith("report_") and name.endswith(".html") and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "report.json")):
            reports.append((os.path.getmtime(path), path))
    reports.sort(reverse=True)
    for _, path in reports[max(1, int(keep_limit or REPORT_KEEP_LIMIT)):]:
        try:
            shutil.rmtree(path)
        except OSError:
            pass


def compact_result_text(device: dict) -> str:
    if device.get("status") in CONNECTED_STATUSES:
        return "连通正常"
    evidence = str(device.get("evidence") or "")
    if "timed out" in evidence.lower() or "超时" in evidence or "未收到响应" in evidence:
        return "无响应"
    if "不存在" in evidence:
        return "接口不存在"
    return "测试失败"


def compact_connection_text(device: dict) -> str:
    connection = {"serial": "串口", "network": "网口", "pci": "PCI"}.get(
        str(device.get("connection_type") or ""), str(device.get("connection_type") or "-")
    )
    protocol = str(device.get("protocol") or "").lower()
    connection = {
        "modbus_rtu": "Modbus RTU",
        "modbus_tcp": "Modbus TCP",
        "tcp": "TCP",
        "udp": "UDP",
    }.get(protocol, connection)
    via = device.get("via_device", {}) or {}
    via_name = str(via.get("device_name") or "")
    return f"{connection} / 经{via_name}" if via_name else connection


def compact_report_data(data: dict) -> dict:
    devices = data.get("devices", []) or []
    summary = data.get("summary", {}) or {}
    interfaces = []
    for category, key in INTERFACE_GROUP_KEYS.items():
        for item in data.get(key, []) or []:
            interfaces.append({
                "type": category,
                "interface": item.get("name", ""),
                "status": item.get("status", ""),
                "devices": (item.get("details", {}) or {}).get("configured_devices", []) or [],
            })

    compact_devices = []
    for device in devices:
        passed = device.get("status") in CONNECTED_STATUSES
        via = device.get("via_device", {}) or {}
        compact_devices.append({
            "device_id": device.get("device_id", ""),
            "device_name": device.get("name", ""),
            "device_type": device.get("type", ""),
            "interface_type": device.get("connection_type", ""),
            "interface": device.get("interface", ""),
            "communication": compact_connection_text(device),
            "via_device": via.get("device_name", ""),
            "result": "PASS" if passed else "FAIL",
            "test_report": compact_result_text(device),
        })

    return {
        "report_version": 1,
        "hostname": data.get("hostname", ""),
        "started_at": data.get("started_at", ""),
        "finished_at": data.get("finished_at", ""),
        "duration_ms": round(float(data.get("duration_ms") or 0), 1),
        "summary": {
            "found": len(devices),
            "passed": summary.get("passed", 0),
            "failed": summary.get("failed", 0),
        },
        "interfaces": interfaces,
        "devices": compact_devices,
        "report": data.get("report", {}) or {},
    }


def render_compact_report_txt(data: dict) -> str:
    summary = data.get("summary", {}) or {}
    devices = data.get("devices", []) or []
    lines = [
        "设备连通性测试报告",
        "=" * 28,
        f"主机: {data.get('hostname', '')}",
        f"测试时间: {data.get('finished_at') or data.get('started_at', '')}",
        f"发现设备: {summary.get('device_count', len(devices))}",
        f"通过: {summary.get('passed', 0)}",
        f"失败: {summary.get('failed', 0)}",
        "",
        "测试结果",
        "-" * 28,
    ]
    if not devices:
        lines.append("未发现配置库中的已知设备")
    for index, device in enumerate(devices, start=1):
        result = "通过" if device.get("status") in CONNECTED_STATUSES else "失败"
        lines.append(
            f"[{index}] {device.get('name', '')} | {device.get('interface', '-') or '-'} | "
            f"{compact_connection_text(device)} | {result} | {compact_result_text(device)}"
        )
    return "\n".join(lines) + "\n"


def render_compact_report_html(data: dict) -> str:
    summary = data.get("summary", {}) or {}
    devices = data.get("devices", []) or []

    def esc(value) -> str:
        return html_lib.escape(str(value if value is not None else ""), quote=True)

    rows = []
    for device in devices:
        passed = device.get("status") in CONNECTED_STATUSES
        result = "通过" if passed else "失败"
        cls = "pass" if passed else "fail"
        rows.append(
            "<tr>"
            f"<td><strong>{esc(device.get('name', ''))}</strong></td>"
            f"<td>{esc(device.get('interface', '') or '-')}</td>"
            f"<td>{esc(compact_connection_text(device))}</td>"
            f"<td><span class=\"result {cls}\">{result}</span></td>"
            f"<td>{esc(compact_result_text(device))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="empty">未发现配置库中的已知设备</td></tr>')

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>设备连通性测试报告</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f3f5f7; color: #18212b; font-family: "Noto Sans CJK SC", "Microsoft YaHei", Arial, sans-serif; }}
    main {{ width: min(1040px, calc(100% - 32px)); margin: 28px auto; }}
    h1 {{ margin: 0 0 18px; font-size: 24px; letter-spacing: 0; }}
    .summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #d9e0e7; border-radius: 8px; background: #fff; margin-bottom: 16px; }}
    .metric {{ padding: 16px; border-right: 1px solid #e5e9ee; }}
    .metric:last-child {{ border-right: 0; }}
    .label {{ color: #66727f; font-size: 13px; }}
    .value {{ margin-top: 5px; font-size: 21px; font-weight: 700; }}
    .panel {{ overflow: hidden; border: 1px solid #d9e0e7; border-radius: 8px; background: #fff; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 13px 14px; border-bottom: 1px solid #e5e9ee; text-align: left; vertical-align: middle; }}
    th {{ background: #f7f9fb; color: #52606d; font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    .result {{ font-weight: 700; }} .pass {{ color: #16854b; }} .fail {{ color: #c53b36; }}
    .empty {{ padding: 28px; text-align: center; color: #7b8794; }}
    @media (max-width: 700px) {{ .summary {{ grid-template-columns: repeat(2, 1fr); }} .metric:nth-child(2) {{ border-right: 0; }} .panel {{ overflow-x: auto; }} table {{ min-width: 680px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>设备连通性测试报告</h1>
    <div class="summary">
      <div class="metric"><div class="label">主机</div><div class="value">{esc(data.get('hostname', '-'))}</div></div>
      <div class="metric"><div class="label">测试时间</div><div class="value">{esc(data.get('finished_at') or data.get('started_at', '-'))}</div></div>
      <div class="metric"><div class="label">通过</div><div class="value">{esc(summary.get('passed', 0))}</div></div>
      <div class="metric"><div class="label">失败</div><div class="value">{esc(summary.get('failed', 0))}</div></div>
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>设备</th><th>接口</th><th>连接方式</th><th>结果</th><th>测试报告</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </main>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "QLDeviceCheckGeneric/2.0"

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(WEB_INDEX_PATH, "r", encoding="utf-8") as f:
                self._send_html(f.read())
            return
        if path == "/api/health":
            self._send_json({"ok": True})
            return
        if path == "/api/standards":
            self._send_json({"ok": True, "devices": StandardDeviceConfig().devices()})
            return
        if path.startswith("/report/"):
            self._send_report(path)
            return
        if path == "/favicon.ico":
            self._send_empty()
            return
        self.send_error(404, "Not Found")

    def do_POST(self):
        path = urlparse(self.path).path
        handlers = {
            "/api/scan": scan_catalog,
            "/api/scan-interface": scan_interface,
            "/api/run": test_all_discovered,
            "/api/test-device": test_single_device,
        }
        operation = handlers.get(path)
        if operation is None:
            self.send_error(404, "Not Found")
            return
        if not RUN_LOCK.acquire(blocking=False):
            self._send_json({"ok": False, "error": "检测任务正在运行，请稍候"}, status=409)
            return
        try:
            payload = self._read_json()
            self._send_json(operation(payload))
        except Exception as exc:
            self._send_json({
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc().splitlines(),
            }, status=500)
        finally:
            RUN_LOCK.release()

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def _send_html(self, html: str):
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_report(self, path: str):
        relative = unquote(path[len("/report/"):]).replace("\\", "/").strip("/")
        if not relative or ".." in relative.split("/"):
            self.send_error(404, "Not Found")
            return
        if not relative.endswith((".html", ".txt", ".json")):
            self.send_error(404, "Not Found")
            return
        full_path = os.path.abspath(os.path.join(REPORT_DIR, *relative.split("/")))
        report_root = os.path.abspath(REPORT_DIR)
        if not full_path.startswith(report_root + os.sep) or not os.path.isfile(full_path):
            self.send_error(404, "Not Found")
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }
        ext = os.path.splitext(full_path)[1]
        with open(full_path, "rb") as f:
            encoded = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, data: dict, status: int = 200):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_empty(self, status: int = 204):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description="工控机通用测试系统 Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("工控机通用测试系统已启动")
    print(f"访问地址: http://{args.host}:{args.port}")
    print("按 Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
