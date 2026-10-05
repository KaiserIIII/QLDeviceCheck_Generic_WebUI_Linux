# Industrial Device Check

[简体中文](README_zh.md) · [Configuration guide](config/CONFIG_GUIDE.md) · [MIT License](LICENSE)

A Linux Web UI for discovering known industrial devices, checking protocol responses, and generating inspection reports. Configuration-driven matching connects serial, network, and PCI interfaces to the equipment being tested.

## Capabilities

| Interface | Checks |
| --- | --- |
| Serial | Modbus RTU read functions 01–04; configured custom requests and response matching |
| Network | Modbus TCP, TCP connectivity, custom TCP/UDP probes |
| PCI | Passive inspection of link state, driver binding, and associated interface nodes |

The workflow supports full scans, individual interface scans, and device retests. Reports are generated in HTML, TXT, and JSON. The `via_device` configuration models devices reached through a shared PLC or gateway connection.

## Run

Requirements: Linux, Python 3.7+, and permission to access the selected interfaces.

```bash
bash setup_linux.sh
bash run_web.sh
```

Open <http://127.0.0.1:8080>.

## Configure and verify

Define equipment in [config/device_list.json](config/device_list.json), using [device_list_examples.json](config/device_list_examples.json) and the [configuration guide](config/CONFIG_GUIDE.md).

```bash
python3 validate_config.py
python3 self_test.py
```

Configuration validation and the internal self-test do not access field devices.

## Implementation

- [web_app.py](web_app.py): HTTP API, task coordination, and report generation.
- [core/config_manager.py](core/config_manager.py): configuration loading and validation.
- [core/generic_detector.py](core/generic_detector.py): discovery and inspection workflow.
- [core/protocol_engine.py](core/protocol_engine.py): protocol requests and response validation.
- [web/index.html](web/index.html): browser interface.

## Device access

Built-in Modbus checks use read-only function codes; PCI checks are passive. Custom requests can have different semantics and must be reviewed for the target equipment. Use an isolated test environment before field testing, and keep the Web UI on a trusted network.
