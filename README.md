# Industrial Device Check — Linux Web UI

> Browser-based discovery and read-only connectivity testing for known industrial devices on Linux and Kylin systems.

[中文说明](README_zh.md) · [Configuration guide](config/CONFIG_GUIDE.md) · [License](LICENSE)

## Overview

This tool helps factory acceptance and maintenance teams verify that serial modules, PLCs, remote I/O devices, network adapters, and PCI communication cards are present and responding. It combines interface discovery with protocol-level probes and presents the workflow in a lightweight local Web UI.

## What it checks

- Enumerates serial, network, and PCI interfaces.
- Matches detected interfaces against `config/device_list.json`.
- Sends read-only Modbus RTU/TCP or configured custom probes.
- Supports full scans, per-interface scans, and per-device retests.
- Generates timestamped HTML, TXT, and JSON reports.

```text
Enumerate interfaces → Match configuration → Probe protocol → Validate response → Generate reports
```

## Supported interfaces and protocols

| Category | Capabilities |
|---|---|
| Serial | Modbus RTU function codes 01–04; custom request/response in hex, text, or Base64 |
| Network | Modbus TCP; TCP connectivity checks; custom TCP and UDP probes |
| PCI | Link state, driver binding, and associated network/serial nodes using passive inspection |

Intermediate modules such as PLCs or I/O gateways can expose child devices through a shared connection. Use `via_device` in the configuration to model that relationship and avoid duplicate matches.

## Requirements

- Linux, including Kylin, Ubuntu, or Debian
- Python 3.7 or newer
- Permission to access serial devices (typically membership in the `dialout` group)

## Quick start

```bash
git clone https://github.com/KaiserIIII/QLDeviceCheck_Generic_WebUI_Linux.git
cd QLDeviceCheck_Generic_WebUI_Linux
bash setup_linux.sh
bash run_web.sh
```

Open `http://127.0.0.1:8080` in a browser.

To expose the UI on a trusted local network:

```bash
bash run_web.sh --host 0.0.0.0 --port 8080
```

## Configure devices

Edit `config/device_list.json`, using `config/device_list_examples.json` as a reference. Validate changes before connecting to field hardware:

```bash
python3 validate_config.py
python3 self_test.py
```

These two commands test configuration and internal logic without accessing field devices.

## Project structure

```text
├── web_app.py                    # HTTP server, API routes, and reports
├── core/config_manager.py        # Configuration loading and validation
├── core/generic_detector.py      # Discovery and test workflow
├── core/protocol_engine.py       # Modbus and custom protocol handling
├── config/device_list.json       # Known-device configuration
├── web/index.html                # Dependency-free browser UI
├── setup_linux.sh
├── run_web.sh
├── validate_config.py
└── self_test.py
```

## Safety

- Modbus probes are restricted to read-only function codes 01–04.
- PCI checks are passive.
- Custom probes should be reviewed against the target device's protocol before use.
- Do not expose the Web UI to untrusted networks.

## License

MIT License. See [LICENSE](LICENSE).
