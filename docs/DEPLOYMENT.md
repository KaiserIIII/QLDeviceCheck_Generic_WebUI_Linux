# Deployment / 工位部署

## Local station

Use Python 3.10+ and a dedicated virtual environment. On Linux run `bash setup_linux.sh`; it installs only pinned runtime dependencies with artifact hash verification. `bash run_web.sh --config /path/device_list.json --data-dir /path/station-data` starts a live station bound to `127.0.0.1:8080`. Add `--demo` to use only synthetic devices; demo ignores the live device configuration.

Use an absolute data directory when launching through a service manager. Task records and evidence live in `jobs.db`, with SQLite WAL/SHM companions while running. Stop the service before making a file-copy backup, or use SQLite's supported backup mechanism. Back up configuration separately. Protect the directory using OS permissions: records can contain unit IDs, operator notes and network endpoints. Retention and automatic deletion are not implemented.

**Run exactly one process per physical station.** The database has an exclusive OS-held ownership lease: a second process fails before modifying the first process's jobs. Separate database files do not coordinate concurrent access to the same hardware. The `.owner` companion file may remain after shutdown; ownership depends on the OS lock, not the presence of that file.

Restart marks unfinished jobs `interrupted`, retains evidence and does not resume hardware operations. Cancellation waits for the current device/carrier group. Device timeout settings bound most calls; service shutdown waits briefly, marks remaining work interrupted and rejects late result updates. Validate driver behavior and timeouts on the actual station before relying on unattended operation.

## Network access

Network binding requires `QLDC_ACCESS_TOKEN`:

```bash
export QLDC_ACCESS_TOKEN='<a-strong-local-secret>'
bash run_web.sh --host 0.0.0.0 --port 8080 --data-dir /path/station-data
```

Enter the token with **访问凭证** in the browser. It is kept in tab session storage, not a URL. CLI clients use a bearer header. The built-in server provides HTTP, not encrypted transport: use network access only inside a controlled station environment with suitable transport protection. Reverse proxies must preserve the service Host/Origin behavior; arbitrary hostname rewriting or TLS termination requires deliberate configuration and validation. This release has no user roles, multi-tenant isolation or public hosting workflow.

## Device scope

Review [configuration examples](../device_list_examples.json) and [the guide](../config/CONFIG_GUIDE.md). Acceptance creates results for all selected expected devices, not only discovered devices. Legacy scan remains available for diagnosis, but discovery success does not establish acceptance coverage.

Use an isolated test station before real equipment. Verify serial permissions, network routes, installed PCI tools/drivers, carrier/child addressing and response matching. Acceptance rejects explicit writes and Modbus functions outside 01–04; arbitrary custom bytes still require protocol review. PCI evidence is passive; register reads and response patterns prove only the configured communication condition. Electrical actions and business behavior need their own validated procedures.

## Compatibility

The old UI is at `/legacy` in live mode. It shares the station lock, produces original reports in `report/`, and is excluded from new task statistics. The original helper `preserve_scan_interfaces` remains available for the offline self-test. Use the new workflow for persistent, configuration-bound evidence and repair comparisons.
