# 論文說書改寫中心 / Paper Story Rewriting Center

🦞 一個本地優先的 Hybrid 論文系統：
既能把 PDF 生成「說書人版」HTML，也能對既有 HTML 做索引、搜尋、Q&A 與閱覽。

## 使用手冊

- 詳細中文操作手冊請見：[使用手冊.md](使用手冊.md)

## 目前狀態

目前系統以 Flask Web 介面為主，核心流程已完整可用：

1. **PDF → 說書人 HTML**（非同步背景執行，支援 7 種風格）
2. **HTML → 向量索引**（auto-index 走 incremental sync，支援 runtime fallback）
3. **語意搜尋 / Q&A / HTML 閱覽**
4. **Paper manifest**（merged HTML + index，具備 canonical status）
5. **Generation job 管理**（submit / run / status / retry / cancel / handoff）
6. **Paper 管理**（刪除、重新命名、編輯顯示名稱）
7. **側欄內建使用說明**（Help / User Manual）

---

## 功能

- 📚 **說書改寫（PDF 或手動單元）**
  - 從單篇 PDF 產生 storyteller 風格 HTML
  - 支援手動輸入改寫單元（不經 PDF）
  - 非同步背景執行，不阻塞 GUI
  - 可選 auto-index（改寫後自動做 incremental index sync）
- 🧰 **Generation job 管理**
  - 任務列表、狀態追蹤、失敗重試（retry）、進行中取消（cancel）
  - 成功後可直接 handoff 到開啟 / 搜尋 / 詢問
  - 前端會每 8 秒輪詢任務狀態
- 🗂️ **論文管理**
  - 刪除論文（同步清除 LanceDB 索引與本地 HTML，不可復原）
  - 重新命名檔名
  - 編輯顯示名稱（不動檔名與 paper_id）
- 🔍 **語意搜尋**
  - 以 chunk 為檢索單位，依 `paper_id` 去重成論文結果
- 💬 **Q&A 對話**
  - 支援 forced paper selection（可先勾選論文再提問）
  - 支援引擎優先順序調整與 fallback
- 📖 **論文閱覽**
  - Web modal 內嵌 HTML，支援下載
- 📐 **公式渲染**
  - Q&A 區：MathJax
  - 內嵌 HTML 閱覽區：KaTeX auto-render
- 📊 **Paper manifest / status**
  - 每篇論文有明確狀態：`ready` / `generated_not_indexed` / `index_only` / `unavailable`
  - sidebar 顯示狀態 badge
- ❓ **使用說明（User Manual）**
  - 側欄內建精簡操作指南（生成、狀態、搜尋/Q&A、刪除管理）

---

## 核心規則

### 公式規則

- 保留真正 LaTeX 分隔符：`$...$`、`$$...$$`、`\(...\)`、`\[...\]`
- **不可**改成 Unicode 偽公式

### 生成規則

- 預設順序式處理
- 支援 storyteller / blog / professor / fairy / lazy / question / log 風格
- 各風格可在 Web UI 以 slider 調整參數

### Runtime 規則

- 背景 job 與 auto-index 會自動偵測正確的 Python runtime
- 優先順序：環境變數 override → 目前 interpreter → linuxbrew fallback
- 環境變數：`PAPER_STORYTELLER_PYTHON` 或 `STORYTELLER_PYTHON`

---

## 技術架構


| 元件              | 說明                                  |
| --------------- | ----------------------------------- |
| **Frontend**    | Flask + Alpine.js 單頁介面              |
| **Backend API** | Flask Blueprint（/api）               |
| **Embedding**   | Ollama / `qwen3-embedding:8b`（本地）   |
| **Q&A LLM**     | Gemini（主）+ Ollama/MiniMax（fallback） |
| **Rewrite LLM** | Gemini（主）+ Ollama/MiniMax（fallback） |
| **Vector DB**   | LanceDB（本地）                         |
| **PDF 抽字**      | `PyMuPDF` (fitz)                    |
| **HTML 公式**     | 全面統一使用 KaTeX auto-render            |


---

## 模組分層

```text
project-root/
├── server.py                 # Flask 入口
├── start.sh                  # 一鍵啟動腳本
├── webapp/                   # 前端模板、靜態資源、路由
│   ├── routes/api.py         # REST API
│   ├── templates/index.html  # 主頁
│   └── static/js/app.js      # 前端互動與任務輪詢
├── center_service.py         # Web-facing 協調層
├── generation_service.py     # generation jobs + auto-index
├── storyteller_pipeline.py   # PDF / manual sections → HTML
├── retrieval_service.py      # embedding / LanceDB / search / index sync
├── paper_repository.py       # manifest / paper status / metadata
├── qa_service.py             # Q&A pipeline
├── job_store.py              # job JSON storage
├── runtime_support.py        # Python runtime selection / fallback
└── paper_center.py           # CLI 入口（輕量）
```

---

## 安裝

### 1. 系統依賴

已不需 `poppler-utils`，改用 Python 套件 `pymupdf` 解析 PDF 結構。

