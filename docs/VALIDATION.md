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

## Scope and limits

Coverage is collected for `inspection/`, `core.protocol_engine` and `core.transport`, not the entire legacy detector or repository. The original offline self-test adds loopback checks but does not establish field hardware compatibility. Browser tests are separate from the Python CI job and use an existing local browser runtime.

This automated suite does not measure serial driver behavior, electrical performance, sustained station load, power-loss filesystems or custom vendor protocols. The earlier usage-test confirmation is separate from these automated results. OS-level device exclusivity across different station data directories is not provided.

Reports describe configured communication/passive checks; certification and customer performance claims are outside this test record. See the Release notes and PR checks for the final uploaded revision and remote CI results.
