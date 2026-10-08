# Field Acceptance Workbench Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for task implementation and review. Steps use checkbox syntax.

**Goal:** Deliver a configuration-driven, evidence-preserving industrial acceptance workflow with a hardware-independent demo and reviewable GitHub changes.

**Architecture:** The existing protocol core stays behind a detector adapter. An inspection service serializes work, persists task snapshots to SQLite, and supplies the HTTP API, comparison and reports. The browser consumes only these APIs.

**Tech Stack:** Python 3.10+, standard-library HTTP/SQLite/threads, existing psutil and pyserial, local HTML/CSS/JavaScript, pytest and coverage for development.

## Global Constraints

- Work in E:/学习/实习/工控验收升级 on codex/field-acceptance-workbench; preserve the original project directory.
- No real industrial device operations during development. Fake adapters and loopback fixtures only.
- No third-party Skill installation, no remote transmission of private code/configuration, preserve MIT license.
- Explicit --demo uses a synthetic catalog and never calls GenericDetector or hardware enumeration.
- Default bind 127.0.0.1; non-loopback bind requires QLDC_ACCESS_TOKEN.
- A single process/worker owns one SQLite task DB. Restart marks unfinished tasks interrupted, without replay.
- Verdicts PASS, FAIL, REVIEW, NOT_RUN; incomplete/failed tasks never count as fully passed.
- Modes/config hashes/scope must be checked before comparison claims; demo excluded from live statistics.
- Cancellation is cooperative at device boundaries and reports unfinished devices NOT_RUN.
- Existing config formats and legacy scan/retest endpoints remain available in live mode under a shared lock.

### Task 1: Acceptance domain, persistence, adapters and HTTP

**Files:** Create inspection/{__init__,domain,store,adapters,service,comparison,reports,http}.py and tests/test_workbench.py, tests/test_http.py; modify web_app.py into a compatibility entry point by preserving the old server in legacy_web.py; create demo catalog in code.

**Interfaces:** InspectionService(db_path, demo=False, config_path=None, adapter=None); catalog() -> dict; create(payload, snapshot=None) -> job dict; get(job_id) -> dict; list_jobs(limit=20, offset=0, status='', query='') -> dict; cancel(job_id) -> dict; retest(job_id,payload) -> dict; insights() -> dict; close() requests cancellation and waits boundedly. Service exposes hardware_lock for legacy calls. Store.get/list/update persist JSON job records and event timestamps. compare_jobs(baseline,current) -> dict; export_job(job,format) -> (bytes,content_type). make_server(host,port,service,token='') -> ThreadingHTTPServer.

**Job JSON:** id, status, mode ('demo'/'live'), scenario ('healthy'/'faults'/'timeout'/'crc'/'missing'), metadata {station_id,batch,operator,notes}, config_hash, config_snapshot, device_ids, parent_job_id, created_at/started_at/finished_at ISO UTC, duration_ms, progress {completed,total}, summary {expected,passed,failed,review,not_run,verdict}, results [], events []. Result fields device_id,name,connection_type,interface,protocol,scope,verdict,fault_code,summary,suggestion,request,response,duration_ms,simulated. catalog returns mode,config_hash,devices [{device_id,name,connection_type,interface,protocol,scope}],scenarios. Every expected device has an initial NOT_RUN result.

- [ ] Write failing service tests for missing devices, restart interruption, cancellation, contention, snapshot preservation, retest selection, scope and mode mismatch, escaped reports.

```python
def test_missing_device_cannot_pass(tmp_path):
    service = InspectionService(tmp_path / 'jobs.db', demo=True)
    job = service.create({'scenario': 'missing', 'station_id': 'DEMO-01'})
    finished = wait_finished(service, job['id'])
    assert finished['summary']['failed'] >= 1
    assert len(finished['results']) == finished['summary']['expected']
    assert finished['summary']['verdict'] != 'PASS'
```

- [ ] Run pytest tests/test_workbench.py; confirm missing implementation fails. Implement domain verdicts, bounded validation, atomic SQLite store, then one-worker service. Adapter.run(config, device_ids, scenario, cancelled, on_result) produces normalized device results in order, including parent carrier checks for selected children. Live adapter performs per-top-level-group detection with StandardDeviceConfig(data=snapshot), strips unselected results, reports progress at group boundaries, never filters missing devices. Write-labelled operations are rejected in acceptance regardless of stored allow_write_tests; do not claim arbitrary custom bytes are safe.
- [ ] Implement reports and comparison. retest only FAIL/REVIEW/NOT_RUN devices using original full config snapshot; payload can change demo scenario and operator notes but cannot widen scope. Full acceptance comparison requires same selected scope; failed-only retest may compare its shared devices with explicit partial scope and cannot claim whole-unit recovery.
- [ ] Write failing real HTTP tests before http.py. Use ephemeral loopback HTTP server, JSON via urllib, test create/poll/list/cancel/retest/catalog/validate/insights/compare/export and malformed input, missing IDs, foreign origin, missing token, 64 KiB cap, static traversal, legacy isolation in demo, 409 conflict.

