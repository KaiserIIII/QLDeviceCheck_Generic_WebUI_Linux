# Release 安装与使用

下载 [GitHub Release](https://github.com/KaiserIIII/QLDeviceCheck_Generic_WebUI_Linux/releases) 中对应平台的包：

| 包 | 用途 |
| --- | --- |
| `QLDeviceCheck-v3.0.0-windows-x64.zip` | Windows x64，含离线 psutil/pyserial 运行依赖 |
| `QLDeviceCheck-v3.0.0-linux-x86_64.zip` | Linux x86_64/glibc，含离线运行依赖 |
| `SHA256SUMS.txt` | 下载文件校验值 |

需要预先安装 **CPython 3.10+**，包括 venv/pip。包不包含 Python 解释器；ARM、其他架构及 Python 自由线程构建需要匹配的依赖制品。Linux 的 psutil wheel 支持 glibc 2.12+ 的常规 CPython 构建。项目方已确认项目测试可用；本次 Release 的检查结果见 [VALIDATION.md](VALIDATION.md)。

## Windows

1. 解压到可写目录。
2. 双击 `Start-Demo.cmd`。首次启动自动创建 `.venv`，从随包 `wheels/` 离线安装固定依赖。
3. 浏览器打开 `http://127.0.0.1:8080`，保留终端窗口；`Ctrl+C` 关闭服务。

执行真实验收使用 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_web.ps1 --config config\device_list.json --data-dir data\live
```

## Linux

```bash
unzip QLDeviceCheck-v3.0.0-linux-x86_64.zip
cd QLDeviceCheck-v3.0.0
bash setup_linux.sh
bash run_demo.sh
# 真实验收使用审核后的设备配置：
bash run_web.sh --config config/device_list.json --data-dir data/live
```

安装器检测到 `wheels/` 时只使用本地依赖，校验锁定的 SHA-256。源码包不含 wheels 时使用 PyPI 安装同一版本。每个发行包包含 `RELEASE-MANIFEST.json`，记录对应的 Git commit、平台、依赖来源/许可证/哈希。

## 数据与更新

默认监听本机；网络使用和访问令牌见 [部署说明](DEPLOYMENT.md)。任务在选定的 `data` 目录保留。升级时先停止服务，备份数据和配置，将新版本解压到新目录，再用 `--data-dir` 指向原数据目录。不要复制旧虚拟环境到新平台，重新运行安装器。旧扫描报告位于 `report/`，需要单独保留。

同一设备工位避免并发启动多个实例；同一数据库由独占锁保护。关闭服务后可打包备份，任务配置快照仍在记录中。Release 来源通过标签固定，PR 保留便于审阅，不自动合并主分支。

离线 wheel 选择与哈希校验遵循 [pip 官方下载说明](https://pip.pypa.io/en/stable/cli/pip_download/)和[安全安装说明](https://pip.pypa.io/en/stable/topics/secure-installs/)。
