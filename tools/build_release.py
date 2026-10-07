"""Package a committed snapshot and already-audited wheels; no network access."""
import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def build(ref, version, wheels_root, output):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', version):
        raise ValueError('Use a semantic release version such as v3.0.0')
    source_sha = subprocess.check_output(['git', 'rev-parse', ref], text=True).strip()
    source = subprocess.check_output(['git', 'archive', '--format=zip', source_sha])
    audit = json.loads(subprocess.check_output(['git', 'show', source_sha + ':docs/dependency-audit.json']))
    runtime = {d['name'].lower(): d for d in audit['dependencies'] if d['name'].lower() in ('psutil', 'pyserial')}
    output.mkdir(parents=True, exist_ok=True)
    assets = []
    for folder, platform in [('windows', 'windows-x64'), ('linux', 'linux-x86_64')]:
        wheels = sorted((wheels_root / folder).glob('*.whl'))
        if len(wheels) != len(runtime):
            raise ValueError('Expected exactly the audited runtime wheels for ' + platform)
        manifest = {'version': version, 'source_commit': source_sha, 'platform': platform,
                    'built_at_utc': datetime.now(timezone.utc).isoformat(), 'python_requirement': 'CPython >=3.10',
                    'dependencies': []}
        used = set()
        for wheel in wheels:
            name = wheel.name.split('-')[0].lower()
            dep = runtime[name]
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            if digest not in dep['allowed_artifact_sha256'] or name in used:
                raise ValueError('Wheel is not a unique audited release artifact: ' + wheel.name)
            used.add(name)
            manifest['dependencies'].append({'file': wheel.name, 'sha256': digest, 'version': dep['version'],
                                             'source_repository': dep['repo'], 'source_commit': dep['commit'], 'license': dep['license']})
        target = output / ('QLDeviceCheck-' + version + '-' + platform + '.zip')
        prefix = 'QLDeviceCheck-' + version + '/'
        with zipfile.ZipFile(io.BytesIO(source)) as base, zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as bundle:
            for item in base.infolist():
                if item.is_dir():
                    continue
                bundle.writestr(prefix + item.filename, base.read(item))
            for wheel in wheels:
                bundle.write(wheel, prefix + 'wheels/' + wheel.name)
                with zipfile.ZipFile(wheel) as distribution:
                    for item in distribution.namelist():
                        if not item.endswith('/') and ('license' in item.lower() or 'copying' in item.lower()):
                            bundle.writestr(prefix + 'third-party-licenses/' + wheel.name.split('-')[0] + '/' + Path(item).name,
                                            distribution.read(item))
            bundle.writestr(prefix + 'RELEASE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        assets.append(target)
    checksum = output / 'SHA256SUMS.txt'
    checksum.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in assets), encoding='utf-8')
    return {'source_commit': source_sha, 'assets': [str(p.resolve()) for p in assets + [checksum]]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--version', default='v3.0.0')
    parser.add_argument('--wheels', type=Path, default=Path('output/release-wheels'))
    parser.add_argument('--output', type=Path, default=Path('output/release'))
    args = parser.parse_args()
    print(json.dumps(build(args.ref, args.version, args.wheels, args.output), ensure_ascii=False))
