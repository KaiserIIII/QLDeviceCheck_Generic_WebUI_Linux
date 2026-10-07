"""Review regressions: synthetic configuration, Fake adapters, loopback only."""
import copy
import json
import re
import shutil
import subprocess
import sys
import threading

import pytest

from inspection.adapters import DemoAdapter, LiveAdapter, demo_config
from inspection.comparison import compare_jobs
from inspection.http import make_server
from inspection.reports import export_job
from inspection.service import InspectionService
from test_http import api, requester
from test_workbench import BlockingAdapter, wait_finished


def test_second_process_cannot_recover_or_probe_owned_database(tmp_path):
    adapter = BlockingAdapter()
    path = tmp_path / 'jobs.db'
    svc = InspectionService(path, demo=True, adapter=adapter)
    try:
        job = svc.create({})
        assert adapter.started.wait(1)
        code = """
import sys
from inspection.service import InspectionService
class Forbidden:
    def run(self, *args):
        raise AssertionError('second adapter called')
try:
    svc = InspectionService(sys.argv[1], demo=sys.argv[2] == 'demo', adapter=Forbidden())
except RuntimeError:
    sys.exit(23)
svc.close()
"""
        for mode in ('demo', 'live'):
            result = subprocess.run([sys.executable, '-c', code, str(path), mode], timeout=5)
            assert result.returncode == 23
            assert svc.get(job['id'])['status'] == 'running'
        server = make_server('127.0.0.1', 0, svc)
        try:
            result = subprocess.run([sys.executable, 'web_app.py', '--demo', '--data-dir', str(tmp_path), '--port', str(server.server_port)], capture_output=True, timeout=5)
            assert result.returncode == 1
            assert svc.get(job['id'])['status'] == 'running'
        finally:
            server.server_close()
    finally:
        svc.close()


def test_crashed_owner_releases_lease_and_recovers(tmp_path):
    path = tmp_path / 'jobs.db'
    code = """
import os, sys, threading
from inspection.service import InspectionService
class Blocker:
    def run(self, *args):
        threading.Event().wait(10)
svc=InspectionService(sys.argv[1], demo=True, adapter=Blocker())
job=svc.create({})
print(job['id'], flush=True)
os._exit(0)
"""
    result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, text=True, timeout=5, check=True)
    svc = InspectionService(path, demo=True)
    try:
        assert svc.get(result.stdout.strip())['status'] == 'interrupted'
    finally:
        svc.close()


def test_stubborn_worker_keeps_database_owned_after_bounded_close(tmp_path):
    class Stubborn(BlockingAdapter):
        def run(self, *args):
            self.started.set()
            self.release.wait(8)
    adapter = Stubborn()
    path = tmp_path / 'jobs.db'
    svc = InspectionService(path, demo=True, adapter=adapter)
    svc.create({})
    assert adapter.started.wait(1)
    svc.close()
    try:
        with pytest.raises(RuntimeError):
            InspectionService(path, demo=True)
    finally:
        adapter.release.set()
        svc.worker.join(2)
    other = InspectionService(path, demo=True)
    other.close()


@pytest.mark.parametrize('field,value,reason', [('station_id', 'UNIT-B', 'Station'), ('station_id', '', 'identity'), ('station_id', '  ', 'identity'), ('batch', 'LOT-B', 'Batch')])
def test_recovery_requires_same_unambiguous_identity(tmp_path, field, value, reason):
    svc = InspectionService(tmp_path / 'jobs.db', demo=True)
    try:
        baseline = wait_finished(svc, svc.create({'scenario': 'faults', 'station_id': 'UNIT-A', 'batch': 'LOT-A'})['id'])
        payload = {'station_id': 'UNIT-A', 'batch': 'LOT-A', field: value}
        current = wait_finished(svc, svc.create(payload)['id'])
        diff = compare_jobs(baseline, current)
        assert not diff['comparable'] and not diff['whole_unit_recovered']
        assert any(reason.lower() in r.lower() for r in diff['reasons'])
        assert diff['recovered'] == ['DEMO_PLC']
    finally:
        svc.close()


def test_same_identity_full_rerun_and_empty_batch_can_recover(tmp_path):
    svc = InspectionService(tmp_path / 'jobs.db', demo=True)
    try:
        baseline = wait_finished(svc, svc.create({'scenario': 'faults', 'station_id': 'UNIT-A'})['id'])
        current = wait_finished(svc, svc.create({'station_id': 'UNIT-A'})['id'])
        assert compare_jobs(baseline, current)['whole_unit_recovered']
        missing = copy.deepcopy(current)
        missing['metadata']['station_id'] = ''
        baseline['metadata']['station_id'] = ''
        assert not compare_jobs(baseline, missing)['comparable']
    finally:
        svc.close()


