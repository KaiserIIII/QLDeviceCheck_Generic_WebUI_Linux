import copy
import json
import threading
import time

import pytest

from inspection.service import InspectionService
from inspection.store import SQLiteStore
from inspection.comparison import compare_jobs
from inspection.reports import export_job


TERMINAL = {'completed', 'cancelled', 'failed', 'interrupted'}


def wait_finished(service, job_id):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        job = service.get(job_id)
        if job['status'] in TERMINAL:
            return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


@pytest.fixture
def service(tmp_path):
    value = InspectionService(tmp_path / 'jobs.db', demo=True)
    yield value
    value.close()


def test_missing_device_cannot_pass(service):
    job = service.create({'scenario': 'missing', 'station_id': 'DEMO-01'})
    assert all(r['verdict'] == 'NOT_RUN' for r in job['results'])
    done = wait_finished(service, job['id'])
    assert done['summary']['failed'] >= 1
    assert len(done['results']) == done['summary']['expected']
    assert done['summary']['verdict'] != 'PASS'
    assert all(r['simulated'] for r in done['results'])


def test_snapshot_and_retest_scope(service):
    job = wait_finished(service, service.create({'scenario': 'faults'})['id'])
    broken = [r['device_id'] for r in job['results'] if r['verdict'] != 'PASS']
    assert broken and len(broken) < len(job['results'])
    with pytest.raises(ValueError):
        service.retest(job['id'], {'device_ids': job['device_ids']})
    child = wait_finished(service, service.retest(job['id'], {'scenario': 'healthy', 'notes': 'fixed'})['id'])
    assert child['device_ids'] == broken
    assert child['config_snapshot'] == job['config_snapshot']
    assert child['config_hash'] == job['config_hash']
    assert child['parent_job_id'] == job['id']
    assert child['summary']['verdict'] == 'PASS'
    comparison = compare_jobs(job, child)
    assert comparison['comparable'] and comparison['partial_scope']
    assert not comparison['whole_unit_recovered']
    assert len(comparison['recovered']) == len(broken)
    other = copy.deepcopy(child)
    other['mode'] = 'live'
    assert not compare_jobs(job, other)['comparable']
    other = copy.deepcopy(job)
    other['device_ids'] = job['device_ids'][:1]
    assert not compare_jobs(job, other)['comparable']


class BlockingAdapter:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.snapshot = None

    def run(self, config, device_ids, scenario, cancelled, on_result):
        self.snapshot = copy.deepcopy(config)
        self.started.set()
        while not self.release.wait(.01) and not cancelled():
            pass


def test_contention_cancel_and_shared_lock(tmp_path):
    adapter = BlockingAdapter()
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    try:
        svc.hardware_lock.acquire()
        with pytest.raises(RuntimeError):
            svc.create({})
        svc.hardware_lock.release()
        job = svc.create({})
        assert adapter.started.wait(1)
        job['config_snapshot']['devices'][0]['device_name'] = 'changed outside'
        assert adapter.snapshot['devices'][0]['device_name'] != 'changed outside'
        with pytest.raises(RuntimeError):
            svc.create({})
        svc.cancel(job['id'])
        done = wait_finished(svc, job['id'])
        assert done['status'] == 'cancelled'
        assert done['summary']['not_run'] == done['summary']['expected']
        assert done['summary']['verdict'] != 'PASS'
        assert done['finished_at']
        assert done['events'][-1]['type'] == 'cancelled'
    finally:
        adapter.release.set()
        svc.close()


def test_restart_marks_pending_interrupted(tmp_path, service):
    job = wait_finished(service, service.create({})['id'])
    job['status'] = 'running'
    db = tmp_path / 'other.db'
    store = SQLiteStore(db)
    store.insert(job)
    store.close()
    recovered = InspectionService(db, demo=True)
    try:
        result = recovered.get(job['id'])
        assert result['status'] == 'interrupted'
        assert result['events'][-1]['type'] == 'interrupted'
    finally:
        recovered.close()


