import json
import threading
import urllib.error
import urllib.request

import pytest

from inspection.http import make_server
from inspection.service import InspectionService
from test_workbench import BlockingAdapter, wait_finished


@pytest.fixture
def api(tmp_path):
    svc = InspectionService(tmp_path / 'jobs.db', demo=True)
    server = make_server('127.0.0.1', 0, svc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield svc, server, requester(server)
    server.shutdown()
    server.server_close()
    svc.close()
    thread.join(2)


def requester(server):
    def request(method, path, payload=None, headers=None, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request('http://127.0.0.1:%d%s' % (server.server_port, path), data=data, method=method,
                                         headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            return response.status, json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body
    return request


def test_real_http_job_workflow(api):
    svc, server, request = api
    status, catalog = request('GET', '/api/catalog')
    assert status == 200 and catalog['ok'] and catalog['mode'] == 'demo'
    assert request('GET', '/api/health')[1]['persistence_ready']
    assert request('POST', '/api/config/validate', {'config': catalog['config_snapshot']})[1]['valid']
    status, created = request('POST', '/api/jobs', {'scenario': 'faults'})
    assert status == 202 and created['job']['status'] in ('queued', 'running')
    job = wait_finished(svc, created['job']['id'])
    assert request('GET', '/api/jobs/' + job['id'])[1]['job'] == job
    assert request('GET', '/api/jobs?limit=1&status=completed')[1]['total'] == 1
    assert request('GET', '/api/insights')[1]['demo']['completed'] == 1
    assert request('POST', '/api/jobs/' + job['id'] + '/cancel', {})[1]['job']['status'] == 'completed'
    status, retest = request('POST', '/api/jobs/' + job['id'] + '/retest', {'scenario': 'healthy'})
    assert status == 202
    done = wait_finished(svc, retest['job']['id'])
    status, comparison = request('GET', '/api/compare?baseline=' + job['id'] + '&current=' + done['id'])
    assert status == 200 and comparison['comparison']['partial_scope']
    for format in ('html', 'json', 'csv'):
        status, data = request('GET', '/api/jobs/' + job['id'] + '/export?format=' + format)
        assert status == 200
        assert data['id'] == job['id'] if format == 'json' else b'SIMULATED' in data


def test_input_boundaries(api):
    svc, server, request = api
    for raw in (b'{', b'[]', b'null', b'"string"'):
        status, body = request('POST', '/api/jobs', raw=raw)
        assert status == 400 and not body['ok'] and 'traceback' not in body
    assert request('POST', '/api/jobs', raw=b' ' * 65537)[0] == 413
    assert request('GET', '/api/jobs/' + 'a' * 32)[0] == 404
    assert request('GET', '/api/jobs?limit=0')[0] == 400
    assert request('POST', '/api/config/validate', {'config': {}})[0] == 400
    assert request('GET', '/assets/../config/device_list.json')[0] == 404
    assert request('GET', '/assets/%2e%2e/config/device_list.json')[0] == 404
    assert request('GET', '/legacy')[0] == 200
    for path in ('/api/scan', '/api/run', '/api/scan-interface', '/api/test-device'):
        assert request('POST', path, {})[0] == 403


def test_origin_and_token(api):
    svc, server, request = api
    assert request('POST', '/api/jobs', {}, {'Origin': 'https://foreign.invalid'})[0] == 403
    server.access_token = 'secret'
    assert request('GET', '/api/catalog')[0] == 401
    assert request('POST', '/api/jobs', {}, {'Authorization': 'Bearer wrong'})[0] == 401
    assert request('GET', '/api/catalog', headers={'Authorization': 'Bearer secret'})[0] == 200
    assert request('GET', '/api/catalog?token=secret')[0] == 401
    with pytest.raises(ValueError):
        make_server('0.0.0.0', 0, svc)


def test_real_http_conflict_cancel_and_no_traceback(api):
    svc, server, request = api
    blocker = BlockingAdapter()
    svc.adapter = blocker
    status, job = request('POST', '/api/jobs', {})
    assert status == 202 and blocker.started.wait(1)
    assert request('POST', '/api/jobs', {})[0] == 409
    assert request('POST', '/api/jobs/' + job['job']['id'] + '/retest', {})[0] == 409
    assert request('POST', '/api/jobs/' + job['job']['id'] + '/cancel', {})[1]['job']['status'] == 'cancelling'
    assert wait_finished(svc, job['job']['id'])['status'] == 'cancelled'


def test_demo_never_imports_detector(tmp_path):
    import subprocess
    import sys
    code = "import sys; from inspection.service import InspectionService; from inspection.http import make_server; s=InspectionService(sys.argv[1],demo=True); h=make_server('127.0.0.1',0,s); s.create({}); s.close(); h.server_close(); assert 'core.generic_detector' not in sys.modules; assert 'legacy_web' not in sys.modules"
    subprocess.run([sys.executable, '-c', code, str(tmp_path / 'isolated.db')], check=True, timeout=5)


def test_http_error_never_leaks_traceback(api, monkeypatch):
    svc, server, request = api
    def fail():
        raise OSError('private path and password')
    monkeypatch.setattr(svc, 'catalog', fail)
    status, body = request('GET', '/api/catalog')
    assert status == 500
    assert body == {'ok': False, 'error': 'Operation failed'}
    assert request('GET', '/api/health', headers={'Host': 'foreign.invalid:' + str(server.server_port)})[0] == 403


def test_legacy_live_calls_share_acceptance_lock_and_are_explicit(api, monkeypatch):
    import legacy_web
    svc, server, request = api
    monkeypatch.setattr(legacy_web, 'scan_catalog', lambda payload: {'ok': True, 'devices': [], 'mode': 'scan'})
    svc.demo = False
    svc.mode = 'live'
    svc.hardware_lock.acquire()
    try:
        assert request('POST', '/api/scan', {})[0] == 409
    finally:
        svc.hardware_lock.release()
    status, body = request('POST', '/api/scan', {})
    assert status == 200 and body['legacy']
    assert legacy_web.RUN_LOCK is svc.hardware_lock
