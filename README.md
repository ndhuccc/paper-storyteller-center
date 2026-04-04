# 論文說書人中心 / Paper Storyteller Center

🦞 一個本地優先的 Hybrid 論文系統：
既能把 PDF 生成「說書人版」HTML，也能對既有 HTML 做索引、搜尋、Q&A 與閱覽。

## 目前狀態

目前系統已完成 v0.8 核心功能：

1. **PDF → 說書人 HTML**（非同步背景執行）
2. **HTML → 向量索引**（auto-index 支援 runtime fallback）
3. **搜尋 / Q&A / 閱覽**
4. **Paper manifest**（merged HTML + index，具備 canonical status）
5. **Generation job 管理**（submit / run / status / handoff）

---

## 功能

- 📚 **生成說書人 HTML**
  - 從單篇 PDF 產生 storyteller 風格 HTML
  - 非同步背景執行，不阻塞 GUI
  - 可選 auto-index（生成後自動重建索引）
- 🔍 **語意搜尋**
  - 以 chunk 為檢索單位，依 `paper_id` 去重成論文結果
- 💬 **Q&A 對話**
  - 本地 LLM 回答，支援 forced paper selection
- 📖 **論文閱覽**
  - Streamlit dialog 內嵌 HTML，優先補 KaTeX auto-render
- 📐 **公式渲染**
  - Q&A 區：MathJax
  - 內嵌 HTML 閱覽區：KaTeX auto-render
- 📊 **Paper manifest / status**
  - 每篇論文有明確狀態：`ready` / `generated_not_indexed` / `index_only` / `unavailable`
  - sidebar 顯示狀態 badge

---

## 核心規則

### 公式規則
- 保留真正 LaTeX 分隔符：`$...$`、`$$...$$`、`\(...\)`、`\[...\]`
- **不可**改成 Unicode 偽公式

### 生成規則
- 預設順序式處理
- MVP 只做單篇 PDF → 單篇 HTML
- 目前只正式支援 `storyteller` 風格

### Runtime 規則
- 背景 job 與 auto-index 會自動偵測正確的 Python runtime
- 優先順序：環境變數 override → 目前 interpreter → linuxbrew fallback
- 環境變數：`PAPER_STORYTELLER_PYTHON` 或 `STORYTELLER_PYTHON`

---

## 技術架構

| 元件 | 說明 |
|------|------|
| **Frontend** | Streamlit |
| **Embedding** | Ollama / `qwen3-embedding:8b`（本地） |
| **Q&A LLM** | Ollama / `deepseek-r1:8b`（本地） |
| **Vector DB** | LanceDB（本地） |
| **PDF 抽字** | `pdftotext` |
| **HTML 公式** | 內嵌頁優先 KaTeX；Q&A 區使用 MathJax |

---

## 模組分層

```text
Storytellers/
├── paper_center.py           # CLI 入口（較薄）
├── paper_center_gui.py       # Streamlit GUI
├── center_service.py         # GUI-facing 協調層
├── paper_repository.py       # HTML / metadata / manifest / paper status
├── retrieval_service.py      # embedding / LanceDB / search / rebuild
├── qa_service.py             # Q&A context / prompt / answer
├── qa_render.py              # Q&A 回答渲染（LaTeX 保護 + MathJax HTML）
├── html_loader.py            # HTML 載入 / KaTeX 補注入 / anchor fix
├── generation_service.py     # generation job orchestration + auto-index
├── job_store.py              # JSON job storage
├── storyteller_pipeline.py   # PDF → storyteller HTML pipeline
├── runtime_support.py        # Python runtime selection / fallback
├── papers.lance/             # LanceDB 向量資料庫
├── .jobs/                    # generation jobs（執行時產生）
├── *.html                    # 說書人版 HTML
├── test_mathjax_render.py    # Q&A 公式渲染 smoke test
└── README.md
```

---

## 安裝

### 1. 系統依賴

```bash
# Ubuntu / Debian
sudo apt install poppler-utils
```

### 2. Ollama 模型

```bash
ollama pull qwen3-embedding:8b
ollama pull deepseek-r1:8b
```

### 3. Python 套件

```bash
/home/linuxbrew/.linuxbrew/bin/pip3 install lancedb streamlit markdown
```

### 4. 啟動 GUI

```bash
cd ~/Documents/Storytellers
/home/linuxbrew/.linuxbrew/bin/python3 -m streamlit run paper_center_gui.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
```

---

## 使用方式

### A. 從 GUI 生成
1. 在 `🛠️ 生成說書` 面板輸入 PDF 路徑
2. 選風格（目前 `storyteller`）
3. 選是否 `auto_index`
4. 提交 → 背景執行
5. 在 recent jobs 追蹤狀態
6. 成功後可直接：
   - `📖 開啟生成結果`
   - `🔍 搜尋這篇`
   - `💬 詢問這篇`

### B. 用外部工作流產生 HTML
1. 把 HTML 放進 `~/Documents/Storytellers/`
2. GUI 側欄 `🔄 重建索引`

---

## CLI 使用

```bash
cd ~/Documents/Storytellers
python3 paper_center.py init      # 建立索引
python3 paper_center.py rebuild   # 重建索引
python3 paper_center.py search "knowledge distillation"
python3 paper_center.py ask "這篇論文的核心方法是什麼？"
```

---

## 驗收 / Smoke Test

### 1. Python 編譯檢查

```bash
cd ~/Documents/Storytellers
python3 -m py_compile paper_center.py paper_center_gui.py html_loader.py paper_repository.py retrieval_service.py qa_service.py center_service.py generation_service.py job_store.py storyteller_pipeline.py qa_render.py runtime_support.py
```

### 2. Q&A 公式渲染

```bash
/home/linuxbrew/.linuxbrew/bin/python3 test_mathjax_render.py
```

### 3. Manifest 驗證

```python
from paper_repository import build_paper_manifest
for p in build_paper_manifest():
    print(p['paper_id'], p['paper_status'], p['manifest_source'])
```

### 4. Runtime 選擇驗證

```python
from runtime_support import select_preferred_python
print(select_preferred_python(required_modules=('lancedb',)))
```

---

## 版本歷史

| 版本 | 主題 | 重點 |
|------|------|------|
| v0.6 | Hybrid MVP | 架構分層 + 最小 generation pipeline + GUI 入口 |
| v0.7 | Generation UX | 非同步 job + 狀態顯示 + 回流入口 + repository 純化 |
| v0.8 | Index 聯動 | Merged manifest + canonical status + handoff 穩定化 + auto-index 細化 + runtime fallback |

---

## 已知限制

- generation 目前只正式支援 `storyteller` 風格
- GUI generation 採非同步 job，但無即時輪詢刷新
- 無 retry / cancel 機制
- `auto_index` 目前仍是 full rebuild
- section parsing 採保守 heuristic
- `paper_id` 主要由 output filename stem 推算

---

## 注意事項

- 資料庫與 OpenClaw 記憶庫完全獨立
- Ollama 需在背景執行
- `pdftotext` 必須已安裝
- 若 HTML 嵌入中心閱覽區，系統會優先補 KaTeX auto-render
- LanceDB 需透過正確 Python runtime 執行（已有 `runtime_support.py` 處理）
