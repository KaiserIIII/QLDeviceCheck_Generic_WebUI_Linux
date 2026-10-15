"""Real temporary SQLite faults with synthetic service adapters only."""
import copy
import sqlite3
import threading

import pytest

from inspection.service import InspectionService
from inspection.ownership import DatabaseOwner
from inspection.store import SQLiteStore
from test_history_projection import record
from test_reliability_lifecycle import CapturingAdapter, joined
from test_reliability_lifecycle import HeldAdapter, one_result


def commit_fault(store, operation):
    connection = store.connection
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript('''
        CREATE TABLE fault_parent (id INTEGER PRIMARY KEY);
        CREATE TABLE fault_child (parent_id INTEGER REFERENCES fault_parent(id) DEFERRABLE INITIALLY DEFERRED);
        CREATE TRIGGER reject_commit AFTER ''' + operation + ''' ON jobs
        BEGIN INSERT INTO fault_child VALUES (999); END;
    ''')


def test_insert_commit_failure_rolls_back_record_projection_and_auxiliary_rows(tmp_path):
    store = SQLiteStore(tmp_path / 'jobs.db')
    try:
        commit_fault(store, 'INSERT')
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            store.insert(record(1))
        assert not store.connection.in_transaction
        assert store.list()['total'] == 0
        assert store.connection.execute('SELECT count(*) FROM fault_child').fetchone()[0] == 0
        store.connection.execute('DROP TRIGGER reject_commit')
        store.insert(record(1))
        assert store.get('job-0001')['status'] == 'completed'
    finally:
        store.close()


def test_update_commit_failure_preserves_full_record_and_history_projection(tmp_path):
    store = SQLiteStore(tmp_path / 'jobs.db')
    original = record(1)
    try:
        store.insert(original)
        commit_fault(store, 'UPDATE')
        changed = copy.deepcopy(original)
        changed['status'] = 'failed'
        changed['metadata']['notes'] = 'uncommitted'
        with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
            store.update(changed)
        assert store.get(original['id']) == original
        assert store.list(status='failed')['total'] == 0
        assert store.list(query='uncommitted')['total'] == 0
        assert not store.connection.in_transaction
        store.connection.execute('DROP TRIGGER reject_commit')
        store.update(changed)
        assert store.list(status='failed', query='uncommitted')['total'] == 1
    finally:
        store.close()


@pytest.mark.parametrize('fault', ['locked', 'commit'])
def test_failed_creation_never_starts_adapter_and_releases_station_lock(tmp_path, fault):
    path = tmp_path / 'jobs.db'
    adapter = CapturingAdapter()
    svc = InspectionService(path, demo=True, adapter=adapter)
    other = sqlite3.connect(path, timeout=0)
    try:
        if fault == 'locked':
            svc.store.connection.execute('PRAGMA busy_timeout=0')
            other.execute('BEGIN IMMEDIATE')
            failure = sqlite3.OperationalError
        else:
            commit_fault(svc.store, 'INSERT')
            failure = sqlite3.IntegrityError
        with pytest.raises(failure):
            svc.create({'device_ids': ['DEMO_RELAY']})
        assert not adapter.started.is_set()
        assert not svc.hardware_lock.locked()
        assert svc.active_id is None
        assert svc.list_jobs()['total'] == 0
        assert not svc.store.connection.in_transaction
        other.rollback()
        if fault == 'commit':
            svc.store.connection.execute('DROP TRIGGER reject_commit')
        job = svc.create({'device_ids': ['DEMO_RELAY']})
        joined(svc)
        assert svc.get(job['id'])['status'] == 'completed'
    finally:
        other.rollback()
        other.close()
        svc.close()