def test_reports_preserve_evidence_and_escape(service):
    job = wait_finished(service, service.create({'notes': '<script>x</script>', 'operator': '=CMD()'})['id'])
    html, mime = export_job(job, 'html')
    assert '<script>x</script>' not in html.decode()
    assert '&lt;script&gt;' in html.decode()
    assert 'SIMULATED' in html.decode()
    csv, mime = export_job(job, 'csv')
    assert "'=CMD()" in csv.decode('utf-8-sig')
    raw, mime = export_job(job, 'json')
    assert json.loads(raw)['config_snapshot'] == job['config_snapshot']
    assert json.loads(raw)['results'][0]['response']


def test_html_report_is_readable_and_declares_partial_scope(service):
    job = wait_finished(service, service.create({'station_id': 'DEMO-UNIT-7', 'batch': 'B7', 'device_ids': ['DEMO_RELAY']})['id'])
    content, mime = export_job(job, 'html')
    report = content.decode('utf-8')
    assert '<table' in report and '<th' in report
    assert 'DEMO-UNIT-7' in report and 'B7' in report
    assert '仅选定设备' in report and '1 / 3' in report
    assert job['config_hash'] in report
    assert '请求' in report and '响应' in report
    assert '@media print' in report
    assert 'SIMULATED' in report and '真实设备验收凭据' in report


def test_validation_history_insights(service):
    for payload in ({'scenario': 'bad'}, {'device_ids': []}, {'device_ids': ['unknown']}, {'notes': 'x' * 2001}):
        with pytest.raises(ValueError):
            service.create(payload)
    job = wait_finished(service, service.create({'batch': 'batch-find'})['id'])
    assert service.list_jobs(query='batch-find')['total'] == 1
    assert service.list_jobs(status='completed', limit=1)['jobs'][0]['id'] == job['id']
    assert service.insights()['demo']['completed'] == 1
    assert service.insights()['live']['total'] == 0


def test_live_adapter_group_selection_and_missing(monkeypatch):
    from inspection.adapters import LiveAdapter, demo_config
    import core.generic_detector
    seen = []
    class FakeDetector:
        def __init__(self, **kwargs):
            seen.append(kwargs['config'].data)
        def run(self):
            return self
        def to_dict(self):
            return {'devices': []}
    monkeypatch.setattr(core.generic_detector, 'GenericDetector', FakeDetector)
    config = demo_config()
    config['devices'][1]['child_devices'] = [copy.deepcopy(config['devices'][0])]
    config['devices'][1]['child_devices'][0]['device_id'] = 'child'
    results = []
    LiveAdapter().run(config, ['child'], 'healthy', lambda: False, results.append)
    assert len(seen) == 1 and seen[0]['devices'][0]['device_id'] == config['devices'][1]['device_id']
    assert [r['device_id'] for r in results] == ['child']
    assert results[0]['verdict'] == 'FAIL'


def test_acceptance_rejects_write_before_adapter(service):
    config = service.catalog()['config_snapshot']
    config['settings']['allow_write_tests'] = True
    config['devices'][0]['protocol']['operation'] = 'write'
    with pytest.raises(ValueError, match='write'):
        service.create({}, snapshot=config)


def test_shutdown_is_bounded_and_does_not_record_late_pass(tmp_path):
    class StubbornAdapter(BlockingAdapter):
        def run(self, config, device_ids, scenario, cancelled, on_result):
            from inspection.adapters import DemoAdapter
            self.started.set()
            self.release.wait(5)
            DemoAdapter().run(config, device_ids, scenario, lambda: False, on_result)
    adapter = StubbornAdapter()
    path = tmp_path / 'shutdown.db'
    svc = InspectionService(path, demo=True, adapter=adapter)
    job = svc.create({})
    assert adapter.started.wait(1)
    start = time.monotonic()
    svc.close()
    assert time.monotonic() - start < 2.6
    adapter.release.set()
    svc.worker.join(1)
    reopened = InspectionService(path, demo=True)
    try:
        saved = reopened.get(job['id'])
        assert saved['status'] == 'interrupted'
        assert saved['summary']['not_run'] == saved['summary']['expected']
        assert saved['duration_ms'] >= 1900
    finally:
        reopened.close()


