from .domain import catalog_devices


def compare_jobs(baseline, current):
    left = {r['device_id']: r for r in baseline['results']}
    right = {r['device_id']: r for r in current['results']}
    same_scope = set(baseline['device_ids']) == set(current['device_ids'])
    related_subset = current.get('parent_job_id') == baseline['id'] and set(current['device_ids']).issubset(baseline['device_ids'])
    configured = {device['device_id'] for device in catalog_devices(current['config_snapshot'])}
    partial = set(current['device_ids']) != configured
    reasons = []
    a_identity, b_identity = baseline.get('metadata', {}), current.get('metadata', {})
    a_station, b_station = a_identity.get('station_id', ''), b_identity.get('station_id', '')
    if not a_station.strip() or not b_station.strip():
        reasons.append('Missing station/unit identity')
    elif a_station != b_station:
        reasons.append('Station/unit identity mismatch')
    if a_identity.get('batch', '') != b_identity.get('batch', ''):
        reasons.append('Batch mismatch')
    if baseline['mode'] != current['mode']:
        reasons.append('Mode mismatch')
    if baseline['config_hash'] != current['config_hash']:
        reasons.append('Configuration mismatch')
    if not same_scope and not related_subset:
        reasons.append('Selected scope mismatch')
    if any(left[i]['scope'] != right[i]['scope'] for i in left.keys() & right.keys()):
        reasons.append('Result scope mismatch')
    if baseline['status'] not in ('completed', 'cancelled', 'failed', 'interrupted') or current['status'] not in ('completed', 'cancelled', 'failed', 'interrupted'):
        reasons.append('Jobs are not finished')
    output = {'baseline_id': baseline['id'], 'current_id': current['id'], 'comparable': not reasons, 'reasons': reasons, 'partial_scope': partial,
              'scope': 'shared_devices' if partial else 'full_selection', 'whole_unit_recovered': False,
              'added': sorted(right.keys() - left.keys()), 'removed': sorted(left.keys() - right.keys()), 'recovered': [], 'regressed': [], 'still_failed': [], 'changed': [], 'unchanged': []}
    for device_id in sorted(left.keys() & right.keys()):
        a, b = left[device_id]['verdict'], right[device_id]['verdict']
        key = 'recovered' if a != 'PASS' and b == 'PASS' else 'regressed' if a == 'PASS' and b != 'PASS' else 'still_failed' if a == b == 'FAIL' else 'unchanged' if a == b else 'changed'
        output[key].append(device_id)
    output['whole_unit_recovered'] = not reasons and not partial and bool(output['recovered']) and current['summary']['verdict'] == 'PASS'
    return output
