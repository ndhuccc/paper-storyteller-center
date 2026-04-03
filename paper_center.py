#!/usr/bin/env python3
"""
論文說書人中心 - 核心模組
功能：論文索引建立、語意搜尋、Q&A 對話
"""

import os
import json
import html
from pathlib import Path
from typing import List, Dict, Optional
import urllib.request
import urllib.parse

# ==================== 配置 ====================
STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
LANCEDB_PATH = STORYTELLERS_DIR / "papers.lance"
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"  # 本地 embedding 模型（限制 1000 tokens）

# ==================== LanceDB ====================
def get_lance_db():
    """取得論文說書人中心的 LanceDB 連接"""
    try:
        import lancedb
        return lancedb.connect(str(LANCEDB_PATH))
    except ImportError:
        print("⚠️ 請先安裝 lancedb: pip install lancedb")
        return None


def init_database():
    """初始化資料庫結構"""
    db = get_lance_db()
    if db is None:
        return False
    
    # 檢查表是否存在
    if "papers" in db.table_names():
        print("✅ 資料庫已存在")
        return True
    
    # 建立表（需要先有 schema）
    print("⚠️ 請先執行 'init' 建立索引")
    return False


def create_table_if_not_exists(db):
    """建立論文表"""
    import lancedb
    import pyarrow as pa
    
    # 使用 pyarrow schema，正確定義 vector 類型
    # embedding 是 768 維向量 (nomic-embed-text)
    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("filename", pa.string()),
        pa.field("title", pa.string()),
        pa.field("authors", pa.string()),
        pa.field("date", pa.string()),
        pa.field("keywords", pa.list_(pa.string())),
        pa.field("content", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), list_size=768)),  # 768維向量
    ])
    
    try:
        tbl = db.create_table("papers", schema=schema)
        return tbl
    except Exception as e:
        print(f"建立表時出錯: {e}")
        return None


