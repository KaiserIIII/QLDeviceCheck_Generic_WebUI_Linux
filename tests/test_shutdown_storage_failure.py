"""Shutdown failures use native SQLite and synthetic workers, never devices."""
from contextlib import closing
import json
import sqlite3
import threading

import pytest

from inspection.ownership import DatabaseOwner
from inspection.service import InspectionService
from test_reliability_lifecycle import CapturingAdapter, joined, one_result


class LateResultAdapter:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.callback_returned = threading.Event()
        self.calls = 0

    def run(self, config, device_ids, scenario, cancelled, on_result):
        self.calls += 1
        self.started.set()
        assert self.release.wait(15), 'test did not release synthetic worker'
        on_result(one_result('PASS', 'late synthetic response'))
        self.callback_returned.set()


def durable_record(path, job_id):
    with closing(sqlite3.connect(path, timeout=0)) as connection:
        return connection.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()[0]


@pytest.fixture(params=['trigger', 'writer_lock'])
def failed_shutdown(tmp_path, request):
    path = tmp_path / 'jobs.db'
    service = InspectionService(path, demo=True, adapter=CapturingAdapter([one_result('FAIL', 'older evidence')]))
    original = service.create({'device_ids': ['DEMO_RELAY']})
    joined(service)
    original_raw = durable_record(path, original['id'])
    adapter = LateResultAdapter()
    service.adapter = adapter
    writer = None

    def repair_fault():
        nonlocal writer
        if writer is not None:
            writer.rollback()
            writer.close()
            writer = None
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('DROP TRIGGER IF EXISTS shutdown_fault')
            connection.commit()

    try:
        active = service.create({'device_ids': ['DEMO_RELAY']})
        assert adapter.started.wait(2)
        before = durable_record(path, active['id'])
        if request.param == 'trigger':
            service.store.connection.executescript('''
                CREATE TRIGGER shutdown_fault BEFORE UPDATE ON jobs
                WHEN NEW.status='interrupted'
                BEGIN SELECT RAISE(ABORT, 'synthetic shutdown storage fault'); END;
            ''')
            error_type, message = sqlite3.IntegrityError, 'synthetic shutdown storage fault'
        else:
            service.store.connection.execute('PRAGMA busy_timeout=0')
            writer = sqlite3.connect(path, timeout=0)
            writer.execute('BEGIN IMMEDIATE')
            error_type, message = sqlite3.OperationalError, 'locked'
        with pytest.raises(error_type, match=message) as caught:
            service.close()
        yield service, path, adapter, active, before, original, original_raw, repair_fault
    finally:
        adapter.release.set()
        repair_fault()
        if service.worker:
            service.worker.join(3)
            assert not service.worker.is_alive()
        service.close()


def test_failed_shutdown_closes_sqlite_and_remains_idempotent(failed_shutdown):
    service, path, adapter, active, before, original, original_raw, repair_fault = failed_shutdown
    assert service.closed, 'failed terminal commit must still close the service'
    assert service.closing and service.cancel_event.is_set()
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        service.store.connection.execute('SELECT 1')
    # Repeated close must not retry an impossible write or join the worker again.
    service.close()
    service.close()
    with pytest.raises(RuntimeError, match='closed|closing'):
        service.create({})
    with pytest.raises(RuntimeError):
        with service.legacy_operation():
            pytest.fail('closed service admitted legacy work')
    assert durable_record(path, original['id']) == original_raw
    assert durable_record(path, active['id']) == before
    assert service.worker.is_alive() and not service.owner.file.closed


