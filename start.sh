#!/usr/bin/env bash
# Paper Storyteller Center — 啟動腳本 (Flask)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "🐍 Python: $(python3 --version)"
cd "$SCRIPT_DIR"
exec python3 server.py "$@"