def test_failed_store_bootstrap_closes_its_sqlite_connection(tmp_path, monkeypatch):
    path = tmp_path / 'locked.db'
    locker = sqlite3.connect(path)
    locker.execute('CREATE TABLE seed (id INTEGER)')
    locker.execute('BEGIN IMMEDIATE')
    real_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        kwargs['timeout'] = 0
        value = real_connect(*args, **kwargs)
        opened.append(value)
        return value

    monkeypatch.setattr(sqlite3, 'connect', connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            SQLiteStore(path)
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            opened[0].execute('SELECT 1')
    finally:
        for value in opened:
            value.close()
        locker.rollback()
        locker.close()


def test_worker_start_failure_clears_active_state_and_persists_failure(tmp_path, monkeypatch):
    adapter = CapturingAdapter()
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=adapter)
    original_start = threading.Thread.start

    def unavailable(thread):
        raise RuntimeError('synthetic thread capacity exhausted')

    monkeypatch.setattr(threading.Thread, 'start', unavailable)
    try:
        with pytest.raises(RuntimeError, match='capacity exhausted'):
            svc.create({'device_ids': ['DEMO_RELAY']})
        assert svc.active_id is None
        assert svc.worker is None
        assert not svc.hardware_lock.locked()
        assert not adapter.started.is_set()
        saved = svc.list_jobs()['jobs'][0]
        assert saved['status'] == 'failed'
        assert saved['finished_at']
        monkeypatch.setattr(threading.Thread, 'start', original_start)
        job = svc.create({'device_ids': ['DEMO_RELAY']})
        joined(svc)
        assert svc.get(job['id'])['status'] == 'completed'
    finally:
        monkeypatch.setattr(threading.Thread, 'start', original_start)
        svc.close()


def test_worker_start_and_failure_persistence_fault_require_restart(tmp_path, monkeypatch):
    path = tmp_path / 'jobs.db'
    svc = InspectionService(path, demo=True, adapter=CapturingAdapter([one_result('FAIL')]))
    original = svc.create({'device_ids': ['DEMO_RELAY']})
    joined(svc)
    original = svc.get(original['id'])
    adapter = CapturingAdapter()
    svc.adapter = adapter
    svc.store.connection.executescript('''
        CREATE TRIGGER startup_terminal_fault BEFORE UPDATE ON jobs
        WHEN NEW.status='failed'
        BEGIN SELECT RAISE(ABORT, 'synthetic terminal storage fault'); END;
    ''')

    def unavailable(thread):
        raise RuntimeError('synthetic thread capacity exhausted')

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, 'start', unavailable)
            with pytest.raises((RuntimeError, sqlite3.IntegrityError)) as error:
                svc.create({'device_ids': ['DEMO_RELAY']})
        queued = next(job for job in svc.list_jobs()['jobs'] if job['id'] != original['id'])
        diagnostic = {'code': 'PERSISTENCE_UNAVAILABLE', 'job_id': queued['id'], 'restart_required': True}
        assert svc.persistence_error == diagnostic
        assert isinstance(error.value, RuntimeError) and 'capacity exhausted' in str(error.value)
        assert queued['status'] == 'queued' and queued['finished_at'] is None
        assert svc.active_id is None and svc.worker is None
        assert not svc.hardware_lock.locked() and not adapter.started.is_set()
        for operation in (lambda: svc.create({}), lambda: svc.retest(original['id'], {})):
            with pytest.raises(RuntimeError, match='Persistence'):
                operation()
        with pytest.raises(RuntimeError):
            with svc.legacy_operation():
                pytest.fail('degraded service admitted legacy work')
        svc.store.connection.execute('DROP TRIGGER startup_terminal_fault')
        with pytest.raises(RuntimeError, match='Persistence'):
            svc.create({})
        assert svc.list_jobs()['total'] == 2
    finally:
        svc.close()
    recovered_adapter = CapturingAdapter()
    reopened = InspectionService(path, demo=True, adapter=recovered_adapter)
    try:
        assert reopened.get(queued['id'])['status'] == 'interrupted'
        assert reopened.get(original['id']) == original
        assert reopened.persistence_error is None
        assert not recovered_adapter.started.is_set()
    finally:
        reopened.close()


