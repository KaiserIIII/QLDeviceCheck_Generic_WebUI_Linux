# 设备配置说明与现场示例

本系统实际运行时只读取 `config/device_list.json`。该文件是已知设备通信方式库；扫描时只显示其中有真实接口或协议响应的设备，未连接的已知设备不会出现在工人页面，也不会因为“未安装”直接生成失败项。

## 基本结构

```json
{
  "schema_version": 2,
  "workstation_id": "ICT-001",
  "description": "工控机现场生产测试配置",
  "settings": {
    "allow_write_tests": false,
    "max_report_count": 20
  },
  "devices": []
}
```

每个设备至少包含：

| 字段 | 说明 |
| --- | --- |
| `device_id` | 设备唯一编号 |
| `device_name` | 页面和报告显示名称 |
| `device_type` | 设备类型，如 `sensor`、`plc`、`remote_io` |
| `connection_type` | `serial`、`network`、`pci` |
| `protocol` | 协议测试模板，尽量使用只读测试 |
| `is_intermediate_module` | 是否为中间模块，子设备需要挂在中间模块下 |
| `child_devices` | 子设备列表，可选 |

`allow_write_tests` 默认必须保持 `false`。只有测试规范明确要求写入、确认不会造成机械动作或参数变化，并准备好恢复步骤时才可启用。`max_report_count` 控制 `report` 目录保留的报告文件夹数量。

## 串口设备

串口设备不建议固定 `/dev/ttyS0` 这类接口。没有写 `interface` 时，程序会枚举本机可用串口，并逐个发送配置中的只读探测。

### Modbus RTU 传感器

```json
{
  "device_id": "SER_SENSOR_001",
  "device_name": "温湿度传感器",
  "device_type": "sensor",
  "connection_type": "serial",
  "serial_config": {
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 2
  },
  "protocol": {
    "type": "modbus_rtu",
    "slave_id": 1,
    "test_registers": [
      {"address": 0, "count": 2, "description": "温湿度数据"}
    ]
  },
  "is_intermediate_module": false
}
```

`test_registers` 可通过 `function_code` 指定只读功能码：`1` 读取线圈、`2` 读取离散输入、`3` 读取保持寄存器（默认）、`4` 读取输入寄存器。自动检测拒绝 `5`、`6`、`15`、`16` 等写功能码，避免测试时改变设备输出或参数。

如果同一总线上可能有多个站号和参数相同的设备，应读取厂商定义的型号/版本寄存器，并在该规则中增加 `response_match`。Modbus 的响应匹配默认针对数据区，可用 `exact_hex`、`prefix_hex` 等进一步确认型号；`match_mode: all` 可要求多个地址都验证通过。

例如现场继电器模块使用站号 1、线圈地址 0，可配置为：

```json
{
  "device_id": "SER_RELAY_001",
  "device_name": "RS485继电器模块",
  "device_type": "relay_module",
  "connection_type": "serial",
  "serial_config": {
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 2
  },
  "protocol": {
    "type": "modbus_rtu",
    "slave_id": 1,
    "test_registers": [
      {"function_code": 1, "address": 0, "count": 1, "description": "继电器线圈0状态"}
    ]
  },
  "is_intermediate_module": false
}
```

### 自定义串口设备

条码枪、电子秤、扫码平台以及厂商私有协议设备都使用通用探测规则。`type` 可以写 `custom`、`ascii` 或厂商协议名称，不需要修改 Python。请求支持 `request_hex`、`request_text`、`request_base64` 和 `request_mode: passive`；文本中的 `\\r`、`\\n`、`\\t` 会转换成真实控制字符。

```json
{
  "device_id": "SER_SCAN_001",
  "device_name": "条码扫描器",
  "device_type": "scanner",
  "connection_type": "serial",
  "serial_config": {
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 3
  },
  "protocol": {
    "type": "barcode_ascii",
    "probes": [
      {
        "name": "设备身份查询",
        "request_text": "INFO\\r\\n",
        "read_size": 256,
        "response_match": {
          "regex_text": "SCANNER|BARCODE",
          "min_length": 4
        }
      }
    ]
  },
  "is_intermediate_module": false
}
```

多个 `probes` 默认任意一项通过即匹配；设置 `"match_mode": "all"` 后必须全部通过。可用响应条件如下，写在 `response_match` 内，多个条件同时存在时全部满足才算通过：

| 字段 | 含义 |
| --- | --- |
| `length` / `min_length` / `max_length` | 响应字节数要求 |
| `exact_hex` | 整段二进制完全一致 |
| `prefix_hex` / `suffix_hex` | 指定十六进制前缀或后缀 |
| `contains_hex` | 包含指定字节序列 |
| `exact_text` / `contains_text` | 文本完全一致或包含内容 |
| `regex_text` / `regex_hex` | 文本或十六进制正则表达式 |
| `checksum` | `crc16_modbus`、`sum8` 或 `xor8` |

