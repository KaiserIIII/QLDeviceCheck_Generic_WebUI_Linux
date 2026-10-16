# Modbus RTU read exceptions / 只读异常响应

The configured RTU read probe validates station, function, frame length and CRC before considering an exception. `accept_exception: true` permits a defined exception response as communication evidence; it does not make an arbitrary exception byte valid or prove that a register was read successfully.

RTU now uses the same existing exception-code contract as the TCP validators: `01 02 03 04 05 06 08 0A 0B`. A byte outside that set fails even when exceptions are enabled. The original frame remains in the connection evidence with a diagnostic identifying the unsupported code. The flag's default false behaviour and all defined-code acceptance messages remain unchanged.

配置允许异常不等于绕过报文语义。比如`01 83 00 41 30`的CRC正确，但异常码00不在当前只读探针合同中，因此返回“需确认”；`01 83 02 C0 F1`是已有合同支持的异常，仍仅在明确允许异常时通过，并保留异常说明。它不代表寄存器读取成功、型号认证或设备健康。

The parser retains its existing first-matching-frame policy, including a rejected exception. Configured `any` mode can continue to a later probe; `all` mode stops at a failed probe. The change does not add serial reads/writes, retries, transport framing, discovery scans or new timeout behaviour. Normal response bytes and read-only requests remain unchanged. Legacy/vendor-specific profiles outside this contract require an explicit matching protocol; this check is not a declaration that every legacy diagnostic function uses this set.

回归覆盖支持/不支持的异常码、CRC错误、截断、站号/功能不匹配，以及实际配置检测方法的Fake串口any/all行为与端口关闭。测试不打开真实串口，不向未知设备写入，不执行现场认证。

```bash
python -m pytest -q tests/test_modbus_rtu_exceptions.py
```

References: [Modbus Application Protocol V1.1b3 §7](https://www.modbus.org/file/secure/modbusprotocolspecification.pdf), [Serial Line V1.02 CRC/framing](https://www.modbus.org/file/secure/modbusoverserial.pdf). Prior TCP discovery work is documented separately in [modbus-discovery.md](modbus-discovery.md).

Implementation, regression tests and documentation are AI-assisted. / 本次实现、回归及文档均AI辅助。
