import copy
import time

from .domain import catalog_devices, ensure_read_only, initial_result


def demo_config():
    return {'schema_version': 2, 'workstation_id': 'DEMO-01', 'description': 'SIMULATED acceptance catalog',
            'settings': {'allow_write_tests': False}, 'devices': [
                {'device_id': 'DEMO_RELAY', 'device_name': '透明 IO 继电器通信路径', 'device_type': 'relay', 'connection_type': 'serial', 'interface': 'SIM-COM1', 'serial_config': {'baudrate': 9600}, 'protocol': {'type': 'modbus_rtu', 'function_code': 3, 'address': 0, 'count': 2}, 'test_scope': 'communication_path'},
                {'device_id': 'DEMO_PLC', 'device_name': 'PLC 通信寄存器', 'device_type': 'plc', 'connection_type': 'network', 'interface': 'SIM-ETH0', 'network_config': {'ip': '127.0.0.1', 'port': 502}, 'protocol': {'type': 'modbus_tcp', 'function_code': 3, 'address': 0, 'count': 2}},
                {'device_id': 'DEMO_PCI', 'device_name': 'PCI 通信卡被动状态', 'device_type': 'pci', 'connection_type': 'pci', 'pci_slot': 'SIM-PCI0', 'pci_match': {'vendor': '1234'}}]}


class DemoAdapter:
    def run(self, config, device_ids, scenario, cancelled, on_result):
        for index, device in enumerate(catalog_devices(config)):
            if device['device_id'] not in device_ids:
                continue
            if cancelled():
                return
            time.sleep(.03)
            if cancelled():
                return
            fault = scenario if scenario in ('timeout', 'crc', 'missing') and index == 0 else 'timeout' if scenario == 'faults' and index == 1 else ''
            result = initial_result(device, True)
            result.update(verdict='FAIL' if fault else 'PASS', fault_code=fault.upper(),
                          summary='SIMULATED ' + (fault or ('PCI passive state observed' if device['scope'] == 'pci_passive' else 'Communication path responded')),
                          suggestion='Check configured carrier, cabling and protocol parameters; verify onsite' if fault else 'Business and electrical function require separate verification',
                          request='SIMULATED READ 01 03 00 00 00 02', response='' if fault in ('timeout', 'missing') else 'SIMULATED CRC ERROR' if fault else 'SIMULATED 01 03 04 00 00 00 00', duration_ms=30)
            on_result(result)


def normalize_result(device, raw):
    result = initial_result(device, False)
    if raw is None:
        result.update(verdict='FAIL', fault_code='MISSING', summary='Expected device did not produce a result', suggestion='Check carrier availability and configuration')
        return result
    evidence = str(raw.get('evidence') or raw.get('summary') or '')
    status = raw.get('status')
    verdict = 'PASS' if status in ('正常', '在线', '已发现') else 'REVIEW' if status == '需确认' else 'FAIL'
    lower = evidence.lower()
    fault = '' if verdict == 'PASS' else 'TIMEOUT' if 'timeout' in lower or 'timed out' in lower or '超时' in evidence or '无响应' in evidence else 'CRC' if 'crc' in lower else 'CARRIER' if '中间模块' in evidence else 'RESPONSE_MISMATCH' if raw.get('response') else 'UNAVAILABLE'
    result.update(verdict=verdict, fault_code=fault, interface=raw.get('interface') or device['interface'], summary=evidence,
                  suggestion='Check cabling, carrier and configured protocol; this is a rule-based suggestion' if fault else 'This result covers communication or passive state only; verify business function separately',
                  request=str(raw.get('request') or ''), response=str(raw.get('response') or ''), duration_ms=max(0, float(raw.get('duration_ms') or 0)))
    return result


class LiveAdapter:
    def run(self, config, device_ids, scenario, cancelled, on_result):
        from core.config_manager import StandardDeviceConfig
        from core.generic_detector import GenericDetector
        ensure_read_only(config)
        devices = {d['device_id']: d for d in catalog_devices(config)}
        for parent in config['devices']:
            group_ids = [parent['device_id']] + [d['device_id'] for d in parent.get('child_devices', [])]
            chosen = [d for d in group_ids if d in device_ids]
            if not chosen:
                continue
            if cancelled():
                return
            # selected() retains the parent carrier for selected children.
            selected = StandardDeviceConfig(data=config).selected(chosen)
            selected.data.setdefault('settings', {})['allow_write_tests'] = False
            raw = GenericDetector(config=selected, serial_probe=True, subnet_probe=False).run().to_dict()
            index = {r.get('device_id'): r for r in raw.get('devices', [])}
            checks = raw.get('connection_tests', []) or []
            def evidence(device_id):
                result = normalize_result(devices[device_id], index.get(device_id))
                result['attempts'] = copy.deepcopy([check for check in checks if check.get('device_id') == device_id])
                return result
            for device_id in chosen:
                result = evidence(device_id)
                if device_id != parent['device_id']:
                    result['supporting_checks'] = [evidence(parent['device_id'])]
                on_result(result)
