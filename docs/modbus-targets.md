# Modbus read target identity / Modbus 只读目标标识

Configured response-bearing RTU reads require `slave_id` in **1–247**. Address 0 is serial broadcast; this workbench does not perform broadcast read acceptance. Reserved addresses 248–255 are rejected for RTU reads.

Modbus TCP `unit_id` supports **0–255**, including explicit numeric `0` and `255`. Omitting either target field still defaults to `1`. A configured value is never reduced modulo 256 or replaced merely because it is zero.

RTU 只读验收使用 1–247 的单播站号；串口广播 0 与保留站号不能用于需要响应的验收。TCP 单元标识支持 0–255，明确配置的 0 保持为 0，字段缺失仍默认 1。

Configuration accepts existing numeric-string and exact integral-float forms, such as `"247"`, `247.0` and `"0"`. Booleans, fractional or nonfinite numbers, missing-type values such as null, and out-of-range targets are invalid. Pure byte encoders/response helpers require actual Python integers and raise `ProtocolConfigError` for invalid expected targets. Configuration validation returns an input error before an adapter runs.

配置层保留整数形式字符串及整数值浮点数的兼容性，但拒绝布尔值、小数、非有限数、null 和越界值。底层字节 API 要求真正的整数。响应检查不会把错误目标掩码成另一个合法设备。

This is a compatibility correction for invalid targets and explicit TCP zero, not physical device certification. Raw custom request bytes still need operator review. Valid nonzero read packets keep their original encoding.

Reference: [Modbus TCP/IP Implementation Guide V1.0b](https://www.modbus.org/file/secure/messagingimplementationguide.pdf), MBAP fields pp5–6 and target routing p23. Automated evidence uses pure packets, FakeSocket and loopback HTTP only; no hardware was contacted. Engineering and regression additions are AI-assisted.
