#!/usr/bin/env python3
"""
論文說書人中心 - 核心模組（Chunk Embedding 版）
功能：論文分段索引建立、語意搜尋、Q&A 對話
"""

import os
import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import urllib.request

# ==================== 配置 ====================
STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
LANCEDB_PATH = STORYTELLERS_DIR / "papers.lance"
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "qwen3-embedding:8b"
CHUNK_SIZE = 800     # 每段字數
CHUNK_OVERLAP = 100  # 重疊字數（確保語意不被截斷）

# ==================== LanceDB ====================
def get_lance_db():
    try:
        import lancedb
        return lancedb.connect(str(LANCEDB_PATH))
    except ImportError:
        print("⚠️ 請先安裝 lancedb: pip install lancedb")
        return None


def create_table(db):
    """建立 Chunk 版論文表"""
    import pyarrow as pa
    
    schema = pa.schema([
        pa.field("id", pa.string()),          # paper_id + "_chunk_" + chunk_idx
        pa.field("paper_id", pa.string()),    # 原始論文 ID
        pa.field("filename", pa.string()),
        pa.field("title", pa.string()),
        pa.field("authors", pa.string()),
        pa.field("date", pa.string()),
        pa.field("chunk_index", pa.int32()),  # 第幾個 chunk
        pa.field("chunk_text", pa.string()),  # 本段文字
        pa.field("content", pa.string()),     # 論文全文（供 Q&A 使用）
        pa.field("embedding", pa.list_(pa.float32(), list_size=4096)),
    ])
    
    try:
        return db.create_table("papers", schema=schema)
    except Exception as e:
        print(f"建立表時出錯: {e}")
        return None


# ==================== Embedding ====================
def get_embedding(text: str) -> List[float]:
    """使用本地 Ollama 取得 embedding"""
    text = text[:3000]
    url = f"{OLLAMA_BASE_URL}/api/embeddings"
    data = {"model": EMBEDDING_MODEL, "prompt": text}
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read()).get('embedding', [])
    except Exception as e:
        print(f"⚠️ Embedding 錯誤: {e}")
        return []


