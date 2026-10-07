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
| State and persistence | Concurrent creation, lock conflicts, cancellation boundaries, restart recovery, bounded shutdown, no late passes |
| Configuration | Snapshot survives file changes; original snapshot reused in retest; explicit write rejection |
| HTTP | Real ephemeral loopback requests, task lifecycle, history/search, comparison/export, malformed payloads, caps, traversal, Host/Origin/token checks |
| Demo isolation | Subprocess assertion that the normal demo workflow never imports the hardware detector or legacy server |
| Protocol | Fragmented configured active TCP, EOF, timeout/reset with partial evidence, Modbus CRC/truncation/identity/byte counts/quantity and write refusal |
| Browser | Fault task, evidence dialog, report download, repair retest, partial comparison, persisted history, configuration validation, desktop/390 px layout and page script errors |

## Recorded results — 2026-10-07

- Clean hash-locked environment: CPython 3.13.0, Windows 11 build 26200, SQLite 3.45.3. `pip install --require-hashes -r requirements.lock` and dependency consistency check passed.
- Pytest: **85 passed**, independent clean-environment run completed in 19.90 seconds. Includes cross-process ownership, configuration-injected legacy requests, stable demo outcomes, identity-aware comparisons, every-probe/carrier evidence, incoming/outgoing TCP framing and response/lock release timing.
- Line coverage: **91%** for the new `inspection/` modules (693 statements, 62 uncovered); **85%** for the combined inspection/protocol-engine/transport selection (941 statements, 140 uncovered). These percentages do not cover the complete legacy detector. Windows run leaves POSIX locking branches uncovered locally; the CI matrix exercises POSIX separately.
- Original self-test: **23 offline checks passed**. Configuration validation: 5 root devices, 1 child, writes disabled. Python compile checks, JavaScript syntax checks and shell/PowerShell parsing passed.
- Browser: Node 24.19.0, Playwright 1.62.1, installed Chrome 155.0.8059.40. Desktop and 390 px workflow passed with **zero page script errors**. Structured evidence rendering used an explicitly labelled browser-only fixture to verify escaped content; screenshots show normal synthetic demo records.
- Release candidate `8872651`: Windows package extraction, fresh offline runtime installation with `PIP_NO_INDEX=1`, launcher argument forwarding, actual localhost server, fault task, export, healthy failed-only retest, scope comparison and restart/history preservation passed. Final bundles are rebuilt from the final tagged commit and checked again before upload.

## Scope and limits

Coverage is collected for `inspection/`, `core.protocol_engine` and `core.transport`, not the entire legacy detector or repository. The original offline self-test adds loopback checks but does not establish field hardware compatibility. Browser tests are separate from the Python CI job and use an existing local browser runtime.

This automated suite does not measure serial driver behavior, electrical performance, sustained station load, power-loss filesystems or custom vendor protocols. The earlier usage-test confirmation is separate from these automated results. OS-level device exclusivity across different station data directories is not provided.

Reports describe configured communication/passive checks; certification and customer performance claims are outside this test record. See the Release notes and PR checks for the final uploaded revision and remote CI results.
