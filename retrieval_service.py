#!/usr/bin/env python3
"""Shared retrieval/indexing services for Paper Storyteller Center."""

import json
import re
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional


STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
LANCEDB_PATH = STORYTELLERS_DIR / "papers.lance"
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "qwen3-embedding:8b"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


@lru_cache(maxsize=1)
def get_lance_db():
    """Create and cache LanceDB connection."""
    try:
        import lancedb

        return lancedb.connect(str(LANCEDB_PATH))
    except ImportError:
        print("⚠️ 請先安裝 lancedb: pip install lancedb")
        return None
    except Exception as e:
        print(f"⚠️ LanceDB 連線錯誤: {e}")
        return None


def clear_lance_db_cache() -> None:
    """Clear cached LanceDB connection."""
    get_lance_db.cache_clear()


def create_table(db):
    """Create papers chunk table schema."""
    import pyarrow as pa

    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("paper_id", pa.string()),
        pa.field("filename", pa.string()),
        pa.field("title", pa.string()),
        pa.field("authors", pa.string()),
        pa.field("date", pa.string()),
        pa.field("chunk_index", pa.int32()),
        pa.field("chunk_text", pa.string()),
        pa.field("content", pa.string()),
        pa.field("embedding", pa.list_(pa.float32(), list_size=4096)),
    ])

    try:
        return db.create_table("papers", schema=schema)
    except Exception as e:
        print(f"建立表時出錯: {e}")
        return None


def get_embedding(text: str) -> List[float]:
    """Get embeddings from local Ollama."""
    url = f"{OLLAMA_BASE_URL}/api/embeddings"
    data = {"model": EMBEDDING_MODEL, "prompt": text[:3000]}
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read()).get("embedding", [])
    except Exception as e:
        print(f"⚠️ Embedding 錯誤: {e}")
        return []


def extract_text_from_html(html_content: str) -> str:
    """Extract plain text from HTML."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def parse_paper_metadata(filename: str) -> Optional[Dict]:
    """Parse metadata from a paper HTML file under STORYTELLERS_DIR."""
    filepath = STORYTELLERS_DIR / filename
    if not filepath.exists():
        return None

    html_content = filepath.read_text(encoding="utf-8")
    plain_text = extract_text_from_html(html_content)

    title_match = re.search(r"<title>([^<]+)</title>", html_content)
    title = title_match.group(1) if title_match else filename.replace(".html", "")

    author_match = re.search(r"作者[：:]\s*([^<\n]+)", plain_text)
    authors = author_match.group(1).strip() if author_match else "未知"

    date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", plain_text)
    date = date_match.group(1) if date_match else "未知"

    return {
        "paper_id": filename.replace(".html", ""),
        "filename": filename,
        "title": title,
        "authors": authors,
        "date": date,
        "content": plain_text,
    }


def rebuild_index() -> bool:
    """Rebuild chunk embedding index."""
    db = get_lance_db()
    if db is None:
        print("❌ 無法連接資料庫")
        return False

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

        title_prefix = f"論文：{paper['title']}\n\n"
        chunks = split_into_chunks(paper["content"])
        print(f"     分割成 {len(chunks)} 個 chunks")

        for i, chunk_text in enumerate(chunks):
            embedding_text = title_prefix + chunk_text
            embedding = get_embedding(embedding_text)
            if not embedding:
                print(f"     ⚠️ chunk {i} embedding 失敗，跳過")
                continue

            all_rows.append(
                {
                    "id": f"{paper['paper_id']}_chunk_{i}",
                    "paper_id": paper["paper_id"],
                    "filename": paper["filename"],
                    "title": paper["title"],
                    "authors": paper["authors"],
                    "date": paper["date"],
                    "chunk_index": i,
                    "chunk_text": chunk_text,
                    "content": paper["content"],
                    "embedding": embedding,
                }
            )

    if all_rows:
        tbl.add(all_rows)
        papers_count = len(set(r["paper_id"] for r in all_rows))
        print(f"✅ 已建立索引: {papers_count} 篇論文，共 {len(all_rows)} 個 chunks")
        return True

    return False


def search_papers(query: str, top_k: int = 5, similarity_threshold: float = 0.0) -> List[Dict]:
    """Search relevant chunks and dedupe by paper_id."""
    query_embedding = get_embedding(query)
    if not query_embedding:
        return []

    db = get_lance_db()
    if db is None:
        return []

    try:
        tbl = db.open_table("papers")
        results = tbl.search(query_embedding, vector_column_name="embedding").limit(top_k * 5).to_pandas().to_dict("records")

        results = [r for r in results if (1.0 - r.get("_distance", 9999)) >= similarity_threshold]

        best_per_paper = {}
        for r in results:
            pid = r.get("paper_id", r.get("id", ""))
            sim = 1.0 - r.get("_distance", 9999)
            if pid not in best_per_paper or sim > (1.0 - best_per_paper[pid].get("_distance", 9999)):
                best_per_paper[pid] = r

        sorted_papers = sorted(
            best_per_paper.values(),
            key=lambda x: 1.0 - x.get("_distance", 9999),
            reverse=True,
        )
        return sorted_papers[:top_k]

    except Exception as e:
        print(f"搜尋時出錯: {e}")
        return []
