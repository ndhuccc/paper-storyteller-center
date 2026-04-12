#!/usr/bin/env bash
# Paper Story Rewriting Center — 以 python3 server.py 啟動
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec python3 server.py "$@"
