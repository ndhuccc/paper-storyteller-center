#!/usr/bin/env bash
# Paper Story Rewriting Center — 啟動腳本 (Flask)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [[ -x "$SCRIPT_DIR/.venv/bin/python3" ]]; then
  PY="$SCRIPT_DIR/.venv/bin/python3"
  echo "🐍 Using project venv: $PY"
else
  PY="python3"
  echo "🐍 Using PATH python3 (no .venv found; run: bash scripts/bootstrap_venv.sh)"
fi
echo "🐍 Version: $($PY --version)"
exec "$PY" server.py "$@"