@pytest.mark.parametrize('scenario', ['healthy', 'faults', 'timeout', 'crc', 'missing'])
def test_demo_scenario_outcomes_do_not_change_with_selected_subset(scenario):
    full = []
    adapter = DemoAdapter()
    config = demo_config()
    adapter.run(config, ['DEMO_RELAY', 'DEMO_PLC', 'DEMO_PCI'], scenario, lambda: False, full.append)
    for expected in full:
        subset = []
        adapter.run(config, [expected['device_id']], scenario, lambda: False, subset.append)
        assert subset[0] == expected


def test_same_scenario_retest_remains_failed(tmp_path):
    svc = InspectionService(tmp_path / 'jobs.db', demo=True)
    try:
        first = wait_finished(svc, svc.create({'scenario': 'faults', 'station_id': 'UNIT-A'})['id'])
        second = wait_finished(svc, svc.retest(first['id'], {})['id'])
        assert second['results'][0]['verdict'] == 'FAIL'
        third = wait_finished(svc, svc.retest(second['id'], {'scenario': 'healthy'})['id'])
        assert third['results'][0]['verdict'] == 'PASS'
    finally:
        svc.close()


@pytest.mark.parametrize('statuses', [('有响应', '有响应'), ('异常', '有响应'), ('有响应', '异常')])
def test_all_probe_and_parent_evidence_survives_storage_and_exports(tmp_path, monkeypatch, statuses):
    import core.generic_detector
    config = demo_config()
    parent = config['devices'][1]
    child = copy.deepcopy(config['devices'][0])
    child['device_id'] = 'CHILD'
    parent['child_devices'] = [child]
    probes = [{'device_id': 'CHILD', 'status': status, 'request': 'request%d <script>' % i, 'response': 'response%d' % i, 'duration_ms': i, 'interface': 'FAKE'} for i, status in enumerate(statuses, 1)]
    class Detector:
        def __init__(self, **kwargs):
            self.config = kwargs['config']
        def run(self):
            return self
        def to_dict(self):
            return {'devices': [{'device_id': 'DEMO_PLC', 'status': '正常', 'evidence': 'carrier', 'request': 'carrier-read', 'response': 'carrier-response'}, {'device_id': 'CHILD', 'status': '正常' if statuses[-1] == '有响应' else '异常', 'request': 'request2', 'response': 'response2'}], 'connection_tests': [dict(probes[0], device_id='DEMO_PLC', request='carrier-probe'), *probes]}
    monkeypatch.setattr(core.generic_detector, 'GenericDetector', Detector)
    path = tmp_path / 'jobs.db'
    svc = InspectionService(path, adapter=LiveAdapter())
    job = wait_finished(svc, svc.create({'device_ids': ['CHILD']}, snapshot=config)['id'])
    svc.close()
    svc = InspectionService(path, demo=True)
    try:
        job = svc.get(job['id'])
        assert [r['device_id'] for r in job['results']] == ['CHILD']
        result = job['results'][0]
        assert len(result['attempts']) == 2
        assert result['attempts'][0]['request'] == 'request1 <script>'
        assert result['supporting_checks'][0]['device_id'] == 'DEMO_PLC'
        assert result['supporting_checks'][0]['attempts'][0]['request'] == 'carrier-probe'
        for fmt in ('json', 'csv', 'html'):
            report = export_job(job, fmt)[0].decode('utf-8-sig')
            assert 'response1' in report and 'carrier-read' in report and 'carrier-probe' in report
            if fmt == 'html':
                assert '<script>' not in report and '&lt;script&gt;' in report
    finally:
        svc.close()


def test_default_and_demo_results_have_stable_evidence_arrays(tmp_path):
    svc = InspectionService(tmp_path / 'jobs.db', demo=True)
    try:
        job = svc.create({})
        for record in (job, wait_finished(svc, job['id'])):
            assert all(isinstance(r['attempts'], list) and isinstance(r['supporting_checks'], list) for r in record['results'])
    finally:
        svc.close()