```python
status, body = request('POST', '/api/jobs', {'scenario': 'faults'})
assert status == 202
assert body['job']['status'] in ('queued', 'running')
```

- [ ] Implement routes with wrappers {ok:true,job:...}/{ok:true,jobs:[],total:...}; compare {ok:true,comparison:...}, insights {ok:true,...}, catalog {ok:true,...}. Errors {ok:false,error:...}, use 400/401/403/404/409/413; do not expose exception traces. UI assets /assets/app.js and /assets/style.css served from fixed allowlist. Legacy UI /legacy; old APIs only live, share service lock. New root web/index.html is supplied in Task 3.
- [ ] Verify service and API tests, run existing self_test.py. Review task for state races, progress, persistent data and hardware access isolation; commit intended files.

### Task 2: Protocol framing regressions

**Files:** Modify core/protocol_engine.py and focused TCP receive paths in core/generic_detector.py; create core/transport.py and tests/test_protocol_regressions.py.

**Interfaces:** receive_response(sock, timeout, max_bytes=4096, complete=None) -> bytes uses a monotonic deadline, accumulates fragments up to a bound, handles EOF, preserves partial response at timeout. A predicate identifies complete Modbus MBAP frames or a satisfied explicit custom response rule. Without a predicate, collect until EOF/deadline rather than assuming recv is one application frame.

- [ ] Write failing tests for split custom TCP and Modbus TCP responses, byte-count mismatches, truncation, wrong transaction/unit/function, CRC error and write function refusal.

```python
def test_declared_modbus_byte_count_cannot_be_ignored():
    packet = bytes.fromhex('00 01 00 00 00 05 01 03 04 00 2A')
    assert not validate_modbus_tcp_response(packet, 1, 1, 3)[0]
```

- [ ] Run tests and capture observed failures. Replace one-recv assumptions in configured active TCP paths with bounded assembly; preserve actual partial bytes in evidence. Validate Modbus function 1/2 versus 3/4 response byte-count consistency, maximum ADU size, exception size and request count expectations where known.
- [ ] Run new protocol tests and original loopback self-test, review changes against Modbus official specification; commit.

### Task 3: Local browser workbench, delivery and publication

**Files:** Replace web/index.html; create web/assets/{app.js,style.css}; preserve old page as web/legacy.html. Update README.md, README_zh.md, docs/{ARCHITECTURE,API,DEMO,VALIDATION,DEPENDENCIES}.md, CHANGELOG.md, requirements.txt/requirements-dev.txt, .github/workflows/tests.yml, tests/browser_workflow.cjs, .gitignore.

- [ ] Write browser acceptance script that runs demo on localhost, creates faults task, waits completed, opens evidence, downloads JSON, creates healthy failed-only retest, compares recovery, refreshes history, and checks narrow screen overflow. Run against missing UI, capture failure.
- [ ] Implement local assets with sidebar, station overview, new-task form, expected catalog, task progress and status, sortable/filterable history, evidence detail drawer, exports, failed retest and two-job comparison. API auth token stored only in session memory/storage, never URL. Text is assigned safely via textContent or escaped templating. Show SIMULATED and partial scope/comparability prominently. Do not fabricate dashboard values or measurement data.
- [ ] Verify using local server, browser console/network, desktop and 390 px screenshot. Confirm cancellation exits spinner and all completed task rows survive refresh.
- [ ] Lock reviewed dependency sources/commits; fixed CI Action commits and minimum test environment. No CDN or added runtime framework. Run pytest, coverage (report workbench separately from legacy detector), self-test, config validation, Python compile, JS syntax, fresh demo startup.
- [ ] Request independent branch review, fix actionable findings and rerun affected tests. Write truthful validation including unverified Linux/Kylin/physical-device paths.
- [ ] Use GitHub yeet workflow: inspect full diff, commit, push branch, connector create draft PR and attach PR artifact. Verify remote commit/PR.

## Progress

### Authorized release delivery extension — 2026-10-07

User requested a published Release suitable for immediate use. After code review and fresh verification, publish v3.0.0 from the exact verified branch commit. Supply Windows x64 and Linux x86_64 archives with reviewed offline runtime wheels, source SHA/provenance manifest, SHA256SUMS, setup/start scripts and clear Python 3.10+ prerequisite. Exercise extraction, clean offline installation and real localhost startup/task/report/restart workflow on the available Windows host. Remote Linux CI supplies offline functional evidence; physical Linux/Kylin hardware remains unverified. Preserve the PR for review; the Release tag can point directly at this branch without merging main.

Tasks 1–3 are implemented and locally verified, including a seven-finding backend review fix wave. Recorded checks are in docs/VALIDATION.md. Final branch review, remote CI and publication are the remaining delivery gates. The controller maintains the local SDD progress ledger. User authorized branch/PR and Release publication; no additional user checkpoint is needed.
