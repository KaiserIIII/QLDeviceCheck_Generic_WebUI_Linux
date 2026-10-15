# Validation / 验证记录

On 2026-10-07 the project owner confirmed the project had already been tested and could be used. The details of that earlier test are owner-provided context. This upgrade's reproducible automated validation uses synthetic adapters, temporary databases and loopback sockets. No real industrial device was operated by the upgrade automation.

## Reproduce

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python validate_config.py
python self_test.py
python -m coverage run --source=inspection,core.protocol_engine,core.transport -m pytest -q
python -m coverage report --show-missing
python -m compileall -q inspection core web_app.py legacy_web.py
node --check web/assets/app.js
```

See [DEMO.md](DEMO.md) for the optional browser workflow. CI runs the Python offline checks on `ubuntu-latest` and `windows-latest`, Python 3.10/3.13; inspect the linked PR checks for actual remote outcomes.

## What is exercised

| Area | Evidence |
| --- | --- |
| Coverage and verdicts | Missing expected devices, unfinished tasks, adapter failure and non-passing retests |
| State and persistence | Concurrent creation, lock conflicts, cancellation boundaries, restart recovery, bounded shutdown, no late passes, evidence-preserving atomic old-schema migration |
| Configuration | Snapshot survives file changes; original snapshot reused in retest; explicit write rejection |
| HTTP | Real ephemeral loopback requests, task lifecycle, bounded history/search/statistics, comparison/export, protected legacy report helper, malformed payloads, caps, traversal, Host/Origin/token checks |
| Demo isolation | Subprocess assertion that the normal demo workflow never imports the hardware detector or legacy server |
| Protocol | Fragmented configured active TCP, EOF, timeout/reset with partial evidence, Modbus CRC/truncation/identity/byte counts/quantity and write refusal |
| Browser | Fault task, evidence dialog, report download, repair retest, partial comparison, 21-task cross-page comparison with retained filter selections, protected legacy report downloads/error display, configuration validation, desktop/390 px layout and page script errors |

## Recorded results — 2026-10-07

- Clean hash-locked environment: CPython 3.13.0, Windows 11 build 26200, SQLite 3.45.3. `pip install --require-hashes -r requirements.lock` and dependency consistency check passed.
- Pytest: **93 passed**, independent clean-environment run of source commit `685946e7f9f4f6999b915e5a46114b8d6f32737c` completed in 20.22 seconds. Includes cross-process ownership, configuration-injected legacy requests, stable demo outcomes, identity-aware comparisons, every-probe/carrier evidence, incoming/outgoing TCP framing, coarse-clock timeout bounds, response/lock release timing, bounded history reads and migration rollback.
- Line coverage: **92%** for the new `inspection/` modules (729 statements, 58 uncovered); **86%** for the combined inspection/protocol-engine/transport selection (977 statements, 135 uncovered). These percentages do not cover the complete legacy detector. Windows run leaves POSIX locking branches uncovered locally; the CI matrix exercises POSIX separately.
- Original self-test: **23 offline checks passed**. Configuration validation: 5 root devices, 1 child, writes disabled. Python compile checks, JavaScript syntax checks and shell/PowerShell parsing passed.
- Browser: Node 24.19.0, Playwright 1.62.1, installed Chrome 155.0.8059.40. Desktop and 390 px workflow passed with **zero page script errors**. Cross-page comparison selected the first and 21st actual stored tasks and preserved both selections after changing the filter. Structured evidence and protected report browser tests use explicitly labelled synthetic fixtures; the Python/Node helper separately tests actual protected loopback report routes. README screenshots show normal demo records.
- Bounded-history regression: 300 records with 80 KiB snapshots; 20-row history decoded at most 20 projections and no full evidence, statistics decoded none. Peak Python allocations stayed below 4 MiB. A 120-record old-schema migration preserved original JSON/rowids and stayed below 4 MiB. Mixed recovery decoded only the three unfinished tasks among 103 records. These are regression fixtures, not a sustained-load benchmark.
- CI source `a9bbf872bb53a7dbbd87d86c85f3f009dfc72a9b`: Linux/Windows Python 3.10/3.13 all passed in [run 37607639342](https://github.com/KaiserIIII/QLDeviceCheck_Generic_WebUI_Linux/actions/runs/37607639342). Final changed-source outcomes are available from [PR checks](https://github.com/KaiserIIII/QLDeviceCheck_Generic_WebUI_Linux/pull/1/checks) and the Release notes.
- Release candidate `e0dc7fc`: Windows package extraction, fresh offline runtime installation with `PIP_NO_INDEX=1`, launcher argument forwarding, actual localhost server, fault task, export, healthy failed-only retest, scope comparison and restart/history preservation passed. Final bundles are rebuilt from the final tagged commit and checked again before upload; the final source/hash and smoke outcome are recorded in the Release notes.

## Recorded results — 2026-10-08 bilingual upgrade

- Python regression suite: **100 passed in 20.90 seconds** on the Windows development environment. The new report route checks English and Chinese exports, byte-identical JSON/CSV output, fixed public asset delivery, unsupported and explicitly blank language rejection, and HTML escaping of original evidence.
- JavaScript syntax and Python compilation checks passed for the bilingual assets and application modules. Configuration validation passed with 5 root devices, 1 child device and writes disabled; the original offline self-test passed all **23 checks**.
- Browser workflow: the new bilingual workflow passed on Chromium with English and Chinese switching, task creation, evidence display, English HTML download, failed-only retest, history filters, cross-page comparison selection retention, reload preference, blocked `localStorage` fallback, the compatibility scan page and 390 px layout. The existing Chinese workflow also passed with zero page errors.
- API boundary: omitted `lang` keeps the Chinese default; `lang=zh` and `lang=en` return the requested report language; unsupported or explicitly blank language values return HTTP 400. No live device write or unknown hardware operation was used by these checks.

## Recorded results — 2026-10-15 reliability review

The AI-assisted review used public baseline `c7e3ddcf6e6594ea9874cd63b870183bdd6850ea`, isolated source copies and an independent clone for the merge candidate. No real devices were accessed and no new dependency was installed during the local verification. Existing CPython 3.13.0, pytest 8.4.2 and coverage 7.10.6 were used on Windows.

- Full isolated-clone regression: **212 passed, zero failed/skipped**, in 38.01 seconds. The public baseline had 100 tests; the added regressions cover lifecycle, real temporary SQLite lock/commit/bootstrap/recovery faults, worker startup failure, stale/conflicting callbacks, repeated cancellation, HTTP admission/framing/teardown, protocol boundaries, nested-device rejection and CSV retest provenance.
- Original offline self-test: **23 checks passed**, including actual TCP/UDP exchange with loopback fixtures. Configuration validation passed with 5 root devices, 1 child and writes disabled. Python compilation passed.
- Broad runtime coverage, using `--branch --source=. --omit=tests/*,tools/*,self_test.py,validate_config.py`: **1,816/3,627 executable lines (50.07%)** and **558/1,414 branches (39.46%)**. This is a broader denominator than earlier targeted figures; it is not an 80% whole-project coverage claim.
- Real bounded HTTP tests deliver 401/403 JSON while a rejected body is already in flight and verify teardown byte/time limits. They do not establish resilience against arbitrary internet load.
- CSV tests exercise an actual three-task Demo retest chain and the loopback export endpoint. All original 29 columns/cells are preserved; column 30 contains the immediate `parent_job_id` (empty for roots). English/Chinese exports share the same CSV schema. Exact-29-column external consumers require adaptation.
- Storage failure cannot manufacture a durable terminal result when writes are unavailable. The tested response is explicit degradation, refusal of further operations, then interrupted-state recovery after repair/restart; there is no automatic hardware retry.

Linux and Python 3.10 were not executed locally during this review. Use the final PR's existing Windows/Linux × Python 3.10/3.13 CI checks for remote results. Browser checks are separate: the earlier 203-test candidate was exercised on desktop/mobile with synthetic faults, retest, reload, keyboard controls and exports; the added CSV lineage column is covered by Python/API tests, not a new browser acceptance claim. No new release bundle or physical-device certification is implied.

Pre-merge review found and reproduced one additional compound failure: a worker could fail to start while its fallback terminal write also failed, leaving a queued record without the persistence diagnostic. The guarded fallback now sets the same restart-required state and preserves the original startup exception. A real temporary SQLite-trigger regression checks blocked creation/retest/legacy work, continued refusal after storage repair, and restart recovery without replay. The final staged Git tree, including normalized CSV/JSON fixtures, passed **213 tests in 34.70 seconds**, plus configuration validation, all 23 self-checks and compilation. Final broad runtime coverage is **1,819/3,630 lines (50.11%)** and **558/1,414 branches (39.46%)**, using the same command above. This final run supersedes the earlier 212-test candidate; no physical hardware or sustained-load conclusion is inferred.

## Scope and limits

The first PR matrix at candidate `17b3f4a` passed three jobs but its Windows/Python 3.13 job timed out waiting eight seconds for a preexisting Node helper subprocess (212 other tests passed). The same candidate's separate push matrix passed all four jobs in [run 38734469352](https://github.com/KaiserIIII/QLDeviceCheck_Generic_WebUI_Linux/actions/runs/38734469352); the failed PR run is retained as [run 38734503505](https://github.com/KaiserIIII/QLDeviceCheck_Generic_WebUI_Linux/actions/runs/38734503505). Those logs do not establish where the delay occurred or prove a product/runtime defect.

The test harness now gives each real fetch a three-second deadline, requires response statuses `[200,200,401]` and unauthorized JSON `ok:false`, emits request/completion evidence, and allows a separate 20-second subprocess lifecycle budget. A server-token mismatch must fail with the specific auth error and no success receipt. It uses natural Node shutdown. The final Git-tree export passed **214 tests in 36.00 seconds** (including both harness controls), with all 23 self-checks, configuration validation and compilation still passing. Runtime coverage is unchanged from the preceding 213-test result. Inspect final PR checks for the new head's cross-platform outcome; the earlier timeout is not hidden or counted as a production bug.

Coverage is collected for `inspection/`, `core.protocol_engine` and `core.transport`, not the entire legacy detector or repository. The original offline self-test adds loopback checks but does not establish field hardware compatibility. Browser tests are separate from the Python CI job and use an existing local browser runtime.

This automated suite does not measure serial driver behavior, electrical performance, sustained station load, power-loss filesystems or custom vendor protocols. The earlier usage-test confirmation is separate from these automated results. OS-level device exclusivity across different station data directories is not provided.

Reports describe configured communication/passive checks; certification and customer performance claims are outside this test record. See the Release notes and PR checks for the final uploaded revision and remote CI results.
