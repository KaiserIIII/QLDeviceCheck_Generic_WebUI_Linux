"""Extract a Windows release, install offline, run and restart the real demo server."""
import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def request(base, path, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def wait_for(callback, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = callback()
            if condition(value):
                return value
        except (OSError, ValueError):
            pass
        time.sleep(.05)
    raise AssertionError('Release workflow timed out')


def smoke(package):
    if os.name != 'nt':
        raise RuntimeError('This smoke test exercises the Windows release launcher')
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PIP_NO_INDEX='1')
    env.pop('QLDC_ACCESS_TOKEN', None)
    with tempfile.TemporaryDirectory(prefix='release-smoke-', dir='output') as scratch:
        scratch = Path(scratch).resolve()
        assert scratch.is_relative_to(Path('output').resolve())
        with zipfile.ZipFile(package) as bundle:
            for item in bundle.infolist():
                if not (scratch / item.filename).resolve().is_relative_to(scratch):
                    raise ValueError('Unsafe archive path')
            bundle.extractall(scratch)
        root = next(scratch.iterdir())
        install = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'setup_windows.ps1')],
                                 cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        if install.returncode:
            raise AssertionError(install.stdout.decode('utf-8', errors='replace'))
        launcher = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'run_demo.ps1'), '--help'],
                                  cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        assert launcher.returncode == 0 and b'--demo' in launcher.stdout and b'--data-dir' in launcher.stdout
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        base = 'http://127.0.0.1:' + str(port)
        process = None
        # Direct child ownership makes the forced-restart check deterministic.
        command = [str(root / '.venv/Scripts/python.exe'), '-X', 'utf8', str(root / 'web_app.py'), '--demo', '--port', str(port), '--data-dir', str(root / 'data/demo')]
        try:
            for run in range(2):
                process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                wait_for(lambda: request(base, '/api/health'), lambda d: d['mode'] == 'demo')
                with urllib.request.urlopen(base, timeout=3) as page:
                    assert 'FIELD ACCEPTANCE'.encode() in page.read()
                if run == 0:
                    original = request(base, '/api/jobs', {'station_id': 'RELEASE-SMOKE-01', 'batch': 'RELEASE', 'scenario': 'faults'})['job']
                    original = wait_for(lambda: request(base, '/api/jobs/' + original['id'])['job'], lambda j: j['status'] == 'completed')
                    assert original['summary']['failed'] > 0
                    child = request(base, '/api/jobs/' + original['id'] + '/retest', {'scenario': 'healthy'})['job']
                    child = wait_for(lambda: request(base, '/api/jobs/' + child['id'])['job'], lambda j: j['status'] == 'completed')
                    assert child['summary']['verdict'] == 'PASS'
                    comparison = request(base, '/api/compare?baseline=' + original['id'] + '&current=' + child['id'])['comparison']
                    assert comparison['comparable'] and comparison['partial_scope'] and not comparison['whole_unit_recovered']
                    report = request(base, '/api/jobs/' + child['id'] + '/export?format=json')
                    assert report['report_marker'] == 'SIMULATED' and report['config_snapshot'] == original['config_snapshot']
                else:
                    assert request(base, '/api/jobs')['total'] == 2
                    assert request(base, '/api/jobs/' + original['id'])['job']['summary']['failed'] > 0
                process.terminate()
                process.wait(5)
                process = None
            manifest = json.loads((root / 'RELEASE-MANIFEST.json').read_text('utf-8'))
            return {'result': 'PASS', 'source_commit': manifest['source_commit'], 'package': package.name,
                    'offline_install': True, 'task_report_retest_restart': True}
        finally:
            if process is not None:
                process.terminate()
                process.wait(5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    print(json.dumps(smoke(args.package), ensure_ascii=False))
