# 論文說書人中心 / Paper Storyteller Center

🦞 用 AI 將學術論文改寫成「說書人風格」的中文閱讀體驗，支援語意搜尋與 Q&A 對話。

## 功能

- 📚 **論文故事化改寫** — 將 PDF 論文以白話方式重新詮釋
- 🔍 **語意搜尋** — 用自然語言做純向量搜尋（中英文皆可）
- 💬 **Q&A 對話** — 對論文內容提問，AI 根據論文內容回答
- 📖 **論文閱覽** — Pop-up Modal 閱覽 HTML 說書人版本
- 📐 **MathJax 支援** — 數學公式正確渲染

## 技術架構

| 元件 | 說明 |
|------|------|
| **Frontend** | Streamlit |
| **Embedding** | Ollama / qwen3-embedding:8b（本地）|
| **Vector DB** | LanceDB（本地，獨立於 OpenClaw）|
| **LLM** | Ollama / deepseek-r1:8b（本地，可替換）|

## 安裝

### 1. 安裝依賴

```bash
# Ollama 模型
ollama pull qwen3-embedding:8b
ollama pull deepseek-r1:8b

# Python 套件
pip install lancedb streamlit markdown
```

### 2. 建立索引

```bash
cd ~/Documents/Storytellers
python3 paper_center.py init
```

### 3. 啟動 GUI

```bash
streamlit run paper_center_gui.py --server.port 8501
```

## 使用方式

### 新增論文

1. 使用「論文說書人」工作簿（OpenClaw workbook 10）產生 HTML
2. 輸出到 `~/Documents/Storytellers/` 目錄
3. 在 GUI 點擊「🔄 重建索引」

### 搜尋論文

在搜尋框輸入自然語言問題或關鍵字（中英文均可），系統會：
1. 先將查詢轉為向量
2. 從 chunk 級索引找出最相關內容
3. 依 `paper_id` 去重後顯示論文結果

### Q&A 對話

輸入問題後，系統會：
1. 找出相關論文段落
2. 餵給本地 LLM 生成回答
3. 標註參考來源

## 目錄結構

```
Storytellers/
├── paper_center.py         # 核心模組（CLI 工具）
├── paper_center_gui.py     # Streamlit GUI
├── papers.lance/           # LanceDB 向量資料庫
├── *.html                  # 論文說書人版本
└── README.md
```

## 渲染 smoke test

若你改了 Q&A 顯示邏輯，可先跑：

```bash
cd ~/Documents/Storytellers
/home/linuxbrew/.linuxbrew/bin/python3 test_mathjax_render.py
```

它會輸出 `/tmp/storyteller_mathjax_smoke.html`，可用瀏覽器檢查：
- `$...$`
- `$$...$$`
- `\\(...\\)`
- `\\[...\\]`
- Markdown 表格 / 粗體

## 注意事項

- 資料庫與 OpenClaw 記憶庫完全獨立
- 向量資料庫存放於 `papers.lance/`（本地）
- Ollama 需在背景執行