def test_simultaneous_creation_has_one_owner(tmp_path):
    adapter = BlockingAdapter()
    svc = InspectionService(tmp_path / 'race.db', demo=True, adapter=adapter)
    barrier = threading.Barrier(6)
    outcomes = []
    def run():
        barrier.wait()
        try:
            outcomes.append(svc.create({})['id'])
        except RuntimeError:
            outcomes.append('conflict')
    threads = [threading.Thread(target=run) for _ in range(6)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        assert len(outcomes) == 6 and outcomes.count('conflict') == 5
        assert svc.list_jobs()['total'] == 1
    finally:
        svc.close()


def test_adapter_failure_cannot_leave_pass_verdict(tmp_path):
    class FailsAfterResults:
        def run(self, config, device_ids, scenario, cancelled, on_result):
            from inspection.adapters import DemoAdapter
            DemoAdapter().run(config, device_ids, scenario, cancelled, on_result)
            raise RuntimeError('private traceback content')
    svc = InspectionService(tmp_path / 'failed.db', demo=True, adapter=FailsAfterResults())
    try:
        done = wait_finished(svc, svc.create({})['id'])
        assert done['status'] == 'failed'
        assert done['summary']['verdict'] != 'PASS'
        assert 'private traceback' not in json.dumps(done)
    finally:
        svc.close()


def test_duplicate_retest_input_is_validation_error(service):
    job = wait_finished(service, service.create({'scenario': 'faults'})['id'])
    with pytest.raises(ValueError):
        service.retest(job['id'], {'device_ids': [{'bad': 'object'}]})


def test_failed_only_retest_never_claims_whole_unit_recovery(service):
    original = wait_finished(service, service.create({'scenario': 'missing', 'device_ids': ['DEMO_RELAY']})['id'])
    child = wait_finished(service, service.retest(original['id'], {'scenario': 'healthy'})['id'])
    comparison = compare_jobs(original, child)
    assert comparison['comparable'] and comparison['partial_scope']
    assert not comparison['whole_unit_recovered']


def test_real_config_snapshot_survives_file_change(tmp_path):
    from inspection.adapters import demo_config, DemoAdapter
    path = tmp_path / 'config.json'
    snapshot = demo_config()
    path.write_text(json.dumps(snapshot), encoding='utf-8')
    blocker = BlockingAdapter()
    svc = InspectionService(tmp_path / 'live.db', config_path=str(path), adapter=blocker)
    try:
        job = svc.create({})
        assert blocker.started.wait(1)
        snapshot['devices'][0]['device_name'] = 'new name'
        path.write_text(json.dumps(snapshot), encoding='utf-8')
        assert svc.catalog()['config_hash'] != job['config_hash']
        assert svc.get(job['id'])['config_snapshot']['devices'][0]['device_name'] != 'new name'
        svc.cancel(job['id'])
        original = wait_finished(svc, job['id'])
        svc.adapter = DemoAdapter()
        child = wait_finished(svc, svc.retest(original['id'], {})['id'])
        assert child['config_snapshot'] == original['config_snapshot']
        assert child['config_hash'] == original['config_hash']
        assert all(not r['simulated'] for r in child['results'])
    finally:
        svc.close()


def test_live_missing_and_progress_at_group_boundaries(monkeypatch):
    from inspection.adapters import LiveAdapter, demo_config
    import core.generic_detector
    seen = []
    results = []
    stop = threading.Event()
    class FakeDetector:
        def __init__(self, **kwargs):
            self.config = kwargs['config']
            seen.append(self.config.data)
        def run(self):
            return self
        def to_dict(self):
            raw = self.config.raw_devices()[0]
            return {'devices': [{'device_id': raw['device_id'], 'status': '正常', 'evidence': 'passive evidence', 'request': 'read', 'response': 'response'}]}
    monkeypatch.setattr(core.generic_detector, 'GenericDetector', FakeDetector)
    def record(result):
        results.append(result)
        stop.set()
    snapshot = demo_config()
    LiveAdapter().run(snapshot, [d['device_id'] for d in snapshot['devices']], 'healthy', stop.is_set, record)
    assert len(seen) == len(results) == 1
    assert results[0]['verdict'] == 'PASS' and not results[0]['simulated']


def test_comparison_rejects_configuration_or_evidence_scope_mismatch(service):
    job = wait_finished(service, service.create({})['id'])
    other = copy.deepcopy(job)
    other['config_hash'] = 'different'
    assert not compare_jobs(job, other)['comparable']
    other = copy.deepcopy(job)
    other['results'][0]['scope'] = 'business_function'
    assert not compare_jobs(job, other)['comparable']
