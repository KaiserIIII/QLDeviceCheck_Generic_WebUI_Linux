# 工控机通用测试系统

> 运行在 Linux / 银河麒麟系统本机，通过浏览器完成已知设备扫描和工业协议连通性测试。

[中文](./README.md) · [License](./LICENSE)

---

## 简介

工控机出厂或运维阶段需要确认所有外接设备（串口模块、PLC、远程 IO、网卡、PCI 通信卡等）是否在线且通信正常。传统做法是逐个设备手工跑调试工具，效率低且容易遗漏。

本系统解决两个核心问题：

1. **自动化设备发现** —— 枚举本机所有通信接口（串口 / 网口 / PCI），对照已知设备配置库自动匹配。
2. **协议级连通验证** —— 对发现的设备发送只读协议探测帧（Modbus RTU / Modbus TCP / 自定义协议），收到有效响应才算"通过"。

全程通过浏览器操作，不依赖桌面环境，适合工控机触摸屏或远程 SSH 端口转发访问。

---

## 工作流程

<p align="center">
  <strong>枚举接口 → 匹配配置库 → 协议探测 → 响应判定 → 生成报告</strong>
</p>

| 步骤 | 操作 | 说明 |
|:--|:--|:--|
| 1 | 点击"扫描设备" | 系统枚举串口、网口和 PCI，按配置库逐项匹配，只显示真正有响应的设备 |
| 2 | 点击"测试全部" | 先扫描，再对已发现设备逐一发送协议探测帧，生成 HTML / TXT / JSON 三份报告 |
| 3 | 接口级扫描 | 每个接口右侧的独立扫描按钮，只检测该接口对应的配置设备 |
| 4 | 设备级复测 | 每个设备行的"测试"按钮，单独复测该设备 |

子系统架构分为四层：

```
接口识别层  →  枚举串口、网口、PCI 接口并检查可访问性
设备搜索层  →  按配置库匹配直连设备、中间模块和子设备
通信测试层  →  Modbus RTU / TCP 只读探测 + 自定义协议请求响应验证
配置管理层  →  device_list.json 标准化设备通信参数，启动时自动校验
```

中间模块（IO 模块、PLC）可作为透明通道，其子设备复用已连通的父设备接口。配置中通过 `via_device` 记录途经的中间模块，避免一条响应被重复识别为多个设备。

---

## 支持的协议和接口

| 类别 | 能力 |
|:--|:--|
| 串口 | Modbus RTU（功能码 01-04 只读）、自定义命令响应（hex / text / base64） |
| 网口 | Modbus TCP（功能码 01-04 只读）、TCP 连通测试（502 / 102 / 44818 / 4840）、自定义 TCP / UDP 协议 |
| PCI | PCIe 链路状态、驱动绑定、关联网口/串口节点 — 纯被动读取，不向外设发数据 |

---

## 快速开始

### 要求

- Linux（银河麒麟、Ubuntu、Debian 等）
- Python ≥ 3.7
- 串口设备读写权限（当前用户需在 `dialout` 组）

### 安装与运行

```bash
unzip QLDeviceCheck_Generic_WebUI_Linux.zip
cd QLDeviceCheck_Generic_WebUI_Linux
bash setup_linux.sh    # 首次运行：创建虚拟环境并安装依赖
bash run_web.sh        # 启动 Web 服务
```

浏览器打开：

```
http://127.0.0.1:8080
```

允许局域网其他设备访问（如工控机触摸屏）：

```bash
bash run_web.sh --host 0.0.0.0 --port 8080
```

### 配置校验

修改 `config/device_list.json` 后先运行：

```bash
python3 validate_config.py   # 校验配置文件合法性
python3 self_test.py         # 内部逻辑自检
```

两条命令均不访问现场硬件，可放心在开发机上运行。

---

## 项目结构

```
QLDeviceCheck_Generic_WebUI_Linux/
├── README.md
├── LICENSE
├── requirements.txt              # Python 依赖 (psutil, pyserial)
├── setup_linux.sh                # 首次环境初始化
├── run_web.sh                    # 启动 Web 服务
├── validate_config.py            # 配置文件校验（不访问硬件）
├── self_test.py                  # 内部逻辑自检（不访问硬件）
├── web_app.py                    # Web 服务器 + API 路由 + 报告生成
├── web/
│   └── index.html                # 浏览器界面（纯 HTML/CSS/JS，无框架依赖）
├── core/
│   ├── __init__.py
│   ├── config_manager.py         # 读取并校验 device_list.json
│   ├── generic_detector.py       # 核心检测引擎（五阶段扫描-测试流程）
│   └── protocol_engine.py        # 协议底层：Modbus 帧构造、CRC 校验、响应匹配
└── config/
    ├── device_list.json          # 已知设备通信方式库（交付时填写实际设备）
    ├── device_list_examples.json # 更多配置示例
    └── CONFIG_GUIDE.md           # 配置文件字段说明
```

### 核心模块职责

**`web_app.py`** — 基于 Python 标准库 `http.server.ThreadingHTTPServer` 的 Web 服务。提供四组 API：全量扫描、单接口扫描、全量测试、单设备测试。全局锁防止并发检测任务。检测完成后在 `report/` 目录生成三种格式的报告，按时间戳归档并按 `max_report_count` 自动清理旧报告。

