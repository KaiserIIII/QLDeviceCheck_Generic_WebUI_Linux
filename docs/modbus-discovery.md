# Modbus TCP discovery response integrity / 发现响应校验

TCP connection and Modbus response are separate evidence. An open port remains a TCP connection result when protocol discovery receives unrelated, truncated or mismatched bytes; it must not be relabelled Modbus merely because data arrived.

端口连通与协议有效性分别记录。标准寄存器发现使用与配置读检查相同的MBAP关联、功能和数量验证；基本设备识别验证固定43/14、MEI14、访问码1、结构字段与有界对象列表。两条路径均使用既有有界收包逻辑，支持TCP任意分片；超时预算仍为0.45秒、上限260字节，socket始终在finally关闭。

The read probe reuses the existing response validator and preserves configured unit IDs, including TCP0. The fallback device-identification probe retains its existing transaction1/unit1/basic-object0 request. It validates the first returned page only; pagination metadata is reported without issuing more requests. Valid Modbus exceptions indicate protocol reachability, not successful register reads or identified model names. Returned object bytes are not authenticated device identity.

The fixed basic-object0 response must start at object0 and contain a nonempty contiguous prefix of basic objects0/1/2. A final first page contains all three mandatory objects; a continuation points to the next remaining basic object. Empty/non-progressing pages and regular/extended objects are refused. TCP response validators reject exception bytes outside the defined code set1/2/3/4/5/6/8/0A/0B, even when exceptions are allowed. This shared TCP check also covers configured reads; RTU exception handling is unchanged by this correction.

兼容范围：既有只读请求字节、配置轮询上限和超时不变。合法异常帧保留为协议可达，摘要明确异常；缺失或错位事务/单元、非法长度、错功能/MEI/访问码和不完整对象不会产生Modbus分类。基础识别不提供厂商认证、完整设备型谱或自动分页；其他访问码及厂商扩展不由本固定查询处理。

Verification uses deterministic byte sockets, all single-split positions for representative responses and real loopback TCP. It does not connect to field hardware or establish electrical performance. The broader application still needs independent security and hardware validation; this bounded correction does not certify the service.

Protocol references: [Modbus Application Protocol V1.1b3, §6.21 and §7](https://www.modbus.org/file/secure/modbusprotocolspecification.pdf), [Modbus TCP/IP Implementation Guide, MBAP header](https://www.modbus.org/file/secure/messagingimplementationguide.pdf).

Implementation, tests and documentation are AI-assisted. / 本次实现、测试与说明为AI辅助。
