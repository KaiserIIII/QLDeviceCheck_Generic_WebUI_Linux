# Changelog

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