def test_legacy_operations_use_injected_config_and_invalidate_scan_cache(tmp_path, monkeypatch):
    import legacy_web
    config = demo_config()
    config['settings']['max_report_count'] = 7
    config['devices'][1]['child_devices'] = [{'device_id': 'CHILD', 'device_name': 'child', 'device_type': 'plc', 'connection_type': 'network', 'protocol': {'type': 'modbus_tcp'}}]
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    observed = []
    class Detector:
        def __init__(self, **kwargs):
            # A missing injection is deliberately recorded without opening anything.
            self.config = kwargs.get('config')
            observed.append(copy.deepcopy(self.config.data) if self.config else None)
        def run(self):
            return self
        def to_dict(self):
            devices = self.config.all_configured_devices() if self.config else []
            return {'devices': [dict(d, status='正常', name=d['device_name'], interface='PINNED') for d in devices], 'summary': {}, 'logs': []}
    monkeypatch.setattr(legacy_web, 'GenericDetector', Detector)
    report_limits = []
    monkeypatch.setattr(legacy_web, 'save_report', lambda result, limit: report_limits.append(limit))
    svc = InspectionService(tmp_path / 'jobs.db', config_path=str(path))
    server = make_server('127.0.0.1', 0, svc, token='secret')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = requester(server)
    auth = {'Authorization': 'Bearer secret'}
    try:
        assert request('GET', '/api/standards', headers=auth)[1]['devices'][0]['device_id'] == 'DEMO_RELAY'
        for token in ('', 'wrong'):
            assert request('POST', '/api/scan', {}, {'Authorization': 'Bearer ' + token})[0] == 401
        assert request('POST', '/api/scan', {}, auth)[0] == 200
        assert observed[-1] == config
        status, body = request('POST', '/api/test-device', {'device_id': 'DEMO_PLC'}, auth)
        assert status == 200 and body['device']['device_id'] == 'DEMO_PLC'
        assert [d['device_id'] for d in observed[-1]['devices']] == ['DEMO_PLC']
        assert observed[-1]['devices'][0]['interface'] == 'PINNED'
        assert request('POST', '/api/test-device', {'device_id': 'CHILD'}, auth)[0] == 200
        assert [d['device_id'] for d in observed[-1]['devices']] == ['DEMO_PLC']
        assert [d['device_id'] for d in observed[-1]['devices'][0]['child_devices']] == ['CHILD']
        assert observed[-1]['devices'][0]['interface'] == 'PINNED'
        assert request('POST', '/api/scan-interface', {'interface_type': 'network', 'interface': 'PINNED'}, auth)[0] == 200
        assert [d['device_id'] for d in observed[-1]['devices']] == ['DEMO_PLC']
        assert request('POST', '/api/run', {}, auth)[0] == 200
        assert report_limits == [7]
        assert all(c is not None for c in observed)
        config['description'] = 'new revision'
        path.write_text(json.dumps(config), encoding='utf-8')
        count = len(observed)
        assert request('POST', '/api/test-device', {'device_id': 'DEMO_PLC'}, auth)[0] == 400
        assert len(observed) == count
    finally:
        server.shutdown()
        server.server_close()
        svc.close()
        thread.join(2)


@pytest.mark.parametrize('key,value', [('devices', None), ('devices', [None]), ('devices', [{'child_devices': [None]}]), ('settings', []), ('settings', 'bad')])
def test_nested_malformed_configuration_returns_validation_error(api, key, value):
    svc, server, request = api
    config = demo_config()
    config[key] = value
    assert request('POST', '/api/config/validate', {'config': config})[0] == 400