def test_failed_restart_recovery_releases_lease_and_can_be_retried(tmp_path):
    path = tmp_path / 'jobs.db'
    store = SQLiteStore(path)
    original = record(1, status='running', verdict='NOT_RUN')
    store.insert(original)
    store.connection.executescript('''
        CREATE TRIGGER recovery_fault BEFORE UPDATE ON jobs
        WHEN NEW.status='interrupted'
        BEGIN SELECT RAISE(ABORT, 'synthetic recovery fault'); END;
    ''')
    store.close()
    adapter = CapturingAdapter()
    with pytest.raises(sqlite3.IntegrityError, match='recovery fault'):
        InspectionService(path, demo=True, adapter=adapter)
    owner = DatabaseOwner(path)
    owner.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT status FROM jobs').fetchone()[0] == 'running'
        connection.execute('DROP TRIGGER recovery_fault')
    svc = InspectionService(path, demo=True, adapter=adapter)
    try:
        recovered = svc.get(original['id'])
        assert recovered['status'] == 'interrupted'
        assert recovered['config_snapshot'] == original['config_snapshot']
        assert recovered['results'] == original['results']
        assert len([e for e in recovered['events'] if e['type'] == 'interrupted']) == 1
        assert not adapter.started.is_set(), 'restart must not retry the adapter'
    finally:
        svc.close()


def test_persistent_write_failure_is_explicit_and_denies_new_work_until_restart(tmp_path, monkeypatch):
    from inspection.http import make_server
    from test_http import requester

    class DelayedResult(HeldAdapter):
        def run(self, config, device_ids, scenario, cancelled, on_result):
            super().run(config, device_ids, scenario, cancelled, on_result)
            on_result(one_result())

    path = tmp_path / 'jobs.db'
    svc = InspectionService(path, demo=True, adapter=CapturingAdapter([one_result('FAIL')]))
    original = svc.create({'device_ids': ['DEMO_RELAY']})
    joined(svc)
    adapter = DelayedResult()
    svc.adapter = adapter
    blocker = sqlite3.connect(path, timeout=0)
    errors = []
    monkeypatch.setattr(threading, 'excepthook', lambda args: errors.append(args.exc_type.__name__))
    server = make_server('127.0.0.1', 0, svc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        job = svc.create({'device_ids': ['DEMO_RELAY']})
        assert adapter.started.wait(2)
        svc.store.connection.execute('PRAGMA busy_timeout=0')
        blocker.execute('BEGIN IMMEDIATE')
        adapter.release.set()
        joined(svc)
        assert errors == [], 'secondary finalization failure escaped the worker'
        assert svc.active_id is None
        assert not svc.hardware_lock.locked()
        assert svc.get(job['id'])['status'] == 'running', 'no durable final write was possible'
        assert svc.get(job['id'])['results'][0]['verdict'] == 'NOT_RUN'
        diagnostic = {'code': 'PERSISTENCE_UNAVAILABLE', 'job_id': job['id'], 'restart_required': True}
        assert svc.persistence_error == diagnostic
        status, health = requester(server)('GET', '/api/health')
        assert status == 200
        assert health['persistence_ready'] is False
        assert health['persistence_error'] == diagnostic
        for operation in (lambda: svc.create({}), lambda: svc.retest(original['id'], {})):
            with pytest.raises(RuntimeError, match='Persistence'):
                operation()
        with pytest.raises(RuntimeError):
            with svc.legacy_operation():
                pytest.fail('degraded service admitted legacy work')
        assert svc.list_jobs()['total'] == 2
        blocker.rollback()
        # Releasing the database does not silently replay work or clear the diagnostic.
        with pytest.raises(RuntimeError, match='Persistence'):
            svc.create({})
    finally:
        adapter.release.set()
        blocker.rollback()
        blocker.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        svc.close()
    assert svc.owner.file.closed
    recovered_adapter = CapturingAdapter()
    reopened = InspectionService(path, demo=True, adapter=recovered_adapter)
    try:
        recovered = reopened.get(job['id'])
        assert recovered['status'] == 'interrupted'
        assert recovered['results'][0]['verdict'] == 'NOT_RUN'
        assert recovered['summary']['verdict'] != 'PASS'
        assert reopened.get(original['id'])['status'] == 'completed'
        assert reopened.persistence_error is None
        assert not recovered_adapter.started.is_set()
    finally:
        reopened.close()
