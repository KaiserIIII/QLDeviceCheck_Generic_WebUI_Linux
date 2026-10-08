"""Execute the shipped report helper against actual protected loopback routes."""
import json
import re
import shutil
import subprocess

import pytest

from test_http import api


def test_legacy_report_helper_authorizes_blobs_and_rejects_bad_credentials(api, tmp_path, monkeypatch):
    import inspection.http
    svc, server, request = api
    # Only serve static synthetic files; this never invokes a hardware operation.
    script = request('GET', '/assets/i18n.js')[1].decode() + '\n' + re.search(r'<script>([\s\S]*?)</script>', request('GET', '/legacy')[1].decode()).group(1)
    monkeypatch.setattr(inspection.http, 'BASE_DIR', tmp_path)
    svc.demo = False
    root = tmp_path / 'report' / 'fixture'
    root.mkdir(parents=True)
    for suffix in ('html', 'txt', 'json'):
        (root / ('report.' + suffix)).write_text('synthetic report ' + suffix, encoding='utf8')
    server.access_token = 'session-secret'
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required to execute actual legacy helper')
    script_path = tmp_path / 'legacy-report.js'
    script_path.write_text(script, encoding='utf-8')
    code = r"""
const vm = require('node:vm'), assert = require('node:assert/strict'), fs = require('node:fs');
const script = fs.readFileSync(process.argv[1], 'utf8'), base = process.argv[2];
const values = new Map([['qldc.token','session-secret']]);
const blobs = [], clicked = [], revoked = [];
const element = {addEventListener(){}, querySelectorAll(){return []}};
const document = {documentElement:{dataset:{}},querySelector:()=>element,querySelectorAll:()=>[],getElementById:()=>element, addEventListener(){}, body:{append(){}},
  createElement:()=>({click(){clicked.push({href:this.href,download:this.download})},remove(){}})};
const context = {document, Blob, URL:{createObjectURL:b=>{blobs.push(b);return 'blob:local-'+blobs.length},revokeObjectURL:u=>revoked.push(u)},
  setTimeout:f=>f(), sessionStorage:{getItem:k=>values.get(k)},
  fetch:(url,opts)=>{assert.ok(!url.includes('secret'));return fetch(base+url,opts)}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
  for(const format of ['html','txt','json']){
    await vm.runInContext(`openReport('/report/fixture/report.${format}')`,context);
    assert.equal(await blobs.at(-1).text(),'synthetic report '+format);
    assert.equal(blobs.at(-1).type.replace(/\s/g,''),{html:'text/html;charset=utf-8',txt:'text/plain;charset=utf-8',json:'application/json;charset=utf-8'}[format]);
  }
  assert.equal(clicked.length,3);assert.equal(revoked.length,3);
  for(const c of clicked){assert.ok(c.href.startsWith('blob:'));assert.ok(!JSON.stringify(c).includes('secret'));}
  for(const token of ['', 'wrong']){
    values.set('qldc.token',token);
    await assert.rejects(vm.runInContext("openReport('/report/fixture/report.html')",context));
  }
  assert.equal(clicked.length,3);
  for(const url of ['https://evil.example/report/x.html','//evil/report/x.html','/api/scan','/report/x.html?token=secret'])
    await assert.rejects(vm.runInContext(`openReport(${JSON.stringify(url)})`,context));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run([node, '-e', code, str(script_path), f'http://127.0.0.1:{server.server_port}'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert request('GET', '/report/fixture/report.html')[0] == 401
    server.access_token = ''
    code_local = code[:code.index('(async()=>{')] + "(async()=>{values.set('qldc.token','');await vm.runInContext(\"openReport('/report/fixture/report.txt')\",context);assert.equal(await blobs[0].text(),'synthetic report txt');})().catch(e=>{console.error(e);process.exitCode=1});"
    result = subprocess.run([node, '-e', code_local, str(script_path), f'http://127.0.0.1:{server.server_port}'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
