#!/usr/bin/env bash
# Paper Storyteller Center — 啟動腳本
# 固定使用 pipx 安裝的 Streamlit（含所有必要套件）

PIPX_PYTHON="/home/ccchiang/.local/share/pipx/venvs/streamlit/bin/python"
PIPX_STREAMLIT="/home/ccchiang/.local/bin/streamlit"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 確認環境
echo "🐍 Python: $($PIPX_PYTHON --version)"
echo "📦 Streamlit: $($PIPX_STREAMLIT --version)"

# 切換到專案目錄（讓 import 路徑正確）
cd "$SCRIPT_DIR"

exec "$PIPX_PYTHON" -m streamlit run paper_center_gui.py \
    --server.port 8501 \
    --server.headless true \
    "$@"