def test_failed_shutdown_rejects_late_evidence_and_recovers_without_replay(failed_shutdown):
    service, path, adapter, active, before, original, original_raw, repair_fault = failed_shutdown
    assert service.worker.is_alive()
    with pytest.raises(RuntimeError, match='owned'):
        DatabaseOwner(path)
    adapter.release.set()
    joined(service)
    assert adapter.callback_returned.is_set(), 'exercise actual late callback'
    assert durable_record(path, active['id']) == before, 'late callback rewrote shutdown evidence'
    assert durable_record(path, original['id']) == original_raw
    assert service.owner.file.closed and not service.hardware_lock.locked()
    # Repair only the isolated fault fixture before reopening the real service.
    repair_fault()
    fresh_adapter = CapturingAdapter()
    reopened = InspectionService(path, demo=True, adapter=fresh_adapter)
    try:
        recovered = reopened.get(active['id'])
        prior = json.loads(before)
        assert recovered['status'] == 'interrupted'
        assert recovered['results'] == prior['results']
        assert recovered['config_snapshot'] == prior['config_snapshot']
        assert recovered['summary']['verdict'] != 'PASS'
        assert len([e for e in recovered['events'] if e['type'] == 'interrupted']) == 1
        assert durable_record(path, original['id']) == original_raw
        assert not fresh_adapter.started.is_set() and adapter.calls == 1
    finally:
        reopened.close()
    again = InspectionService(path, demo=True, adapter=fresh_adapter)
    try:
        assert again.get(active['id']) == recovered, 'second restart changed terminal evidence'
        assert not fresh_adapter.started.is_set()
    finally:
        again.close()


def test_concurrent_failed_close_cleans_once_and_keeps_original_error(tmp_path):
    path = tmp_path / 'jobs.db'
    adapter = LateResultAdapter()
    service = InspectionService(path, demo=True, adapter=adapter)
    closers = []
    outcomes = []
    barrier = threading.Barrier(3)

    def close():
        barrier.wait(timeout=3)
        try:
            service.close()
        except Exception as exc:
            outcomes.append(type(exc).__name__ + ':' + str(exc))
        else:
            outcomes.append('closed')

    try:
        job = service.create({'device_ids': ['DEMO_RELAY']})
        assert adapter.started.wait(2)
        service.store.connection.executescript('''
            CREATE TRIGGER shutdown_fault BEFORE UPDATE ON jobs
            WHEN NEW.status='interrupted'
            BEGIN SELECT RAISE(ABORT, 'synthetic shutdown storage fault'); END;
        ''')
        closers = [threading.Thread(target=close) for _ in range(2)]
        for thread in closers:
            thread.start()
        barrier.wait(timeout=3)
        for thread in closers:
            thread.join(4)
            assert not thread.is_alive()
        assert sorted(outcomes) == ['IntegrityError:synthetic shutdown storage fault', 'closed']
        assert service.closed and not service.owner.file.closed
        with pytest.raises(RuntimeError, match='owned'):
            DatabaseOwner(path)
        adapter.release.set()
        joined(service)
        assert service.owner.file.closed
        assert json.loads(durable_record(path, job['id']))['status'] == 'running'
    finally:
        adapter.release.set()
        for thread in closers:
            thread.join(4)
        if service.worker:
            service.worker.join(3)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('DROP TRIGGER IF EXISTS shutdown_fault')
            connection.commit()
        service.close()


def test_close_during_legacy_work_retains_lease_until_operation_exits(tmp_path):
    path = tmp_path / 'jobs.db'
    service = InspectionService(path, demo=True)
    entered, release = threading.Event(), threading.Event()
    errors = []

    def legacy():
        try:
            with service.legacy_operation():
                entered.set()
                assert release.wait(5)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=legacy)
    try:
        thread.start()
        assert entered.wait(2)
        service.close()
        assert service.closed and not service.owner.file.closed
        with pytest.raises(RuntimeError, match='owned'):
            DatabaseOwner(path)
        release.set()
        thread.join(2)
        assert not thread.is_alive() and not errors
        assert service.owner.file.closed and not service.hardware_lock.locked()
        owner = DatabaseOwner(path)
        owner.close()
        service.close()
    finally:
        release.set()
        thread.join(2)
        service.close()