**`core/config_manager.py`** — 加载 `device_list.json` 并逐项校验。检查 schema 版本、device_id 唯一性、串口参数合法性（波特率/数据位/校验位/停止位/超时）、网络参数（transport/role/端口范围）、Modbus 寄存器（功能码限制只读 1-4、地址 0-65535、数量 1-2000）、写操作是否需要显式授权、十六进制字段格式。配置错误在启动阶段报出，不会进入检测流程。

**`core/generic_detector.py`** — 检测引擎，按五个阶段执行：

1. **接口识别** — pyserial 枚举串口 + glob 补充 USB/ACM 节点 + sysfs 反查 PCI 串口；psutil 读网口 IP/MAC/状态；lspci 或 sysfs 枚举 PCI 设备。
2. **直连设备测试** — 串口设备按配置参数打开、发送 Modbus RTU 或自定义命令、解析响应；网口设备按 TCP/UDP 和 client/server 角色做连接或监听；PCI 设备做被动 sysfs 读取。
3. **子设备测试** — 复用已连通的中间模块接口，用于设备自身的协议参数（从站号、寄存器地址）探测。
4. **通信验证** — 汇总所有链路探测结果，区分"有响应/连通"、"需确认"、"异常"。
5. **结果汇总** — 统计通过/失败/警告数量并包装为 `GenericDetectionResult`。

**`core/protocol_engine.py`** — 底层协议函数。CRC-16 Modbus 校验（查表法）、Modbus RTU / TCP 读请求帧构造、从原始字节流中定位有效 RTU 响应帧、验证 TCP 响应的 MBAP 头和 PDU、自定义协议的请求编码（hex/text/base64 + 校验和追加）、12 种响应匹配策略（精确/前缀/后缀/包含/正则/文本/多候选/长度约束等）。

**`web/index.html`** — 单页面应用，零外部依赖。按串口/网口/PCI 三类分别渲染接口卡片，每张卡片内按接口分组展示匹配到的设备表。支持全量扫描、全量测试、单接口扫描、单设备复测四种操作。通过统一配置对象处理三类接口的渲染，避免重复代码。

---

## 设备配置文件

`config/device_list.json` 是已知设备通信方式库，每条设备记录包含：

```jsonc
{
  "device_id": "NET_PLC_001",            // 唯一标识
  "device_name": "PLC主站",              // 显示名称
  "device_type": "plc",                  // 设备类型（自定义标签）
  "connection_type": "network",          // serial / network / pci
  "network_config": {
    "ip": "192.168.1.100",               // 目标 IP
    "port": 502,                         // 目标端口
    "transport": "tcp",                  // tcp / udp
    "device_role": "server"              // server（本机主动连）/ client（本机等待连接）
  },
  "protocol": {
    "type": "modbus_tcp",                // modbus_rtu / modbus_tcp / custom
    "unit_id": 1,                        // Modbus 单元 ID
    "test_registers": [                  // 要读取的寄存器列表
      {"address": 0, "count": 10, "description": "PLC数据区"}
    ]
  },
  "is_intermediate_module": true,        // 是否为中间模块（有子设备）
  "child_devices": [ ... ]               // 通过此模块连接的子设备
}
```

串口设备不指定 `interface` 字段时，系统会自动扫描所有可用串口并逐个匹配。网络设备不指定网口时，系统根据目标 IP 走内核路由表自动选择。

详细配置说明见 [`config/CONFIG_GUIDE.md`](config/CONFIG_GUIDE.md)。

---

## 报告

"测试全部"完成后，在 `report/YYYYMMDD_HHMMSS_毫秒/` 目录下生成三份文件：

| 格式 | 文件名 | 用途 |
|:--|:--|:--|
| HTML | `report.html` | 浏览器直接打开，格式化展示 |
| TXT | `report.txt` | 打印或接入第三方日志系统 |
| JSON | `report.json` | 供程序解析或自动化流水线消费 |

三份报告内容一致，只保留接口、已发现设备、通过/失败和简短结论。`settings.max_report_count` 控制报告文件夹保留数量（默认 20），超出自动清理最旧记录。

---

## 安全边界

- 默认只进行读取和查询。Modbus 自动测试**仅允许功能码 0x01-0x04**（读线圈、读离散量、读保持寄存器、读输入寄存器）。
- 写线圈、写寄存器或设备动作测试必须显式设置 `allow_write_tests: true`。
- 默认监听 `127.0.0.1`，不对外暴露。如需局域网访问，手动指定 `--host 0.0.0.0`。
- 报告接口有路径穿越检测（拒绝含 `..` 的路径）。
- PCI 检测全程被动读取 sysfs 和 lspci 输出，不向 PCI 外设发送任何通信帧。

---

## TODO / 已知限制

- 单个设备的测试是串行的，多串口环境下建议使用单接口扫描按钮分批操作
- 检测进行中无法取消，需等待完成
- 运行日志仅在内存中，进程重启后丢失
- 前端尚未支持实时进度推送（当前为一次性返回全部结果）

---

## License

MIT — 详见 [LICENSE](./LICENSE)。
