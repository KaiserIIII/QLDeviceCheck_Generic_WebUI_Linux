import hmac
import ipaddress
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .comparison import compare_jobs
from .domain import catalog_devices, config_hash, ensure_read_only
from .reports import export_job

BASE_DIR = Path(__file__).resolve().parent.parent
JOB_PATH = re.compile(r'^/api/jobs/([a-f0-9]{32})(?:/(cancel|retest|export))?$')
MAX_BODY = 65536


class HttpError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == 'localhost'


def make_server(host, port, service, token=''):
    if not loopback(host) and not token:
        raise ValueError('Nonloopback bind requires QLDC_ACCESS_TOKEN')
    class Server(ThreadingHTTPServer):
        daemon_threads = True
    server = Server((host, port), Handler)
    server.service = service
    server.access_token = token
    server.bind_host = host
    return server


class Handler(BaseHTTPRequestHandler):
    server_version = 'QLDeviceCheckWorkbench/3.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _dispatch(self, method):
        try:
            if method == 'POST':
                self._body()
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            self._access(path)
            query = parse_qs(parsed.query)
            if method == 'GET' and path in ('/', '/index.html', '/legacy', '/assets/app.js', '/assets/i18n.js', '/assets/style.css'):
                relative = {'/': 'web/index.html', '/index.html': 'web/index.html', '/legacy': 'web/legacy.html', '/assets/app.js': 'web/assets/app.js', '/assets/i18n.js': 'web/assets/i18n.js', '/assets/style.css': 'web/assets/style.css'}[path]
                target = BASE_DIR / relative
                if not target.is_file():
                    raise HttpError(404, 'Asset not found')
                mime = 'text/javascript' if path.endswith('.js') else 'text/css' if path.endswith('.css') else 'text/html'
                return self._send(target.read_bytes(), mime + '; charset=utf-8')
            if method == 'GET' and path == '/favicon.ico':
                return self._send(b'', 'image/x-icon', 204)
            svc = self.server.service
            if method == 'GET' and path == '/api/health':
                return self._json({'ok': True, 'version': '3.1', 'mode': svc.mode, 'active_job_id': svc.active_id, 'persistence_ready': not svc.closed})
            if method == 'GET' and path == '/api/catalog':
                return self._json(dict(ok=True, **svc.catalog()))
            if method == 'POST' and path == '/api/config/validate':
                body = self._body()
                snapshot = body.get('config', body.get('snapshot', body))
                if not isinstance(snapshot, dict):
                    raise ValueError('Expected configuration object')
                devices = catalog_devices(snapshot)
                ensure_read_only(snapshot)
                return self._json({'ok': True, 'valid': True, 'config_hash': config_hash(snapshot), 'devices': devices, 'device_count': len(devices), 'warnings': ['Custom bytes require manual review; a read label does not establish physical safety']})
            if path == '/api/jobs':
                if method == 'POST':
                    return self._json({'ok': True, 'job': svc.create(self._body())}, 202)
                return self._json(dict(ok=True, **svc.list_jobs(limit=int(query.get('limit', ['20'])[0]), offset=int(query.get('offset', ['0'])[0]), status=query.get('status', [''])[0], query=query.get('query', query.get('q', ['']))[0], sort=query.get('sort', ['newest'])[0])))
            if method == 'GET' and path == '/api/insights':
                return self._json(dict(ok=True, **svc.insights()))
            if method == 'GET' and path == '/api/compare':
                baseline, current = query.get('baseline', [''])[0], query.get('current', [''])[0]
                if not re.fullmatch('[a-f0-9]{32}', baseline) or not re.fullmatch('[a-f0-9]{32}', current):
                    raise ValueError('Invalid comparison job ID')
                return self._json({'ok': True, 'comparison': compare_jobs(svc.get(baseline), svc.get(current))})
            match = JOB_PATH.fullmatch(path)
            if match:
                job_id, action = match.groups()
                if method == 'GET' and action is None:
                    return self._json({'ok': True, 'job': svc.get(job_id)})
                if method == 'GET' and action == 'export':
                    format = query.get('format', ['html'])[0]
                    export_query = parse_qs(parsed.query, keep_blank_values=True)
                    content, mime = export_job(svc.get(job_id), format, lang=export_query.get('lang', ['zh'])[0])
                    return self._send(content, mime, headers={'Content-Disposition': 'attachment; filename="acceptance-' + job_id + '.' + format + '"'})
                if method == 'POST' and action in ('cancel', 'retest'):
                    body = self._body()
                    job = svc.cancel(job_id) if action == 'cancel' else svc.retest(job_id, body)
                    return self._json({'ok': True, 'job': job}, 202 if action == 'retest' else 200)
            if path in ('/api/scan', '/api/scan-interface', '/api/run', '/api/test-device', '/api/standards'):
                if svc.demo:
                    raise HttpError(403, 'Legacy hardware APIs are disabled in demo mode')
                if path == '/api/standards' and method == 'GET':
                    from core.config_manager import StandardDeviceConfig
                    snapshot = svc._snapshot()
                    catalog_devices(snapshot)
                    return self._json({'ok': True, 'devices': StandardDeviceConfig(data=snapshot).devices(), 'legacy': True})
                if method == 'POST':
                    body = self._body()
                    with svc.legacy_operation():
                        from core.config_manager import StandardDeviceConfig
                        snapshot = svc._snapshot()
                        catalog_devices(snapshot)
                        ensure_read_only(snapshot)
                        import legacy_web
                        operations = {'/api/scan': legacy_web.scan_catalog, '/api/scan-interface': legacy_web.scan_interface, '/api/run': legacy_web.test_all_discovered, '/api/test-device': legacy_web.test_single_device}
                        result = dict(operations[path](body, config=StandardDeviceConfig(data=snapshot), state=svc.legacy_state), legacy=True)
                    return self._json(result)
            if method == 'GET' and path.startswith('/report/'):
                if svc.demo:
                    raise HttpError(403, 'Legacy reports are disabled in demo mode')
                root = (BASE_DIR / 'report').resolve()
                target = (root / path[len('/report/'):]).resolve()
                if not target.is_relative_to(root) or target.suffix not in ('.html', '.json', '.txt') or not target.is_file():
                    raise HttpError(404, 'Report not found')
                mime = {'.html': 'text/html', '.json': 'application/json', '.txt': 'text/plain'}[target.suffix]
                return self._send(target.read_bytes(), mime + '; charset=utf-8')
            raise HttpError(404, 'Route not found')
        except HttpError as exc:
            self._json({'ok': False, 'error': str(exc)}, exc.status)
        except KeyError:
            self._json({'ok': False, 'error': 'Job not found'}, 404)
        except (ValueError, TypeError):
            self._json({'ok': False, 'error': 'Invalid request; check input fields and configuration'}, 400)
        except RuntimeError:
            self._json({'ok': False, 'error': 'Station operation conflict or service unavailable'}, 409)
        except Exception:
            self._json({'ok': False, 'error': 'Operation failed'}, 500)

    def _access(self, path):
        host = self.headers.get('Host', '')
        try:
            parsed = urlsplit('http://' + host)
            hostname, port = parsed.hostname or '', parsed.port or 80
        except ValueError:
            raise HttpError(403, 'Invalid host') from None
        if port != self.server.server_port or (loopback(self.server.bind_host) and not loopback(hostname)):
            raise HttpError(403, 'Untrusted host')
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            raise HttpError(403, 'Foreign origin is not allowed')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise HttpError(403, 'Cross-site requests are not allowed')
        token = self.server.access_token
        if token and (path.startswith('/api/') or path.startswith('/report/')):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                raise HttpError(401, 'Access token required')

    def _body(self):
        if hasattr(self, '_parsed_body'):
            return self._parsed_body
        if self.headers.get('Transfer-Encoding'):
            raise HttpError(400, 'Transfer encoding is not supported')
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise HttpError(400, 'Invalid content length') from None
        if length < 0:
            raise HttpError(400, 'Invalid content length')
        if length > MAX_BODY:
            # Bounded discard avoids a TCP reset hiding the 413 response on Windows.
            # No oversized body is decoded, allocated in full, or dispatched.
            self.connection.settimeout(.2)
            try:
                self.rfile.read(min(length, MAX_BODY + 1))
            except OSError:
                pass
            raise HttpError(413, 'Request exceeds 64 KiB')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise HttpError(400, 'Incomplete request body')
        try:
            body = json.loads(raw.decode('utf-8')) if raw else {}
        except (UnicodeError, ValueError):
            raise HttpError(400, 'Malformed JSON') from None
        if not isinstance(body, dict):
            raise HttpError(400, 'Expected JSON object')
        self._parsed_body = body
        return body

    def _json(self, data, status=200):
        self._send(json.dumps(data, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

    def _send(self, content, content_type, status=200, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt, *args):
        # Request URLs can contain operator data; never log them or credentials.
        pass
