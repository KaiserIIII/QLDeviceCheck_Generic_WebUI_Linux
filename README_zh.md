# 工业设备检测系统

[English](README.md) · [配置指南](config/CONFIG_GUIDE.md) · [MIT 许可证](LICENSE)

面向 Linux 的设备发现与协议连通性检测工具，通过 Web 界面完成扫描、测试和报告生成。系统根据配置匹配串口、网络和 PCI 接口与已知设备，支持按接口、设备或全量执行检查。

## 检测能力

| 接口 | 检查内容 |
| --- | --- |
| 串口 | Modbus RTU 只读功能码 01–04；自定义请求与响应匹配 |
| 网络 | Modbus TCP、TCP 连通性、自定义 TCP/UDP 探测 |
| PCI | 被动检查链路状态、驱动绑定及关联接口节点 |

支持全量扫描、单接口扫描和单设备复测，生成 HTML、TXT、JSON 报告。`via_device` 配置用于表示通过 PLC 或网关共享连接的下级设备。

## 运行

需要 Linux、Python 3.7+ 及对应接口的访问权限。

```bash
bash setup_linux.sh
bash run_web.sh
```

浏览器访问 <http://127.0.0.1:8080>。

## 配置与验证

在 [config/device_list.json](config/device_list.json) 中定义设备，参考 [device_list_examples.json](config/device_list_examples.json) 和 [配置指南](config/CONFIG_GUIDE.md)。

```bash
python3 validate_config.py
python3 self_test.py
```

配置校验与内部自测不会访问现场设备。

## 实现

- [web_app.py](web_app.py)：HTTP API、检测任务协调与报告生成。
- [core/config_manager.py](core/config_manager.py)：配置加载与校验。
- [core/generic_detector.py](core/generic_detector.py)：设备发现与检测流程。
- [core/protocol_engine.py](core/protocol_engine.py)：协议请求与响应校验。
- [web/index.html](web/index.html)：浏览器界面。

## 设备访问

内置 Modbus 检查使用只读功能码，PCI 检查采用被动方式。自定义报文的行为取决于设备协议，使用前应单独审核。现场测试前先在隔离环境验证，并将 Web 界面限制在可信网络内。
