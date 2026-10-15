# Changelog

## Unreleased — 2026-10-15 reliability review

- Reject untrusted POST headers before reading request bodies; validate duplicate/ambiguous headers and preserve bounded error-response delivery.
- Protect finished task history from stale/conflicting callbacks, reject work during shutdown and fail closed after an unrecoverable worker persistence error.
- Persist retest parent identity in the initial insert; clean up failed SQLite bootstrap and worker startup.
- Apply shared response constraints to `one_of` candidates, enforce read-function-specific Modbus quantities/address spans and reject non-finite or non-integral settings.
- Reject unsupported device descendants beyond one child level instead of silently reducing the expected-device count.
- Append `parent_job_id` to CSV exports as column 30. Existing 29 columns keep their order and values; consumers requiring exactly 29 columns need updating.
- Add 114 automated regressions above the 100-test public baseline. The isolated Windows candidate passes 214 tests, including combined worker-start/storage failure and a negative authentication-harness control; remote matrix outcomes are recorded separately in PR checks. These changes and tests were developed with AI assistance.

## 3.1 — 2026-10-08

- Add English/Chinese interfaces for the acceptance workbench and compatibility scan page, with browser-language defaults and a remembered preference.
- Translate navigation, forms, status labels, dialogs and operational messages while retaining user-entered data and original evidence.
- Export printable HTML reports in the chosen language; preserve default Chinese exports and unchanged JSON/CSV data.
- Add bilingual browser/API/report regression checks and matching offline release packages.

## 3.0 — 2026-10-07

- Add a configuration-driven acceptance workbench with station/batch/operator metadata, async progress and cooperative cancellation.
- Persist task snapshots, results and events in SQLite; mark unfinished tasks interrupted on restart without hardware replay.
- Keep expected missing devices as failures; separate execution status from inspection verdict.
- Add failed-only retest lineage, scope-aware comparison, HTML/JSON/CSV evidence reports and separate demo/live metrics.
- Add a hardware-independent synthetic demo, local responsive browser assets and a compatibility UI at `/legacy`.
- Assemble fragmented configured active TCP responses; validate Modbus byte counts, quantities and frame identity.
- Add API and protocol regressions, a demo browser workflow, source/artifact dependency provenance and pinned offline CI.

The project owner confirmed the project was tested and is usable. This upgrade's automated checks and release-package validation are recorded in [validation](docs/VALIDATION.md).