请求可用 `append_checksum` 自动追加 `crc16_modbus`、`sum8` 或 `xor8`。纯上报设备不发请求，使用 `"request_mode": "passive"`。旧字段 `test_command` 和 `expected_response_pattern` 继续兼容。

## 网口设备

网口设备需要写清楚两件事：

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `transport` | `tcp` 或 `udp` | 传输层协议 |
| `device_role` | `server` 或 `client` | 设备是服务端还是客户端 |

`device_role: server` 表示设备监听端口，本机作为客户端主动连接它。PLC、远程 IO、OPC UA、EtherNet/IP 等通常是这种形态。

`device_role: client` 表示设备主动连接本机，本机临时监听 `local_port` 等待设备接入。部分相机、扫码器、网关、采集盒会使用这种形态。

`interface` 可以省略：设备为服务端时，程序根据目标 IP 使用 Linux 路由自动确定网口；设备为客户端时，本机监听全部网口。只有测试规范明确指定物理网卡时才填写 `interface`。

### TCP 服务端设备：Modbus TCP PLC

```json
{
  "device_id": "NET_PLC_001",
  "device_name": "PLC主站",
  "device_type": "plc",
  "connection_type": "network",
  "network_config": {
    "ip": "192.168.1.100",
    "port": 502,
    "transport": "tcp",
    "device_role": "server"
  },
  "protocol": {
    "type": "modbus_tcp",
    "unit_id": 1,
    "test_registers": [
      {"address": 0, "count": 10, "description": "PLC数据区"}
    ]
  },
  "is_intermediate_module": true
}
```

### TCP 服务端设备：OPC UA

```json
{
  "device_id": "NET_OPCUA_001",
  "device_name": "OPC UA服务器",
  "device_type": "opcua_server",
  "connection_type": "network",
  "network_config": {
    "ip": "192.168.1.110",
    "port": 4840,
    "transport": "tcp",
    "device_role": "server"
  },
  "protocol": {"type": "opc_ua"},
  "is_intermediate_module": false
}
```

除内置的 `modbus_tcp` 外，如果网络协议只配置 `type`、没有配置 `probes/response_match`，系统只能确认目标 IP 和端口可连接，不能据此证明具体型号。厂商提供身份查询报文时，应按上面的通用规则补充请求和响应特征。

### TCP 客户端设备：视觉相机主动上报

```json
{
  "device_id": "NET_CAMERA_001",
  "device_name": "视觉相机",
  "device_type": "camera",
  "connection_type": "network",
  "network_config": {
    "ip": "192.168.1.120",
    "port": 9000,
    "local_port": 9000,
    "transport": "tcp",
    "device_role": "client",
    "listen_timeout": 5
  },
  "protocol": {
    "type": "camera_private_tcp",
    "response_match": {
      "prefix_hex": "43 41 4D",
      "min_length": 8
    }
  },
  "is_intermediate_module": false
}
```

### UDP 服务端设备：UDP传感器

```json
{
  "device_id": "NET_UDP_SENSOR_001",
  "device_name": "UDP温度采集器",
  "device_type": "sensor",
  "connection_type": "network",
  "network_config": {
    "ip": "192.168.1.130",
    "port": 6000,
    "transport": "udp",
    "device_role": "server",
    "timeout": 3
  },
  "protocol": {
    "type": "temperature_udp",
    "probes": [
      {
        "name": "读取温度",
        "request_text": "READ\\r\\n",
        "response_match": {
          "regex_text": "TEMP=-?\\d+(\\.\\d+)?"
        }
      }
    ]
  },
  "is_intermediate_module": false
}
```

### UDP 客户端设备：UDP主动上报

```json
{
  "device_id": "NET_UDP_PUSH_001",
  "device_name": "UDP主动上报模块",
  "device_type": "collector",
  "connection_type": "network",
  "network_config": {
    "ip": "192.168.1.131",
    "port": 7000,
    "local_port": 7000,
    "transport": "udp",
    "device_role": "client",
    "listen_timeout": 5
  },
  "protocol": {
    "type": "collector_udp",
    "response_match": {
      "prefix_hex": "AA 55",
      "min_length": 6
    }
  },
  "is_intermediate_module": false
}
```

## 中间模块与子设备

中间模块 `is_intermediate_module` 设置为 `true`，子设备写在 `child_devices` 里。子设备默认继承父设备接口和通信方式；父设备通信失败时，子设备会标记为无法测试。

