# Shutdown persistence failures / 关闭服务时的存储故障

`InspectionService.close()` requests cancellation and waits up to two seconds for its worker, as before. If an active task's final `interrupted` write fails, the original database error propagates when resource cleanup succeeds. The service still marks itself closed and closes SQLite; it does not report an unwritten terminal state as durable. Repeated close calls are harmless. Late adapter callbacks cannot update stored evidence.

Database ownership remains held while an old acceptance worker or legacy station operation is still executing. Its existing finalizer releases the lease after that operation exits. A second service must not bypass the lease, delete the `.owner` file to force entry, or replay hardware while old work is running. The companion file's presence is not proof of an active lock.

After the old work stops and storage is repaired, restart with the same data directory. Startup marks unfinished records `interrupted`, retains prior device evidence and does not replay the adapter. Already finished records are unchanged. A failed storage write cannot be made durable by closing resources; back up the database using the procedures in [deployment](DEPLOYMENT.md) before recovery.

关闭服务仍先请求取消、最多等工作线程两秒。活动任务的 `interrupted` 落盘失败时，在资源清理成功的前提下原始数据库错误继续向调用方抛出；服务仍进入已关闭状态并关闭 SQLite，不会假装终态已经保存。重复关闭不重试写入，迟到回调不能改写证据。

旧验收线程或 legacy 操作尚未退出时保留数据库占用锁，由其既有清理路径在退出后释放。不要绕过锁或删除 `.owner` 强行启动第二个服务。旧操作退出、存储修复并按部署说明备份后，再使用同一数据目录启动：未完成任务标为 `interrupted`，不自动重放设备操作，已有终态历史保持。

Offline regression command (Python3.10+, development dependencies):

```bash
python -m pytest -q tests/test_shutdown_storage_failure.py
```

Tests use real temporary SQLite trigger and independent writer-lock faults, actual OS ownership, event-held synthetic adapters, concurrent closers, late callbacks and restart controls. They do not contact field devices. Physical driver termination, storage-media repair and crash/power-loss guarantees are outside this fix. Implementation, tests and this note are AI-assisted.