# ==================== 文字處理 ====================
def extract_text_from_html(html_content: str) -> str:
    """從 HTML 提取純文字"""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """將文字分割成重疊的 chunks"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap  # 重疊部分
    return chunks


def parse_paper_metadata(filename: str) -> Optional[Dict]:
    """從 HTML 解析論文基本資訊"""
    filepath = STORYTELLERS_DIR / filename
    if not filepath.exists():
        return None
    
    html_content = filepath.read_text(encoding='utf-8')
    plain_text = extract_text_from_html(html_content)
    
    title_match = re.search(r'<title>([^<]+)</title>', html_content)
    title = title_match.group(1) if title_match else filename.replace('.html', '')
    
    author_match = re.search(r'作者[：:]\s*([^<\n]+)', plain_text)
    authors = author_match.group(1).strip() if author_match else "未知"
    
    date_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', plain_text)
    date = date_match.group(1) if date_match else "未知"
    
    return {
        "paper_id": filename.replace('.html', ''),
        "filename": filename,
        "title": title,
        "authors": authors,
        "date": date,
        "content": plain_text
    }


# ==================== 索引建立 ====================
def rebuild_index():
    """重建 Chunk Embedding 索引"""
    db = get_lance_db()
    if db is None:
        print("❌ 無法連接資料庫")
        return False
    
    # 刪除舊表重建
    try:
        if "papers" in db.list_tables():
            db.drop_table("papers")
        tbl = create_table(db)
        if tbl is None:
            return False
    except Exception as e:
        print(f"建立表時出錯: {e}")
        return False
    
    html_files = list(STORYTELLERS_DIR.glob("*.html"))
    print(f"📂 找到 {len(html_files)} 篇論文")
    
    all_rows = []
    for html_file in html_files:
        paper = parse_paper_metadata(html_file.name)
        if not paper:
            continue
        
        print(f"  📄 {paper['title'][:40]}...")
        
        # 標題加入每個 chunk 作為前綴（提升語意定位）
        title_prefix = f"論文：{paper['title']}\n\n"
        chunks = split_into_chunks(paper['content'])
        print(f"     分割成 {len(chunks)} 個 chunks")
        
        for i, chunk_text in enumerate(chunks):
            embedding_text = title_prefix + chunk_text
            embedding = get_embedding(embedding_text)
            if not embedding:
                print(f"     ⚠️ chunk {i} embedding 失敗，跳過")
                continue
            
            all_rows.append({
                "id": f"{paper['paper_id']}_chunk_{i}",
                "paper_id": paper['paper_id'],
                "filename": paper['filename'],
                "title": paper['title'],
                "authors": paper['authors'],
                "date": paper['date'],
                "chunk_index": i,
                "chunk_text": chunk_text,
                "content": paper['content'],
                "embedding": embedding
            })
    
    if all_rows:
        tbl.add(all_rows)
        papers_count = len(set(r['paper_id'] for r in all_rows))
        print(f"✅ 已建立索引: {papers_count} 篇論文，共 {len(all_rows)} 個 chunks")
        return True
    
    return False


# ==================== 搜尋 ====================
def search_papers(query: str, top_k: int = 5, similarity_threshold: float = 0.0) -> List[Dict]:
    """語意搜尋：搜尋最相關 chunks，依論文去重後返回"""
    query_embedding = get_embedding(query)
    if not query_embedding:
        return []
    
    db = get_lance_db()
    if db is None:
        return []
    
    try:
        tbl = db.open_table("papers")
        # 多抓幾個 chunk，去重後取 top_k 篇論文
        results = tbl.search(query_embedding, vector_column_name="embedding") \
                     .limit(top_k * 5).to_pandas().to_dict('records')
        
        # 過濾相似度門檻（similarity = 1 - distance >= threshold）
        results = [r for r in results if (1.0 - r.get('_distance', 9999)) >= similarity_threshold]
        
        # 依論文去重：每篇論文只保留相似度最高的 chunk
        best_per_paper = {}
        for r in results:
            pid = r['paper_id']
            sim = 1.0 - r.get('_distance', 9999)
            if pid not in best_per_paper or sim > (1.0 - best_per_paper[pid].get('_distance', 9999)):
                best_per_paper[pid] = r
        
        # 依相似度排序，取 top_k
        sorted_papers = sorted(best_per_paper.values(),
                               key=lambda x: 1.0 - x.get('_distance', 9999),
                               reverse=True)
        return sorted_papers[:top_k]
    
    except Exception as e:
        print(f"搜尋時出錯: {e}")
        return []


# ==================== Q&A ====================
def answer_question(question: str, context_limit: int = 3) -> Tuple[str, List[Dict]]:
    """使用 RAG 回答問題"""
    results = search_papers(question, top_k=context_limit)
    if not results:
        return "抱歉，沒有找到相關的論文內容。", []
    
    context_parts = [f"=== {r.get('title','未知')} ===\n{r.get('content','')[:2000]}" for r in results]
    context = "\n\n".join(context_parts)
    
    prompt = f"""你是一個專業的論文說書人，擅長比較和解釋學術論文的內容。

根據以下論文內容，回答用戶的問題。請用繁體中文回答，並引用相關的論文標題：

=== 論文內容 ===
{context}

=== 用戶問題 ===
{question}

回答："""
    
    try:
        url = f"{OLLAMA_BASE_URL}/api/generate"
        data = {"model": "qwen3:8b", "prompt": prompt, "stream": False}
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=180) as response:
            answer = json.loads(response.read()).get('response', '').strip()
            return answer, results
    except Exception as e:
        return f"生成回答時發生錯誤：{e}", results


# ==================== 主程式 ====================
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("""
📚 論文說書人中心 - 使用說明
  python3 paper_center.py init     - 初始化並建立 chunk 索引
  python3 paper_center.py rebuild  - 重建全部索引
  python3 paper_center.py search   - 搜尋論文
  python3 paper_center.py ask      - 問問題
        """)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command in ("init", "rebuild"):
        print("🔄 重建 Chunk 索引...")
        rebuild_index()
    
    elif command == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("🔍 輸入搜尋關鍵字: ")
        results = search_papers(query)
        print(f"\n找到 {len(results)} 篇相關論文：\n")
        for i, r in enumerate(results, 1):
            sim = 1.0 - r.get('_distance', 9999)
            print(f"{i}. {r.get('title','未知')} | 相似度={sim:.2f}")
    
    elif command == "ask":
        question = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else input("❓ 輸入問題: ")
        print("\n🤔 思考中...")
        answer, sources = answer_question(question)
        print(f"\n📝 回答：\n{answer}\n")
        if sources:
            print("📚 參考論文：")
            for s in sources:
                print(f"  - {s.get('title','未知')}")
    
    else:
        print(f"⚠️ 未知指令: {command}")