```json
{
  "device_id": "SER_IO_001",
  "device_name": "IO控制器",
  "device_type": "io_controller",
  "connection_type": "serial",
  "serial_config": {
    "baudrate": 19200,
    "bytesize": 8,
    "parity": "E",
    "stopbits": 1,
    "timeout": 2
  },
  "protocol": {
    "type": "modbus_rtu",
    "slave_id": 1,
    "test_registers": [
      {"address": 0, "count": 8, "description": "DI/DO状态"}
    ]
  },
  "is_intermediate_module": true,
  "child_devices": [
    {
      "device_id": "IO_CHILD_001",
      "device_name": "光电传感器",
      "device_type": "sensor",
      "module_address": 1,
      "protocol": {
        "type": "modbus_rtu",
        "slave_id": 1,
        "test_registers": [
          {"address": 100, "count": 1, "description": "光电输入状态"}
        ]
      }
    }
  ]
}
```

### 透明IO模块

如果 IO 模块只负责转发或映射，页面需要显示实际业务设备，可以把业务设备作为配置主体，并用 `via_device` 记录透明模块。以下配置会在串口下显示“24VDC中间继电器”，通信仍使用现场确认的 XP3018 Modbus RTU 链路：

```json
{
  "device_id": "SER_RELAY_XP3018_001",
  "device_name": "24VDC中间继电器",
  "device_type": "relay",
  "connection_type": "serial",
  "serial_config": {
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "timeout": 2
  },
  "protocol": {
    "type": "modbus_rtu",
    "slave_id": 1,
    "test_registers": [
      {"function_code": 3, "address": 0, "count": 2}
    ]
  },
  "via_device": {
    "device_name": "苏州迅鹏 XP3018 八通道I/O模块",
    "role": "transparent_io"
  },
  "test_scope": "communication_path",
  "is_intermediate_module": false
}
```

`communication_path` 只表示业务设备通信链路可达，不等同于继电器触点已经动作。要验证实际吸合，必须增加安全的输出动作和触点反馈回路。若透明 IO 模块本身也要作为独立设备显示，应读取它独有的型号或版本特征，避免同一响应同时匹配两个设备。

## PCI设备

PCI设备采用被动读取方式，不会向外设发送控制命令。程序遍历 `lspci` 和 sysfs，并按配置中的身份规则匹配，不要求生产机器上的槽位固定。`pci_slot` 仍可用于必须固定槽位的工位。

`pci_match` 支持 `vendor_id`、`device_id`、`subsystem_vendor_id`、`subsystem_device_id`、`class_code`、`driver`、`keywords_any` 和 `keywords_all`。`pci_requirements` 支持 `driver_bound`、`enabled`、`net_interfaces_min` 和 `tty_nodes_min`。

### PCIe网卡

```json
{
  "device_id": "PCI_NET_001",
  "device_name": "PCIe千兆网卡",
  "device_type": "ethernet_controller",
  "connection_type": "pci",
  "pci_match": {
    "vendor_id": "10ec",
    "class_code": "02",
    "driver": ["r8169", "r8168"]
  },
  "pci_requirements": {
    "driver_bound": true,
    "enabled": true,
    "net_interfaces_min": 1
  },
  "is_intermediate_module": false
}
```

### PCIe多串口卡

```json
{
  "device_id": "PCI_SERIAL_001",
  "device_name": "PCIe多串口卡",
  "device_type": "pci_serial_card",
  "connection_type": "pci",
  "pci_match": {
    "class_code": "07",
    "keywords_any": ["serial", "uart", "rs232", "rs485"]
  },
  "pci_requirements": {
    "driver_bound": true,
    "tty_nodes_min": 2
  },
  "is_intermediate_module": true
}
```

## 配置建议

- `device_list.json` 保存允许识别的已知设备及其通信方式；页面只显示实际连接且匹配成功的设备。
- 串口设备尽量不写固定 `interface`，由程序扫描本机可用串口。
- 网口设备写清目标 `ip`、端口、`transport` 和 `device_role`；网卡通常自动选择。
- PCI设备优先使用硬件ID和类别匹配，只有工位规范要求固定插槽时才写 `pci_slot`。
- PLC、远程 IO、OPC UA、EtherNet/IP 多数是 `tcp + server`。
- 相机、扫码器、采集盒如果主动向上位机上报，多数是 `tcp + client` 或 `udp + client`。
- 只读测试优先，避免写线圈、写寄存器、启动、停止、复位等危险操作。
- 修改配置后先运行 `python3 validate_config.py`；部署或升级程序后运行 `python3 self_test.py`，再点击网页检测。两条命令都不会访问现场硬件。