# ==================== Embedding ====================
def get_embedding(text: str) -> List[float]:
    """使用本地 Ollama 取得文本的 embedding"""
    # 限制文本長度，避免 500 錯誤（nomic-embed-text 限制約 1000 tokens）
    # 中文字大約 1 token = 1-2 字元，設定 1500 為安全上限
    text = text[:1500]
    
    url = f"{OLLAMA_BASE_URL}/api/embeddings"
    
    data = {
        "model": EMBEDDING_MODEL,
        "prompt": text
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get('embedding', [])
    except Exception as e:
        print(f"⚠️ Embedding API 錯誤: {e}")
        return []


# ==================== 論文解析 ====================
def extract_text_from_html(html_content: str) -> str:
    """從 HTML 提取純文字"""
    import re
    
    # 移除 script 和 style
    text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # 移除 HTML 標籤
    text = re.sub(r'<[^>]+>', ' ', text)
    
    # 清理空白
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def parse_paper_metadata(filename: str) -> Dict:
    """從 HTML 檔案解析論文資訊"""
    filepath = STORYTELLERS_DIR / filename
    
    if not filepath.exists():
        return None
    
    html_content = filepath.read_text(encoding='utf-8')
    plain_text = extract_text_from_html(html_content)
    
    # 解析標題
    title_match = re.search(r'<title>([^<]+)</title>', html_content)
    title = title_match.group(1) if title_match else filename.replace('.html', '')
    
    # 解析作者
    author_match = re.search(r'作者[：:]\s*([^<\n]+)', plain_text)
    authors = author_match.group(1).strip() if author_match else "未知"
    
    # 解析日期
    date_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', plain_text)
    date = date_match.group(1) if date_match else "未知"
    
    return {
        "id": filename.replace('.html', ''),
        "filename": filename,
        "title": title,
        "authors": authors,
        "date": date,
        "content": plain_text
    }


# ==================== 索引建立 ====================
def rebuild_index():
    """重建論文索引"""
    db = get_lance_db()
    if db is None:
        print("❌ 無法連接資料庫")
        return False
    
    # 取得或建立表
    try:
        if "papers" in db.table_names():
            tbl = db.open_table("papers")
            # 清空舊資料
            tbl.delete("true")
        else:
            tbl = create_table_if_not_exists(db)
            if tbl is None:
                print("❌ 無法建立表")
                return False
    except Exception as e:
        print(f"建立/開啟表時出錯: {e}")
        return False
    
    # 掃描 HTML 檔案
    html_files = list(STORYTELLERS_DIR.glob("*.html"))
    print(f"📂 找到 {len(html_files)} 篇論文")
    
    papers = []
    for html_file in html_files:
        paper = parse_paper_metadata(html_file.name)
        if paper:
            # 生成 embedding
            print(f"  📄 處理中: {paper['title'][:40]}...")
            content_for_embedding = f"{paper['title']}\n\n{paper['content'][:5000]}"
            embedding = get_embedding(content_for_embedding)
            
            paper["embedding"] = embedding
            papers.append(paper)
    
    if papers:
        # 新增到資料庫
        tbl.add(papers)
        print(f"✅ 已建立索引: {len(papers)} 篇論文")
        return True
    
    return False


# ==================== 搜尋 ====================
def search_papers(query: str, top_k: int = 3) -> List[Dict]:
    """語意搜尋論文"""
    # 生成 query 的 embedding
    query_embedding = get_embedding(query)
    if not query_embedding:
        print("⚠️ 無法生成 query embedding")
        return []
    
    db = get_lance_db()
    if db is None:
        return []
    
    try:
        tbl = db.open_table("papers")
        
        # 使用 LanceDB 的 vector search
        results = tbl.search(query_embedding, vector_column_name="embedding").limit(top_k).to_list()
        
        return results
    except Exception as e:
        print(f"搜尋時出錯: {e}")
        return []


# ==================== Q&A ====================
def answer_question(question: str, context_limit: int = 3) -> tuple[str, List[Dict]]:
    """使用 RAG 回答問題"""
    # 1. 搜尋相關內容
    results = search_papers(question, top_k=context_limit)
    
    if not results:
        return "抱歉，沒有找到相關的論文內容。", []
    
    # 2. 組合成 context
    context_parts = []
    for r in results:
        context_parts.append(f"=== {r.get('title', '未知標題')} ===\n{r.get('content', '')[:2000]}")
    
    context = "\n\n".join(context_parts)
    
    # 3. 送給 LLM 生成答案
    prompt = f"""你是一個專業的論文說書人，擅長比較和解釋學術論文的內容。

根據以下論文內容，回答用戶的問題。如果論文內容沒有相關資訊，請如實說「根據現有論文資料，我沒有找到相關資訊」。

=== 論文內容 ===
{context}

=== 用戶問題 ===
{question}

請用繁體中文回答，並且引用相關的論文標題："""
    
    # 使用本地 Ollama 的 LLM
    try:
        url = f"{OLLAMA_BASE_URL}/api/generate"
        data = {
            "model": "qwen/qwen3-8b",  # 使用較小的模型
            "prompt": prompt,
            "stream": False
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            answer = result.get('response', '抱歉，無法生成回答')
            
            return answer.strip(), results
            
    except Exception as e:
        print(f"LLM API 錯誤: {e}")
        return f"抱歉，生成回答時發生錯誤：{e}", results


# ==================== 主程式 ====================
if __name__ == "__main__":
    import sys
    import re
    
    if len(sys.argv) < 2:
        print("""
📚 論文說書人中心 - 使用說明

用法：
  python3 paper_center.py init      - 初始化資料庫並建立索引
  python3 paper_center.py rebuild  - 重建全部索引
  python3 paper_center.py search   - 搜尋論文
  python3 paper_center.py ask      - 問問題
        """)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "init":
        print("📚 初始化論文說書人中心...")
        init_database()
        rebuild_index()
        
    elif command == "rebuild":
        print("🔄 重建索引...")
        rebuild_index()
        
    elif command == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("🔍 輸入搜尋關鍵字: ")
        results = search_papers(query)
        print(f"\n找到 {len(results)} 篇相關論文：\n")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r.get('title', '未知')}")
            print(f"   作者: {r.get('authors', '未知')}")
            print(f"   日期: {r.get('date', '未知')}")
            print()
            
    elif command == "ask":
        question = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("❓ 輸入你的問題: ")
        print("\n🤔 思考中...")
        answer, sources = answer_question(question)
        print(f"\n📝 回答：\n{answer}\n")
        
        if sources:
            print("📚 參考論文：")
            for s in sources:
                print(f"  - {s.get('title', '未知')}")
    else:
        print(f"⚠️ 未知指令: {command}")
