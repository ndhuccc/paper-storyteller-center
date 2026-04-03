# 論文說書人中心 / Paper Storyteller Center

🦞 用 AI 將學術論文改寫成「說書人風格」的中文閱讀體驗，支援語意搜尋與 Q&A 對話。

## 功能

- 📚 **論文故事化改寫** — 將 PDF 論文以白話方式重新詮釋
- 🔍 **語意搜尋** — 用自然語言搜尋論文（關鍵字 + 向量）
- 💬 **Q&A 對話** — 對論文內容提問，AI 根據論文內容回答
- 📖 **論文閱覽** — Pop-up Modal 閱覽 HTML 說書人版本
- 📐 **MathJax 支援** — 數學公式正確渲染

## 技術架構

| 元件 | 說明 |
|------|------|
| **Frontend** | Streamlit |
| **Embedding** | Ollama / nomic-embed-text（本地）|
| **Vector DB** | LanceDB（本地，獨立於 OpenClaw）|
| **LLM** | Ollama / qwen3:8b（本地）|

## 安裝

### 1. 安裝依賴

```bash
# Ollama 模型
ollama pull nomic-embed-text
ollama pull qwen3:8b

# Python 套件
pip install lancedb streamlit
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

在搜尋框輸入關鍵字（中英文均可），系統會：
1. 先做關鍵字文字比對（主要）
2. 備用向量語意搜尋（輔助）

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

## 注意事項

- 資料庫與 OpenClaw 記憶庫完全獨立
- 向量資料庫存放於 `papers.lance/`（本地）
- Ollama 需在背景執行
