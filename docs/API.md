# API v3 / 本地验收接口

Base URL defaults to `http://127.0.0.1:8080`. JSON is UTF-8. When `QLDC_ACCESS_TOKEN` is set, send `Authorization: Bearer <token>` for `/api/*` and `/report/*`; never place credentials in query strings. Browser requests must use the station's own origin. POST bodies are JSON objects with a 64 KiB maximum.

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/api/health` | Service mode, version, active job and persistence status |
| GET | `/api/catalog` | Current expected devices, full configuration, SHA-256 and available scenarios |
| POST | `/api/config/validate` | Validate `{ "config": {...} }` without saving or running it |
| POST | `/api/jobs` | Create a task; returns 202 and `{ok,job}` |
| GET | `/api/jobs` | List `{ok,jobs,total,limit,offset}`; `limit` 1–100, `offset` 0–1,000,000, optional `status`, `query` and `sort=newest/oldest` |
| GET | `/api/jobs/{id}` | Full persisted task record |
| POST | `/api/jobs/{id}/cancel` | Request cooperative cancellation; finished tasks remain unchanged |
| POST | `/api/jobs/{id}/retest` | Create non-passing-device retest using original snapshot; returns 202 |
| GET | `/api/jobs/{id}/export?format=html` | Download HTML, JSON or CSV with evidence and snapshot |
| GET | `/api/compare?baseline={id}&current={id}` | Scope-aware result difference, comparability reasons and coverage flags |
| GET | `/api/insights` | Separate `demo` and `live` counts; passed/failed count completed tasks only |

Example task payload:

```json
{
  "station_id": "DEMO-FAT-01",
  "batch": "DEMO-2026-10",
  "operator": "Demo engineer",
  "notes": "Synthetic walkthrough",
  "scenario": "faults"
}
```

Omit `device_ids` to include the complete catalog; provide an array of known unique IDs to select a subset. Metadata strings have 128-character limits, except notes at 2,000. `scenario` is `healthy`, `faults`, `timeout`, `crc` or `missing` in demo; live accepts only `healthy` (the scenario label does not predetermine actual device results). Metadata may alternatively be nested under `metadata`.

Retest accepts `scenario`, `operator`, `notes`, and optional non-passing `device_ids`; it cannot change unit/batch, widen scope or switch mode. An empty eligible set is rejected. The returned task has `parent_job_id`.

## Record semantics

Task IDs are 32 lowercase hex characters. Times are ISO 8601 UTC. Status: `queued`, `running`, `cancelling`, `completed`, `cancelled`, `failed`, `interrupted`. Execution completion is independent of inspection success: `completed` can have a `FAIL` summary. Each result includes device/interface/protocol identity, scope, verdict, fault code, summary, rule-based suggestion, request, response, duration, `simulated`, structured `attempts` and `supporting_checks` for parent-carrier evidence.

`communication_path` describes the configured connectivity/protocol check. `pci_passive` describes passive PCI observations. Neither establishes full device functionality. A partial retest's successful summary only covers its selected devices. Comparable repair conclusions require matching nonempty station/unit IDs and matching batch, mode, configuration and evidence scope. Differences remain visible as observations when those conditions fail. Compare flags and report coverage must be considered before drawing wider conclusions.

Errors use `{ "ok": false, "error": "..." }`: 400 invalid fields/configuration, 401 token required, 403 origin/host or mode restriction, 404 missing resource, 409 station busy/service conflict, 413 oversized input, 500 unexpected failure. Errors omit exception traces and private paths. Do not automatically replay a hardware POST after an ambiguous connection failure; first inspect current task/history.

## Legacy interfaces

`/api/scan`, `/api/scan-interface`, `/api/run`, `/api/test-device`, `/api/standards` retain the original live interface and return `legacy: true`. They are disabled in demo. `/legacy` is the compatibility UI; `/report/` serves existing report files. Legacy operations share the acceptance execution lock but do not create new SQLite acceptance records.
