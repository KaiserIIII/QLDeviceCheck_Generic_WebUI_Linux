#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 未找到 python3，请先安装 Python 3。"
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo '需要 Python 3.10+'; exit 1; }
install_args=(--require-hashes -r requirements-runtime.lock --disable-pip-version-check)
if [ -d wheels ]; then
  install_args+=(--no-index --find-links wheels)
fi
python -m pip install "${install_args[@]}"

echo "环境准备完成。"
echo "启动网页系统: bash run_web.sh"
echo "浏览器访问: http://127.0.0.1:8080"
