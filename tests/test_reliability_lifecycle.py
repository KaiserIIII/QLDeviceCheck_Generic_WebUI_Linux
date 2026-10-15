"""Synthetic lifecycle regressions; no detector or device transport is used."""
import copy
import threading

import pytest

from inspection.service import InspectionService


class CapturingAdapter:
    def __init__(self, results=None):
        self.callback = None
        self.results = results or []
        self.started = threading.Event()

    def run(self, config, device_ids, scenario, cancelled, on_result):
        self.callback = on_result
        self.started.set()
        for result in self.results:
            on_result(copy.deepcopy(result))


class HeldAdapter:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, config, device_ids, scenario, cancelled, on_result):
        self.started.set()
        assert self.release.wait(4), 'test did not release synthetic adapter'


def joined(service):
    worker = service.worker
    worker.join(2)
    assert not worker.is_alive(), 'synthetic worker did not stop'


def one_result(verdict='PASS', response='first evidence'):
    return {'device_id': 'DEMO_RELAY', 'verdict': verdict, 'response': response}


def test_callback_after_completion_cannot_rewrite_stored_evidence(tmp_path):
    adapter = CapturingAdapter([one_result()])
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    try:
        job = svc.create({'device_ids': ['DEMO_RELAY']})
        joined(svc)
        before = svc.get(job['id'])
        assert before['status'] == 'completed'
        adapter.callback(one_result('FAIL', 'late evidence'))
        assert svc.get(job['id']) == before
    finally:
        svc.close()


def test_old_callback_cannot_mutate_history_while_next_job_runs(tmp_path):
    first = CapturingAdapter([one_result()])
    second = HeldAdapter()
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=first)
    try:
        original = svc.create({'device_ids': ['DEMO_RELAY']})
        joined(svc)
        before = svc.get(original['id'])
        svc.adapter = second
        current = svc.create({'device_ids': ['DEMO_RELAY']})
        assert second.started.wait(2)
        first.callback(one_result('FAIL', 'stale callback'))
        assert svc.get(original['id']) == before
        assert svc.active_id == current['id']
        assert svc.get(current['id'])['results'][0]['verdict'] == 'NOT_RUN'
    finally:
        second.release.set()
        svc.close()


def test_duplicate_identical_callback_is_idempotent(tmp_path):
    adapter = CapturingAdapter([one_result(), one_result()])
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    try:
        job = svc.create({'device_ids': ['DEMO_RELAY']})
        joined(svc)
        saved = svc.get(job['id'])
        assert saved['status'] == 'completed'
        assert saved['summary']['verdict'] == 'PASS'
        assert len([e for e in saved['events'] if e['type'] == 'result']) == 1
    finally:
        svc.close()


def test_conflicting_duplicate_cannot_replace_failure_with_pass(tmp_path):
    adapter = CapturingAdapter([one_result('FAIL'), one_result('PASS', 'replacement')])
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    try:
        job = svc.create({'device_ids': ['DEMO_RELAY']})
        joined(svc)
        saved = svc.get(job['id'])
        assert saved['status'] == 'failed'
        assert saved['summary']['verdict'] != 'PASS'
        assert saved['results'][0]['verdict'] == 'FAIL'
        assert saved['results'][0]['response'] == 'first evidence'
    finally:
        svc.close()


def test_shutdown_rejects_create_while_waiting_for_worker_join(tmp_path, monkeypatch):
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=CapturingAdapter())
    svc.create({'device_ids': ['DEMO_RELAY']})
    joined(svc)
    entered_join = threading.Event()
    release_join = threading.Event()
    real_join = svc.worker.join

    def held_join(timeout=None):
        real_join(timeout)
        entered_join.set()
        assert release_join.wait(4), 'test did not release shutdown'

    monkeypatch.setattr(svc.worker, 'join', held_join)
    closer = threading.Thread(target=svc.close)
    closer.start()
    try:
        assert entered_join.wait(2)
        with pytest.raises(RuntimeError, match='closed|closing'):
            svc.create({'device_ids': ['DEMO_RELAY']})
    finally:
        release_join.set()
        closer.join(3)
        assert not closer.is_alive()
        svc.close()


def test_retest_parent_is_present_in_first_persisted_record(tmp_path):
    adapter = CapturingAdapter([one_result('FAIL')])
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    try:
        original = svc.create({'device_ids': ['DEMO_RELAY']})
        joined(svc)
        # A real SQLite constraint sees the first insert, before any worker runs.
        svc.store.connection.execute("""CREATE TRIGGER require_retest_parent BEFORE INSERT ON jobs
            WHEN json_extract(NEW.record, '$.parent_job_id') IS NULL
            BEGIN SELECT RAISE(ABORT, 'missing retest evidence chain'); END""")
        svc.store.connection.commit()
        child = svc.retest(original['id'], {})
        joined(svc)
        assert child['parent_job_id'] == original['id']
        assert svc.get(child['id'])['parent_job_id'] == original['id']
        assert svc.list_jobs()['total'] == 2
    finally:
        svc.close()


def test_concurrent_create_cancel_keeps_one_task_and_one_cancel_event(tmp_path):
    adapter = HeldAdapter()
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    barrier = threading.Barrier(7)
    outcomes = []

    def operation(create):
        barrier.wait(timeout=2)
        try:
            value = svc.create({}) if create else svc.cancel(job['id'])
            outcomes.append(('created' if create else 'cancelled', value['id']))
        except RuntimeError:
            outcomes.append(('conflict', None))

    threads = [threading.Thread(target=operation, args=(i % 2 == 0,)) for i in range(6)]
    try:
        job = svc.create({'device_ids': ['DEMO_RELAY']})
        assert adapter.started.wait(2)
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()
        assert outcomes.count(('conflict', None)) == 3
        assert outcomes.count(('cancelled', job['id'])) == 3
        assert svc.active_id == job['id']
        assert svc.list_jobs()['total'] == 1
        saved = svc.get(job['id'])
        assert saved['status'] == 'cancelling'
        assert len([e for e in saved['events'] if e['type'] == 'cancelling']) == 1
        adapter.release.set()
        joined(svc)
        terminal = svc.get(job['id'])
        assert terminal['status'] == 'cancelled'
        assert svc.cancel(job['id']) == terminal
    finally:
        adapter.release.set()
        for thread in threads:
            if thread.ident:
                thread.join(2)
        svc.close()


def test_closed_service_rejects_new_work_and_ignores_callback(tmp_path):
    path = tmp_path / 'jobs.db'
    adapter = CapturingAdapter([one_result()])
    svc = InspectionService(path, demo=True, adapter=adapter)
    job = svc.create({'device_ids': ['DEMO_RELAY']})
    joined(svc)
    before = svc.get(job['id'])
    svc.close()
    svc.close()
    assert svc.owner.file.closed
    assert svc.active_id is None
    assert not svc.hardware_lock.locked()
    with pytest.raises(RuntimeError, match='closed'):
        svc.create({})
    with pytest.raises(RuntimeError):
        with svc.legacy_operation():
            pytest.fail('closed service admitted legacy work')
    adapter.callback(one_result('FAIL', 'after close'))
    reopened = InspectionService(path, demo=True, adapter=CapturingAdapter())
    try:
        assert reopened.get(job['id']) == before
    finally:
        reopened.close()
