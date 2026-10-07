import copy
import hashlib
import json
from datetime import datetime, timezone

STATUSES = {'queued', 'running', 'cancelling', 'completed', 'cancelled', 'failed', 'interrupted'}
TERMINAL = {'completed', 'cancelled', 'failed', 'interrupted'}
SCENARIOS = ['healthy', 'faults', 'timeout', 'crc', 'missing']
VERDICTS = {'PASS', 'FAIL', 'REVIEW', 'NOT_RUN'}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def config_hash(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def catalog_devices(snapshot):
    # Configuration validation is pure computation; detector imports stay in LiveAdapter.
    from core.config_manager import StandardDeviceConfig
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('settings', {}), dict):
        raise ValueError('Invalid configuration object/settings')
    try:
        configured = StandardDeviceConfig(data=snapshot).all_configured_devices()
    except (AttributeError, TypeError):
        raise ValueError('Invalid nested configuration shape') from None
    result = []
    for device in configured:
        scope = 'pci_passive' if device['connection_type'] == 'pci' else 'communication_path'
        result.append({'device_id': device['device_id'], 'name': device['device_name'],
                       'connection_type': device['connection_type'],
                       'interface': device.get('interface') or device.get('pci_slot') or '',
                       'protocol': (device.get('protocol') or {}).get('type', ''), 'scope': scope})
    return result


def ensure_read_only(snapshot):
    def visit(value):
        if isinstance(value, dict):
            if str(value.get('operation', '')).lower() == 'write':
                raise ValueError('Acceptance rejects write-labelled operations')
            if 'function_code' in value:
                code = value['function_code']
                try:
                    number = int(code, 0) if isinstance(code, str) else int(code)
                except (TypeError, ValueError):
                    raise ValueError('Invalid Modbus function code') from None
                if number not in (1, 2, 3, 4):
                    raise ValueError('Acceptance rejects Modbus write operations')
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(snapshot)


def validate_payload(payload, devices, mode):
    if not isinstance(payload, dict):
        raise ValueError('Expected JSON object')
    allowed = {'scenario', 'station_id', 'batch', 'operator', 'notes', 'device_ids', 'metadata'}
    if set(payload) - allowed:
        raise ValueError('Unknown job field')
    scenario = payload.get('scenario', 'healthy')
    if scenario not in SCENARIOS or (mode == 'live' and scenario != 'healthy'):
        raise ValueError('Invalid scenario for this mode')
    metadata = payload.get('metadata', {})
    if not isinstance(metadata, dict) or set(metadata) - {'station_id', 'batch', 'operator', 'notes'}:
        raise ValueError('Invalid metadata')
    metadata = {key: payload.get(key, metadata.get(key, '')) for key in ('station_id', 'batch', 'operator', 'notes')}
    for key, value in metadata.items():
        if not isinstance(value, str) or len(value) > (2000 if key == 'notes' else 128):
            raise ValueError('Invalid metadata length or type')
    ids = payload.get('device_ids', [d['device_id'] for d in devices])
    known = {d['device_id'] for d in devices}
    if not isinstance(ids, list) or not ids or len(ids) > 1000 or any(not isinstance(i, str) or i not in known for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Invalid device selection')
    selected = [d['device_id'] for d in devices if d['device_id'] in ids]
    return scenario, metadata, selected


def initial_result(device, simulated):
    return dict(copy.deepcopy(device), verdict='NOT_RUN', fault_code='NOT_RUN', summary='Not executed',
                suggestion='Run acceptance for this device', request='', response='', duration_ms=0, simulated=simulated,
                attempts=[], supporting_checks=[])


def summarize(results):
    counts = {v: sum(r['verdict'] == v for r in results) for v in VERDICTS}
    verdict = 'FAIL' if counts['FAIL'] else 'REVIEW' if counts['REVIEW'] else 'NOT_RUN' if counts['NOT_RUN'] or not results else 'PASS'
    return {'expected': len(results), 'passed': counts['PASS'], 'failed': counts['FAIL'], 'review': counts['REVIEW'], 'not_run': counts['NOT_RUN'], 'verdict': verdict}


def refresh(job):
    job['summary'] = summarize(job['results'])
    if job['status'] in ('failed', 'cancelled', 'interrupted') and job['summary']['verdict'] == 'PASS':
        job['summary']['verdict'] = 'REVIEW'
    job['progress'] = {'completed': sum(r['verdict'] != 'NOT_RUN' for r in job['results']), 'total': len(job['results'])}
    return job