### 2. Ollama 模型

```bash
ollama pull qwen3-embedding:8b
ollama pull gemma4:e2b
ollama pull deepseek-r1:8b
```

### 3. Python 套件

若出現 `**externally-managed-environment`（PEP 668）**，代表目前的 `python3` 是 **Homebrew** 等系統管理環境，**不要**對它直接 `pip install`。請擇一：

**做法 A（建議）：專案虛擬環境**

```bash
cd /path/to/paper-storyteller-center
bash scripts/bootstrap_venv.sh
source .venv/bin/activate
```

之後在同一終端機請一律用已啟用的 `python3`／`pip`（會指向 `.venv`）。

**做法 B：已使用 Conda（你的提示字為 `(base)` 時）**

請改用 **Conda 環境裡的 Python** 安裝，不要用到 Homebrew 的 `python3`：

```bash
conda install -n base -c conda-forge google-genai
# 其餘套件仍可用 pip，但請指定 conda 的解譯器，例如：
"$(conda info --base)/bin/python" -m pip install flask flask-cors markdown lancedb pymupdf python-dotenv
```

或先 `which python3`，若顯示在 `anaconda3` 底下，再執行：

```bash
python3 -m pip install flask flask-cors markdown lancedb pymupdf python-dotenv google-genai
```

**做法 C：手動建立 venv（與 A 相同效果）**

```bash
cd /path/to/paper-storyteller-center
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. 啟動 Web 服務

請先完成 **§3**（專案內建議有 `.venv` 並已 `pip install -r requirements.txt`）。

```bash
cd /path/to/paper-storyteller-center
./start.sh
```

- `**start.sh` 行為**：若專案根目錄存在 `**.venv/bin/python3`**，會**自動**用它啟動 `server.py`，**不必**先手動 `source .venv/bin/activate`。
- **手動啟動**：若不用 `start.sh`，請先啟用 venv，再執行 `server.py`：

```bash
cd /path/to/paper-storyteller-center
source .venv/bin/activate
python3 server.py
```

或直接指定解譯器（未 `activate` 時）：

```bash
.venv/bin/python3 server.py
```

**其他腳本**（例如 `scripts/md_rewrite_sample_to_tmp.py`）：請在**已啟用**的 venv 終端機內執行，或一律使用：

```bash
.venv/bin/python3 scripts/md_rewrite_sample_to_tmp.py --style blog
```

---

## 使用方式

### A. 從 Web UI 生成

1. 在 `🛠️ 說書改寫` 面板上傳 PDF（或改用手動輸入單元）
2. 選風格（storyteller / blog / professor / fairy / lazy / question / log）
3. 選是否 `auto_index`
4. 提交 → 背景執行
5. 在 recent jobs 追蹤狀態（前端會定期刷新）
6. 成功後可直接：
  - `📖 開啟改寫結果`
  - `🔍 搜尋這篇`
  - `💬 詢問這篇`

### B. 用外部工作流產生 HTML

1. 把 HTML 放進 `~/Documents/Storytellers/`
2. GUI 側欄 `🔄 重建索引`

---

## CLI 使用

```bash
cd /path/to/paper-storyteller-center
python3 paper_center.py init      # 建立索引
python3 paper_center.py rebuild   # 重建索引
python3 paper_center.py search "knowledge distillation"
python3 paper_center.py ask "這篇論文的核心方法是什麼？"
```

---

## 驗收 / Smoke Test

### 1. Python 編譯檢查

```bash
cd /path/to/paper-storyteller-center
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


| 版本   | 主題             | 重點                                                                                  |
| ---- | -------------- | ----------------------------------------------------------------------------------- |
| v0.6 | Hybrid MVP     | 架構分層 + 最小 generation pipeline + GUI 入口                                              |
| v0.7 | Generation UX  | 非同步 job + 狀態顯示 + 回流入口 + repository 純化                                               |
| v0.8 | Index 聯動       | Merged manifest + canonical status + handoff 穩定化 + auto-index 細化 + runtime fallback |
| v0.9 | 風格與品質          | 7 種改寫風格 + section parsing 改良 + Q&A citation 強化                                      |
| v1.0 | Product Polish | retry/cancel job + 論文刪除管理 + GUI 使用說明 + 文件收斂                                         |


---

## 已知限制

- generation 支援 7 種風格（storyteller / blog / professor / fairy / lazy / question / log）
- generation 為非同步背景 job，前端採固定週期輪詢（非事件推送）
- `auto_index` 目前為 incremental sync；全量重建仍可手動觸發
- section parsing 採保守 heuristic
- `paper_id` 主要由 output filename stem 推算

---

## 注意事項

- 資料庫與 OpenClaw 記憶庫完全獨立
- Ollama 需在背景執行
- `pymupdf` 必須已安裝
- HTML 說書人頁面皆已統一內嵌 KaTeX 渲染引擎
- LanceDB 需透過正確 Python runtime 執行（已有 `runtime_support.py` 處理）

