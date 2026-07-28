#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "未找到 Linux 虚拟环境，正在创建..."
  bash ./setup_linux.sh
fi

source .venv/bin/activate
python web_app.py "$@"
