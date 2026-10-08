"""History reads must scale with page size, while full evidence survives migration."""
import json
import sqlite3
import tracemalloc

import pytest

from inspection.domain import refresh
from inspection.service import InspectionService
from inspection.store import SQLiteStore


def record(number, mode='demo', status='completed', verdict='PASS'):
    return refresh({'id': f'job-{number:04}', 'created_at': '2026-10-07T00:00:00.000Z',
                    'status': status, 'mode': mode, 'scenario': 'healthy',
                    'metadata': {'station_id': 'Straße 工位', 'batch': 'literal%_batch',
                                 'operator': 'Alice', 'notes': ''},
                    'config_hash': 'hash', 'config_snapshot': {'evidence': 'X' * 80000},
                    'device_ids': ['device'], 'parent_job_id': None, 'started_at': None,
                    'finished_at': None, 'duration_ms': 0, 'events': [],
                    'results': [{'device_id': 'device', 'verdict': verdict, 'attempts': [{'response': 'original'}],
                                 'supporting_checks': [{'response': 'carrier'}]}]})


def test_history_and_insights_do_not_decode_retained_evidence(tmp_path, monkeypatch):
    service = InspectionService(tmp_path / 'jobs.db', demo=True)
    try:
        for i in range(300):
            service.store.insert(record(i, mode='live' if i % 2 else 'demo', verdict='FAIL' if i % 3 == 0 else 'PASS'))
        original_loads = json.loads
        decoded = []
        def bounded_loads(value, *args, **kwargs):
            decoded.append(len(value))
            assert 'config_snapshot' not in value, 'history decoded full evidence'
            return original_loads(value, *args, **kwargs)
        monkeypatch.setattr(json, 'loads', bounded_loads)
        tracemalloc.start()
        history = service.list_jobs(limit=20, offset=20)
        insights = service.insights()
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        assert len(history['jobs']) == 20 and history['total'] == 300
        assert len(decoded) <= 20 and peak < 4 * 1024 * 1024
        assert insights == {mode: {'total': 150, 'completed': 150, 'passed': 100, 'failed': 50,
                                   'devices_passed': 100, 'devices_expected': 150} for mode in ('demo', 'live')}
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        service.close()


def test_old_four_column_database_streams_migration_and_preserves_exact_json(tmp_path):
    path = tmp_path / 'old.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)')
    original = {}
    for i in range(120):
        job = record(i)
        raw = json.dumps(job, ensure_ascii=False, indent=1)
        original[job['id']] = raw
        connection.execute('INSERT INTO jobs VALUES (?, ?, ?, ?)', (job['id'], job['created_at'], job['status'], raw))
    connection.commit()
    connection.close()
    tracemalloc.start()
    store = SQLiteStore(path)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    try:
        assert peak < 4 * 1024 * 1024
        assert dict(store.connection.execute('SELECT id, record FROM jobs')) == original
        page = store.list(limit=2, offset=1, query='STRASSE', sort='oldest')
        assert page['total'] == 120
        assert [job['id'] for job in page['jobs']] == ['job-0001', 'job-0002']
        assert 'config_snapshot' not in page['jobs'][0]
        assert store.get('job-0001') == json.loads(original['job-0001'])
    finally:
        store.close()
    store = SQLiteStore(path)
    try:
        assert store.list(limit=1)['total'] == 120
        assert store.connection.execute('SELECT record FROM jobs WHERE id=?', ('job-0001',)).fetchone()[0] == original['job-0001']
    finally:
        store.close()


def test_casefold_literal_filters_tied_order_and_updates(tmp_path):
    store = SQLiteStore(tmp_path / 'jobs.db')
    try:
        for i in range(4):
            job = record(i)
            if i == 2:
                job['metadata']['batch'] = 'literalXXbatch'
            store.insert(job)
        assert store.list(query='%_')['total'] == 3
        assert store.list(query='STRASSE')['total'] == 4
        assert store.list(query='JOB-0002')['total'] == 1
        assert [j['id'] for j in store.list(query='%_', limit=2, offset=1, sort='oldest')['jobs']] == ['job-0001', 'job-0003']
        assert [j['id'] for j in store.list(limit=2, sort='newest')['jobs']] == ['job-0003', 'job-0002']
        changed = store.get('job-0001')
        changed['metadata']['station_id'] = '更新站'
        changed['status'] = 'failed'
        store.update(changed)
        assert store.list(status='failed', query='更新站')['jobs'][0]['id'] == 'job-0001'
        assert store.list(query='STRASSE')['total'] == 3
        assert store.list(offset=10)['jobs'] == []
    finally:
        store.close()


def test_restart_decodes_only_unfinished_mixed_history(tmp_path, monkeypatch):
    path = tmp_path / 'jobs.db'
    store = SQLiteStore(path)
    for i in range(100):
        store.insert(record(i, mode='live' if i % 2 else 'demo'))
    for i, status in enumerate(('queued', 'running', 'cancelling'), 100):
        store.insert(record(i, mode='live', status=status, verdict='NOT_RUN'))
    store.close()
    original_loads = json.loads
    full_decodes = []
    def tracked_loads(value, *args, **kwargs):
        if 'config_snapshot' in value:
            full_decodes.append(value)
        return original_loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', tracked_loads)
    service = InspectionService(path, demo=True)
    try:
        assert len(full_decodes) == 3
        assert service.list_jobs(status='interrupted')['total'] == 3
        recovered = service.get('job-0101')
        assert recovered['events'][-1]['type'] == 'interrupted'
        assert recovered['results'][0]['attempts'] == [{'response': 'original'}]
        assert service.insights()['live']['completed'] == 50
        assert service.get('job-0001')['status'] == 'completed'
    finally:
        service.close()


def test_migration_error_rolls_back_schema_and_preserves_records(tmp_path):
    path = tmp_path / 'corrupt.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)')
        connection.execute('INSERT INTO jobs VALUES (?, ?, ?, ?)', ('broken', 'date', 'completed', '{broken'))
    with pytest.raises(json.JSONDecodeError):
        SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        assert [row[1] for row in connection.execute('PRAGMA table_info(jobs)')] == ['id', 'created_at', 'status', 'record']
        assert connection.execute('SELECT record FROM jobs').fetchone()[0] == '{broken'
