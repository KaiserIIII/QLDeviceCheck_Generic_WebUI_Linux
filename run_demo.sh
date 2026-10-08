#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
bash ./run_web.sh --demo --data-dir data/demo "$@"
