# 論文說書人中心 / Paper Storyteller Center

🦞 一個本地優先的 Hybrid 論文系統：
既能把 PDF 生成「說書人版」HTML，也能對既有 HTML 做索引、搜尋、Q&A 與閱覽。

## 目前狀態

目前系統已完成最小可用 Hybrid 流程：

1. **PDF → 說書人 HTML**
2. **HTML → 向量索引**
3. **搜尋 / Q&A / 閱覽**

也就是說，現在的中心不再只是閱讀 / 搜尋中心，而是已具備最小版的**生成 + 檢索 + 問答**能力。

---

## 功能

- 📚 **生成說書人 HTML**
  - 從單篇 PDF 產生最小可用的 storyteller 風格 HTML
  - 目前為 MVP：先支援 `storyteller` 風格
- 🔍 **語意搜尋**
  - 用自然語言搜尋論文內容（中英文皆可）
  - 底層以 chunk 為檢索單位，再依 `paper_id` 去重成論文結果
- 💬 **Q&A 對話**
  - 對已索引的論文內容提問
  - 使用本地 LLM 回答
- 📖 **論文閱覽**
  - 在 Streamlit 介面中直接開啟 HTML 說書人版本
- 📐 **公式渲染支援**
  - Q&A 回答區：使用 MathJax
  - 內嵌 HTML 閱覽區：優先補 KaTeX auto-render 相容層

---

## 核心規則

### 公式規則

公式必須保留真正的 LaTeX 分隔符：

- `$...$`
- `$$...$$`
- `\(...\)`
- `\[...\]`

**不可**改成 Unicode 偽公式。

### 生成規則

- 預設採 **順序式處理**
- 目前 MVP 只做單篇 PDF → 單篇 HTML
- 先穩定完成，再考慮更複雜的多風格 / 非同步 job UX

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

目前系統已拆成以下模組：

```text
Storytellers/
├── paper_center.py           # CLI 入口（較薄）
├── paper_center_gui.py       # Streamlit GUI
├── center_service.py         # GUI-facing 協調層
├── paper_repository.py       # HTML / metadata / paper list
├── retrieval_service.py      # embedding / LanceDB / search / rebuild
├── qa_service.py             # Q&A context / prompt / answer
├── qa_render.py              # Q&A 回答渲染（LaTeX 保護 + MathJax HTML）
├── html_loader.py            # HTML 載入 / KaTeX 補注入 / anchor fix
├── generation_service.py     # generation job orchestration
├── job_store.py              # JSON job storage
├── storyteller_pipeline.py   # PDF → storyteller HTML 最小真 pipeline
├── papers.lance/             # LanceDB 向量資料庫
├── .jobs/                    # generation jobs（執行時產生）
├── *.html                    # 說書人版 HTML
└── README.md
```

---

## 安裝

### 1. 安裝系統依賴

需要 `pdftotext`：

```bash
# Ubuntu / Debian
sudo apt install poppler-utils
```

### 2. 安裝 Ollama 模型

```bash
ollama pull qwen3-embedding:8b
ollama pull deepseek-r1:8b
```

### 3. 安裝 Python 套件

```bash
pip install lancedb streamlit markdown
```

### 4. 啟動 GUI

```bash
cd ~/Documents/Storytellers
/home/linuxbrew/.linuxbrew/bin/python3 -m streamlit run paper_center_gui.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
```

---

## 使用方式

## A. 直接用 GUI 生成（MVP）

在 GUI 的 **「🛠️ 生成說書」** 面板中：

1. 輸入 PDF 路徑
2. 選擇風格（目前只有 `storyteller`）
3. 選擇是否 `auto_index`
4. 點擊 **「🚀 提交生成任務」**

目前 MVP 做法是：
- 提交後會同步執行
- 成功後會顯示輸出 HTML 路徑
- 下方會顯示最近 generation jobs

## B. 用外部工作流產生 HTML

你也可以先用外部工作簿 / 腳本產生 HTML，再放進：

```bash
~/Documents/Storytellers/
```

然後在 GUI 中點擊：

- `🔄 重建索引`

---

## 搜尋論文

在搜尋框輸入自然語言問題或關鍵字（中英文均可），系統會：

1. 把 query 轉成 embedding
2. 從 chunk 級索引找出最相關內容
3. 依 `paper_id` 去重，顯示論文結果

---

## Q&A 對話

輸入問題後，系統會：

1. 找出相關論文段落
2. 將 context 餵給本地 LLM
3. 輸出可渲染公式的回答

目前 Q&A 顯示採用：

- thinking block 清理
- LaTeX 保護
- Markdown → HTML
- MathJax 渲染

---

## CLI 使用

### 建立 / 重建索引

```bash
cd ~/Documents/Storytellers
python3 paper_center.py init
python3 paper_center.py rebuild
```

### 搜尋

```bash
python3 paper_center.py search "knowledge distillation"
```

### 問答

```bash
python3 paper_center.py ask "這篇論文的核心方法是什麼？"
```

---

## 驗收 / Smoke Test

### 1. Python 編譯檢查

```bash
cd ~/Documents/Storytellers
python3 -m py_compile paper_center.py paper_center_gui.py html_loader.py paper_repository.py retrieval_service.py qa_service.py center_service.py generation_service.py job_store.py storyteller_pipeline.py qa_render.py
```

### 2. Q&A 公式渲染 smoke test

```bash
cd ~/Documents/Storytellers
/home/linuxbrew/.linuxbrew/bin/python3 test_mathjax_render.py
```

輸出：

```bash
/tmp/storyteller_mathjax_smoke.html
```

可用瀏覽器檢查：
- `$...$`
- `$$...$$`
- `\(...\)`
- `\[...\]`
- Markdown 表格 / 粗體

### 3. 最小 generation smoke test（範例）

```python
from storyteller_pipeline import run_storyteller_pipeline

job = {
    'job_id': 'smoke-test',
    'payload': {
        'pdf_path': '/path/to/paper.pdf',
        'max_sections': 2,
        'model': 'deepseek-r1:8b'
    }
}

result = run_storyteller_pipeline(job)
print(result['output_path'])
```

---

## 已知限制（目前 MVP）

- generation 目前只正式支援 `storyteller` 風格
- GUI generation 目前是**同步執行**，不是完整背景 job UX
- 尚未提供完整的 retry / cancel / filter / job detail panel
- `auto_index` 目前是整體 `rebuild_index()`，不是增量索引
- section parsing 目前採保守 heuristic，不是完整結構分析器

---

## 注意事項

- 資料庫與 OpenClaw 記憶庫完全獨立
- 向量資料庫存放於 `papers.lance/`（本地）
- Ollama 需在背景執行
- `pdftotext` 若不存在，generation pipeline 會失敗
- 若 HTML 會嵌入中心閱覽區，系統會優先補 KaTeX auto-render 相容