def test_legacy_page_helper_uses_session_bearer_for_actual_http(api, monkeypatch):
    import legacy_web
    svc, server, request = api
    svc.demo = False
    svc.config_path = None
    monkeypatch.setattr(svc, '_snapshot', demo_config)
    monkeypatch.setattr(legacy_web, 'scan_catalog', lambda *args, **kwargs: {'ok': True})
    monkeypatch.setattr(legacy_web, 'test_single_device', lambda *args, **kwargs: {'ok': True})
    server.access_token = 'session-secret'
    script = re.search(r'<script>([\s\S]*?)</script>', request('GET', '/legacy')[1].decode()).group(1)
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required to execute legacy page JavaScript')
    code = """
const vm = require('node:vm');
const script = JSON.parse(process.argv[1]);
const base = process.argv[2];
const values = new Map([['qldc.token', 'session-secret']]);
const element = {addEventListener(){}, querySelectorAll(){return []}};
const document = {getElementById(){return element}, addEventListener(){}};
const context = {document, sessionStorage:{getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)}, fetch:(url,opts)=>{if(url.includes('secret'))throw Error('credential in URL');return fetch(base+url,opts)}};
vm.createContext(context);
vm.runInContext(script, context);
(async()=>{
  for(const url of ['/api/scan','/api/test-device'])await vm.runInContext(`postJson('${url}', {device_id:'FAKE'})`,context);
  values.set('qldc.token','wrong');
  let rejected=false;
  try{await vm.runInContext("postJson('/api/scan')",context)}catch{rejected=true}
  if(!rejected)throw Error('wrong token accepted');
})().catch(e=>{console.error(e.message);process.exitCode=1});
"""
    result = subprocess.run([node, '-e', code, json.dumps(script), 'http://127.0.0.1:%d' % server.server_port], capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr


def test_history_sort_has_stable_ties_and_pagination_over_http(api):
    svc, server, request = api
    jobs = [wait_finished(svc, svc.create({'batch': 'sort-fixture'})['id']) for _ in range(3)]
    # Equal timestamps exercise rowid tie ordering after persisted reopen.
    for job in jobs:
        svc.store.connection.execute('UPDATE jobs SET created_at=? WHERE id=?', ('2026-10-07T00:00:00.000Z', job['id']))
    svc.store.connection.commit()
    from inspection.store import SQLiteStore
    db_path = svc.store.connection.execute('PRAGMA database_list').fetchone()[2]
    svc.store.close()
    svc.store = SQLiteStore(db_path)
    ids = [job['id'] for job in jobs]
    for sort, expected in [('oldest', ids), ('newest', list(reversed(ids)))]:
        got = [request('GET', '/api/jobs?sort=%s&limit=1&offset=%d&q=sort-fixture' % (sort, offset))[1]['jobs'][0]['id'] for offset in range(3)]
        assert got == expected
        assert svc.list_jobs(sort=sort)['jobs'][0]['id'] == expected[0]
    assert request('GET', '/api/jobs')[1]['jobs'][0]['id'] == ids[-1]
    assert request('GET', '/api/jobs?sort=arbitrary')[0] == 400
    with pytest.raises(ValueError):
        svc.list_jobs(sort='arbitrary')


def test_core_tags_every_probe_at_exact_parent_child_boundaries(monkeypatch):
    from core.config_manager import StandardDeviceConfig
    from core.generic_detector import GenericDetector
    config = demo_config()
    config['devices'] = [config['devices'][1]]
    config['devices'][0]['child_devices'] = [{'device_id': 'CHILD', 'device_name': 'child', 'device_type': 'plc', 'connection_type': 'network', 'protocol': {'type': 'modbus_tcp'}}]
    detector = GenericDetector(config=StandardDeviceConfig(data=config))
    monkeypatch.setattr(detector, '_configured_interface_items', lambda raw: ([], [], []))
    def device(raw, phase):
        detector.connection_tests.extend([{'request': 'first', 'status': '异常'}, {'request': 'second', 'status': '有响应'}])
        return {'device_id': raw['device_id'], 'status': '正常'}
    def child(raw, parent, result):
        detector.connection_tests.append({'request': 'child', 'status': '有响应'})
        return {'device_id': raw['device_id'], 'status': '正常'}
    monkeypatch.setattr(detector, '_test_configured_device', device)
    monkeypatch.setattr(detector, '_test_configured_child_device', child)
    raw = detector.run().to_dict()
    assert [probe['device_id'] for probe in raw['connection_tests']] == ['DEMO_PLC', 'DEMO_PLC', 'CHILD']
    assert raw['connection_tests'][2]['parent_device_id'] == 'DEMO_PLC'


def test_inflight_legacy_operation_keeps_lease_until_it_returns(tmp_path):
    path = tmp_path / 'jobs.db'
    svc = InspectionService(path, demo=True)
    started, release = threading.Event(), threading.Event()
    def operation():
        with svc.legacy_operation():
            started.set()
            release.wait(3)
    thread = threading.Thread(target=operation)
    thread.start()
    assert started.wait(1)
    svc.close()
    try:
        with pytest.raises(RuntimeError):
            InspectionService(path, demo=True)
    finally:
        release.set()
        thread.join(1)
    svc = InspectionService(path, demo=True)
    svc.close()


def test_legacy_operation_releases_lock_before_response_is_sent(api, monkeypatch):
    import legacy_web
    from inspection.http import Handler
    svc, server, request = api
    svc.demo = False
    monkeypatch.setattr(svc, '_snapshot', demo_config)
    monkeypatch.setattr(legacy_web, 'scan_catalog', lambda *args, **kwargs: {'ok': True, 'operation_done': True})
    original_json = Handler._json
    def checked_json(handler, data, status=200):
        if data.get('operation_done'):
            assert not svc.hardware_lock.locked(), 'response sent while operation lock held'
        return original_json(handler, data, status)
    monkeypatch.setattr(Handler, '_json', checked_json)
    status, body = request('POST', '/api/scan', {})
    assert status == 200 and body['operation_done']
