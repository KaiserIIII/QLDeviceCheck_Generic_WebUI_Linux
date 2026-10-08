# QLDeviceCheck · 工控设备验收工作台

[English](README.md) · [演示](docs/DEMO.md) · [架构](docs/ARCHITECTURE.md) · [API](docs/API.md) · [验证记录](docs/VALIDATION.md) · [MIT](LICENSE)

面向工控设备厂商和系统集成商的本地验收工位。以应检设备清单为起点，记录本次检查范围、协议证据与交付信息，修复后保留原始记录并比较复测结果。

![验收工作台合成演示](docs/screenshots/overview.png)

## 围绕交付与维修组织功能

| 现场问题 | 工作台的处理方式 |
| --- | --- |
| 设备断开后从扫描列表消失 | 每个选定的配置设备都有结果，缺失设备保留失败记录。 |
| 报告无法对应当时的配置 | 每次任务保存完整配置快照、SHA-256、工位或设备编号、批次、操作者和时间。 |
| 修复后重跑覆盖原有证据 | 失败项复测关联原任务并沿用原配置；比较恢复、退化及覆盖范围。 |
| 演示依赖现场设备 | `--demo` 使用独立合成清单，报告标注 `SIMULATED`，模拟与真实任务分别统计。 |

产品定位是轻量的 Linux 现场验收流程与本地证据管理。项目方已确认项目经过测试、可以使用；[设计文档](docs/superpowers/specs/2026-10-07-field-acceptance-design.md)说明定位依据，[验证记录](docs/VALIDATION.md)记录本次升级的检查结果。

## 体验演示

直接安装可从 [Releases](https://github.com/KaiserIIII/QLDeviceCheck_Generic_WebUI_Linux/releases) 下载 Windows x64 或 Linux x86_64 包，内含离线运行依赖。先安装 CPython 3.10+，Windows 双击 `Start-Demo.cmd`，Linux 运行 `bash run_demo.sh`。[Release 使用说明](docs/RELEASE.md)。

工作台和兼容扫描页面支持 **English／简体中文**。使用页面语言选择器切换，浏览器会保存选择；首次访问时按浏览器语言选择中文或英文。HTML 报告按当前界面语言导出，配置设备名称、输入信息和原始协议证据保留原文。

需要 Python **3.10+**；Windows 也可运行演示及离线测试。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-runtime.lock
python web_app.py --demo --data-dir data/demo
```

PowerShell 使用 `.\.venv\Scripts\Activate.ps1` 激活环境。浏览器打开 [localhost:8080](http://127.0.0.1:8080)，创建“混合故障”任务、查看报文证据，再选择“全部正常”复测。[完整演示步骤](docs/DEMO.md)。

## 现场运行

```bash
bash setup_linux.sh
bash run_web.sh --config config/device_list.json --data-dir data/live
```

执行真实任务前审核目标设备及自定义报文。内置验收拒绝标注为写操作的配置及 01–04 以外的 Modbus 功能码，PCI 检查采用被动方式。通信检查结果不代表电气性能或完整业务功能通过。

| 接口 | 现有检测能力 |
| --- | --- |
| 串口 | Modbus RTU 只读功能码 01–04；自定义请求与响应匹配 |
| 网络 | Modbus TCP、TCP 连通性、自定义 TCP/UDP 检查 |
| PCI | 链路、驱动、接口节点检查，支持配置下级设备 |

真实模式保留 `/legacy` 原扫描界面，与验收任务共用工位执行锁；其报告单独存放，不计入新验收历史。现场接入参考[配置指南](config/CONFIG_GUIDE.md)和[部署说明](docs/DEPLOYMENT.md)。

## 工程实现

新增 `inspection/` 领域、服务、存储、适配器、HTTP 与报告模块，将浏览器验收流程和已有协议核心分开。SQLite 持久化任务与证据；重启后将未完成任务标记为中断，不自动重放硬件操作。每个工位数据库只允许一个服务进程使用。

前端使用本地资源，无 CDN 或前端框架。默认监听 `127.0.0.1`，网络监听必须设置 `QLDC_ACCESS_TOKEN`；提供输入限制、Host/Origin 校验及导出转义。当前未实现多用户权限和 TLS 终止。

```bash
python -m pip install --require-hashes -r requirements.lock
python validate_config.py
python self_test.py
python -m pytest -q
```

[验证记录](docs/VALIDATION.md)列明已测路径与未覆盖的真实硬件风险；[依赖记录](docs/DEPENDENCIES.md)提供固定版本、来源 commit、许可证及制品哈希。CI 在 Windows/Linux、Python 3.10/3.13 运行离线检查。
