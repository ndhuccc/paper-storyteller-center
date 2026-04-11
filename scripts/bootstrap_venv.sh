#!/usr/bin/env bash
# Create .venv in repo root and install requirements (avoids Homebrew PEP 668 pip errors).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
"$ROOT/.venv/bin/python" -m pip install -U pip
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
echo "Done. Activate with: source $ROOT/.venv/bin/activate"
echo "Then run: python3 server.py   or   python3 scripts/md_rewrite_sample_to_tmp.py --style blog"
