import copy
import threading
import time
import uuid
from contextlib import contextmanager

from .adapters import DemoAdapter, LiveAdapter, demo_config
from .domain import SCENARIOS, TERMINAL, catalog_devices, config_hash, ensure_read_only, initial_result, refresh, utc_now, validate_payload
from .store import SQLiteStore
from .ownership import DatabaseOwner


class InspectionService:
    def __init__(self, db_path, demo=False, config_path=None, adapter=None):
        self.demo = demo
        self.mode = 'demo' if demo else 'live'
        self.config_path = config_path
        self.adapter = adapter if adapter is not None else DemoAdapter() if demo else LiveAdapter()
        self.hardware_lock = threading.Lock()
        self.lock = threading.RLock()
        self.owner = DatabaseOwner(db_path)
        try:
            self.store = SQLiteStore(db_path)
        except Exception:
            self.owner.close()
            raise
        self.legacy_state = {'devices': {}, 'updated_at': '', 'config_hash': None}
        self.closed = False
        self.worker = None
        self.active_id = None
        self.active_started = None
        self.cancel_event = threading.Event()
        try:
            for job in self.store.unfinished():
                if job['status'] not in TERMINAL:
                    job['status'] = 'interrupted'
                    job['finished_at'] = utc_now()
                    job['events'].append({'at': utc_now(), 'type': 'interrupted', 'message': 'Service restarted; no automatic hardware retry'})
                    self.store.update(refresh(job))
        except Exception:
            self.store.close()
            self.owner.close()
            raise

    def _snapshot(self):
        if self.demo:
            return demo_config()
        from core.config_manager import StandardDeviceConfig
        try:
            return StandardDeviceConfig(self.config_path).data if self.config_path else StandardDeviceConfig().data
        except (AttributeError, TypeError):
            raise ValueError('Invalid nested configuration shape') from None

    @contextmanager
    def legacy_operation(self):
        with self.lock:
            if self.closed or not self.hardware_lock.acquire(blocking=False):
                raise RuntimeError('A station operation is already running or service is closed')
        try:
            yield
        finally:
            with self.lock:
                self.hardware_lock.release()
                if self.closed:
                    self.owner.close()

    def catalog(self):
        snapshot = self._snapshot()
        return {'mode': self.mode, 'config_hash': config_hash(snapshot), 'config_snapshot': snapshot,
                'devices': catalog_devices(snapshot), 'scenarios': SCENARIOS if self.demo else ['healthy']}

    def create(self, payload, snapshot=None):
        snapshot = copy.deepcopy(snapshot if snapshot is not None else self._snapshot())
        devices = catalog_devices(snapshot)
        ensure_read_only(snapshot)
        scenario, metadata, selected = validate_payload(payload, devices, self.mode)
        with self.lock:
            if self.closed:
                raise RuntimeError('Service is closed')
            if not self.hardware_lock.acquire(blocking=False):
                raise RuntimeError('A station operation is already running')
            try:
                job = refresh({'id': uuid.uuid4().hex, 'status': 'queued', 'mode': self.mode, 'scenario': scenario,
                               'metadata': metadata, 'config_hash': config_hash(snapshot), 'config_snapshot': snapshot,
                               'device_ids': selected, 'parent_job_id': None, 'created_at': utc_now(), 'started_at': None,
                               'finished_at': None, 'duration_ms': 0, 'results': [initial_result(d, self.demo) for d in devices if d['device_id'] in selected],
                               'events': [{'at': utc_now(), 'type': 'queued', 'message': 'Acceptance queued'}]})
                self.store.insert(job)
                self.active_id = job['id']
                self.cancel_event = threading.Event()
                self.worker = threading.Thread(target=self._run, args=(job['id'],), daemon=True, name='acceptance-worker')
                self.worker.start()
                return copy.deepcopy(job)
            except Exception:
                self.hardware_lock.release()
                raise

    def _run(self, job_id):
        started = time.monotonic()
        try:
            with self.lock:
                self.active_started = started
                job = self.store.get(job_id)
                job['status'] = 'cancelling' if self.cancel_event.is_set() else 'running'
                job['started_at'] = utc_now()
                job['events'].append({'at': utc_now(), 'type': 'running', 'message': 'Acceptance started'})
                self.store.update(job)
            def on_result(result):
                with self.lock:
                    if self.closed:
                        return
                    job = self.store.get(job_id)
                    matches = [i for i, r in enumerate(job['results']) if r['device_id'] == result.get('device_id')]
                    if not matches or result.get('verdict') not in ('PASS', 'FAIL', 'REVIEW', 'NOT_RUN'):
                        raise ValueError('Invalid adapter result')
                    expected = job['results'][matches[0]]
                    value = dict(expected, **copy.deepcopy(result))
                    value['scope'] = expected['scope']
                    value['simulated'] = self.demo
                    job['results'][matches[0]] = value
                    job['events'].append({'at': utc_now(), 'type': 'result', 'device_id': value['device_id'], 'message': value['verdict']})
                    self.store.update(refresh(job))
            if not self.cancel_event.is_set():
                self.adapter.run(copy.deepcopy(job['config_snapshot']), list(job['device_ids']), job['scenario'], self.cancel_event.is_set, on_result)
            with self.lock:
                if not self.closed:
                    job = self.store.get(job_id)
                    status = 'cancelled' if self.cancel_event.is_set() else 'completed'
                    if status == 'completed':
                        for result in job['results']:
                            if result['verdict'] == 'NOT_RUN':
                                result.update(verdict='FAIL', fault_code='MISSING', summary='Expected device produced no result', suggestion='Check carrier and device configuration')
                    self._finish(job, status, started)
        except Exception:
            with self.lock:
                if not self.closed:
                    self._finish(self.store.get(job_id), 'failed', started)
        finally:
            with self.lock:
                self.active_id = None
                self.active_started = None
                self.hardware_lock.release()
                if self.closed:
                    self.owner.close()

    def _finish(self, job, status, started):
        job.update(status=status, finished_at=utc_now(), duration_ms=round((time.monotonic() - started) * 1000, 2))
        job['events'].append({'at': utc_now(), 'type': status, 'message': 'Acceptance ' + status})
        self.store.update(refresh(job))

    def get(self, job_id):
        return self.store.get(job_id)

    def list_jobs(self, limit=20, offset=0, status='', query='', sort='newest'):
        return self.store.list(limit, offset, status, query, sort)

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job['status'] in TERMINAL:
                return job
            if self.active_id != job_id:
                raise RuntimeError('Job is not active')
            self.cancel_event.set()
            job['status'] = 'cancelling'
            job['events'].append({'at': utc_now(), 'type': 'cancelling', 'message': 'Cancellation requested; effective at a device/group boundary'})
            self.store.update(job)
            return job

    def retest(self, job_id, payload):
        with self.lock:
            original = self.get(job_id)
            if original['status'] not in TERMINAL:
                raise RuntimeError('Retest requires a finished job')
            if original['mode'] != self.mode:
                raise ValueError('Retest mode differs from service mode')
            if not isinstance(payload, dict) or set(payload) - {'scenario', 'operator', 'notes', 'device_ids'}:
                raise ValueError('Retest can only change scenario, operator or notes')
            selected = [r['device_id'] for r in original['results'] if r['verdict'] in ('FAIL', 'REVIEW', 'NOT_RUN')]
            if 'device_ids' in payload:
                ids = payload['device_ids']
                if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or any(i not in selected for i in ids):
                    raise ValueError('Retest cannot widen scope')
                selected = [i for i in selected if i in ids]
            if not selected:
                raise ValueError('No failed or pending devices to retest')
            data = dict(original['metadata'], scenario=payload.get('scenario', original['scenario']), device_ids=selected)
            data.update({k: v for k, v in payload.items() if k in ('operator', 'notes')})
            job = self.create(data, snapshot=original['config_snapshot'])
            saved = self.store.get(job['id'])
            saved['parent_job_id'] = original['id']
            self.store.update(saved)
            job['parent_job_id'] = original['id']
            return job

    def insights(self):
        return self.store.insights()

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.cancel_event.set()
            worker = self.worker
        if worker:
            worker.join(timeout=2)
        with self.lock:
            if self.closed:
                return
            if self.active_id:
                job = self.store.get(self.active_id)
                self._finish(job, 'interrupted', self.active_started or time.monotonic())
            self.closed = True
            self.store.close()
            if not self.hardware_lock.locked():
                self.owner.close()
