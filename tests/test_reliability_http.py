"""Actual loopback HTTP rejection, with a synthetic adapter and temporary DB."""
import json
import socket
import threading
from http.client import HTTPResponse

import pytest

from inspection.http import Handler, make_server
from inspection.service import InspectionService
from test_reliability_lifecycle import CapturingAdapter, HeldAdapter, joined


@pytest.fixture
def raw_api(tmp_path):
    svc = InspectionService(tmp_path / 'jobs.db', demo=True, adapter=CapturingAdapter())
    server = make_server('127.0.0.1', 0, svc, token='synthetic-token')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield svc, server
    server.shutdown()
    server.server_close()
    thread.join(2)
    svc.close()


def request(server, headers=(), body=b'{}', eof=True, method='POST', path='/api/jobs'):
    port = server.server_port
    values = [('Host', f'127.0.0.1:{port}'), ('Authorization', 'Bearer synthetic-token'),
              ('Content-Length', str(len(body)))]
    replacements = {key.lower() for key, value in headers}
    values = [(k, v) for k, v in values if k.lower() not in replacements] + list(headers)
    raw = f'{method} {path} HTTP/1.0\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in values) + '\r\n'
    with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
        client.sendall(raw.encode('latin1') + body)
        if eof:
            client.shutdown(socket.SHUT_WR)
        response = HTTPResponse(client)
        response.begin()
        return response.status, json.loads(response.read())


@pytest.mark.parametrize('headers', [
    [('Content-Length', '2'), ('Content-Length', '999')],
    [('Content-Length', '+2')],
])
def test_ambiguous_or_signed_body_length_cannot_create_task(raw_api, headers):
    svc, server = raw_api
    status, body = request(server, headers)
    assert status == 400
    assert body['ok'] is False
    assert svc.list_jobs()['total'] == 0


@pytest.mark.parametrize('shape', ['userinfo', 'path', 'query', 'fragment', 'duplicate'])
def test_malformed_host_cannot_pass_loopback_admission(raw_api, shape):
    svc, server = raw_api
    host = f'127.0.0.1:{server.server_port}'
    headers = {'userinfo': [('Host', 'untrusted@' + host)],
               'path': [('Host', host + '/untrusted')],
               'query': [('Host', host + '?untrusted')],
               'fragment': [('Host', host + '#untrusted')],
               'duplicate': [('Host', host), ('Host', 'foreign.invalid')]}[shape]
    status, body = request(server, headers)
    assert status == 403
    assert body['ok'] is False
    assert svc.list_jobs()['total'] == 0


@pytest.mark.parametrize('headers', [
    [('Authorization', 'Bearer \xe9')],
    [('Authorization', 'Bearer synthetic-token'), ('Authorization', 'Bearer wrong')],
])
def test_malformed_or_ambiguous_authorization_is_unauthorized(raw_api, headers):
    svc, server = raw_api
    status, body = request(server, headers)
    assert status == 401
    assert body['ok'] is False
    assert svc.list_jobs()['total'] == 0


@pytest.mark.parametrize('headers,body', [
    ([('Content-Length', '10')], b'{'),
    ([('Content-Length', '-1')], b''),
    ([('Content-Length', 'not-an-integer')], b'{}'),
    ([('Transfer-Encoding', 'chunked')], b'0\r\n\r\n'),
    ([], b'\xff'),
    ([], b'[]'),
])
def test_incomplete_or_malformed_body_has_no_task_side_effect(raw_api, headers, body):
    svc, server = raw_api
    status, response = request(server, headers, body)
    assert status == 400
    assert response['ok'] is False
    assert svc.list_jobs()['total'] == 0


def test_partial_body_timeout_is_a_client_error(raw_api, monkeypatch):
    svc, server = raw_api
    original_setup = Handler.setup

    def bounded_setup(handler):
        original_setup(handler)
        handler.connection.settimeout(.05)

    monkeypatch.setattr(Handler, 'setup', bounded_setup)
    status, body = request(server, [('Content-Length', '10')], b'{', eof=False)
    assert status == 400
    assert body['ok'] is False
    assert svc.list_jobs()['total'] == 0


def test_duplicate_http_cancel_requests_do_not_duplicate_transition(raw_api):
    svc, server = raw_api
    adapter = HeldAdapter()
    svc.adapter = adapter
    try:
        status, body = request(server)
        assert status == 202
        job_id = body['job']['id']
        assert adapter.started.wait(2)
        first = request(server, path='/api/jobs/' + job_id + '/cancel')
        second = request(server, path='/api/jobs/' + job_id + '/cancel')
        assert first[0] == second[0] == 200
        assert second[1]['job'] == first[1]['job']
        assert len([e for e in second[1]['job']['events'] if e['type'] == 'cancelling']) == 1
        adapter.release.set()
        joined(svc)
        assert svc.get(job_id)['status'] == 'cancelled'
        terminal = request(server, path='/api/jobs/' + job_id + '/cancel')
        assert terminal[0] == 200
        assert terminal[1]['job']['status'] == 'cancelled'
    finally:
        adapter.release.set()
