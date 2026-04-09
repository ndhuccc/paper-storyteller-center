#!/usr/bin/env python3
"""Shared retrieval/indexing services for Paper Story Rewriting Center."""

import html
import json
import re
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


PROJECT_DIR = Path(__file__).resolve().parent
STORYTELLERS_DIR = PROJECT_DIR / "htmls"
# 向量索引庫（LanceDB）位於專案根目錄 ./papers.lance/
LANCEDB_PATH = PROJECT_DIR / "papers.lance"
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "qwen3-embedding:8b"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
INDEX_METADATA_FILE = PROJECT_DIR / "papers.lance" / ".papers_index_meta.json"
INDEX_METADATA_VERSION = 1
INDEX_SCHEMA_VERSION = 1


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


def _list_table_names(db: Any) -> Set[str]:
    try:
        listed = db.list_tables()
        values = listed.tables if hasattr(listed, "tables") else listed
    except Exception:
        return set()
    names: Set[str] = set()
    for value in values or []:
        name = str(value or "").strip()
        if name:
            names.add(name)
    return names


def _index_config() -> Dict[str, Any]:
    return {
        "metadata_version": INDEX_METADATA_VERSION,
        "schema_version": INDEX_SCHEMA_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
    }


def _paper_signature(html_path: Path) -> str:
    stat = html_path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _load_index_metadata() -> Dict[str, Any]:
    if not INDEX_METADATA_FILE.exists():
        return {"index_config": _index_config(), "papers": {}, "display_titles": {}}
    try:
        payload = json.loads(INDEX_METADATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"index_config": _index_config(), "papers": {}, "display_titles": {}}

    papers = payload.get("papers")
    if not isinstance(papers, dict):
        papers = {}

    config = payload.get("index_config")
    if not isinstance(config, dict):
        config = _index_config()

    raw_display_titles = payload.get("display_titles")
    display_titles: Dict[str, str] = {}
    if isinstance(raw_display_titles, dict):
        for raw_pid, raw_title in raw_display_titles.items():
            pid = _normalize_paper_id(raw_pid)
            title = str(raw_title or "").strip()
            if pid and title:
                display_titles[pid] = title

    return {"index_config": config, "papers": papers, "display_titles": display_titles}


def _save_index_metadata(metadata: Dict[str, Any]) -> None:
    STORYTELLERS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(metadata, ensure_ascii=False, indent=2)
    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(INDEX_METADATA_FILE.parent),
            prefix=".papers_index_meta.",
            suffix=".tmp",
        ) as handle:
            handle.write(serialized)
            tmp_file = Path(handle.name)
        if tmp_file is not None:
            tmp_file.replace(INDEX_METADATA_FILE)
    finally:
        if tmp_file is not None and tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass


def _normalize_paper_id(raw_paper_id: Any) -> str:
    paper_id = str(raw_paper_id or "").strip()
    if not paper_id:
        return ""
    if paper_id.lower().endswith(".html"):
        return Path(paper_id).stem.strip()
    return paper_id


def _lancedb_string_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _resolve_storytellers_html_path(paper_id: str) -> Path:
    # Search for HTML file matching the paper_id (case-insensitive exact match)
    normalized = _normalize_paper_id(paper_id)
    storytellers_root = STORYTELLERS_DIR.expanduser().resolve(strict=False)
    
    # Try exact match first (case-insensitive)
    for html_file in storytellers_root.glob("*.html"):
        if html_file.stem.lower() == normalized.lower():
            return html_file
    
    # Try with -storyteller suffix
    for html_file in storytellers_root.glob("*.html"):
        stem = html_file.stem
        if stem.lower() == normalized.lower():
            return html_file
        # Also check if it ends with -storyteller
        if stem.lower().replace("-storyteller", "") == normalized.lower():
            return html_file
    
    raise FileNotFoundError(f"No HTML file found for paper_id: {paper_id}")


def delete_paper(paper_id: str) -> Dict[str, Any]:
    """Delete one paper from LanceDB index and STORYTELLERS_DIR HTML artifact."""
    normalized_paper_id = _normalize_paper_id(paper_id)
    if not normalized_paper_id:
        return {
            "ok": False,
            "paper_id": "",
            "index_deleted": False,
            "index_error": "empty paper_id",
            "html_deleted": False,
            "html_path": "",
            "html_error": "empty paper_id",
            "cache_cleared": True,
            "message": "paper_id 不可為空",
        }

    index_deleted = False
    index_error = ""
    db = get_lance_db()
    if db is None:
        index_error = "無法連接 LanceDB"
    else:
        try:
            tables = set(db.list_tables().tables)
            if "papers" in tables:
                table = db.open_table("papers")
                table.delete(f"paper_id = {_lancedb_string_literal(normalized_paper_id)}")
                index_deleted = True
            else:
                index_error = "找不到 papers table"
        except Exception as e:
            index_error = str(e)

    html_deleted = False
    html_path = ""
    html_error = ""
    try:
        target_html = _resolve_storytellers_html_path(normalized_paper_id)
        html_path = str(target_html)
        if target_html.exists():
            if target_html.is_file():
                target_html.unlink()
                html_deleted = True
            else:
                html_error = "HTML 目標不是檔案"
        else:
            html_error = "找不到 HTML 檔案"
    except Exception as e:
        html_error = str(e)

    clear_lance_db_cache()

    ok = index_deleted or html_deleted
    if index_deleted and html_deleted:
        message = "已刪除索引與 HTML"
    elif index_deleted:
        message = "已刪除索引，HTML 未刪除"
    elif html_deleted:
        message = "已刪除 HTML，索引未刪除"
    else:
        message = "未刪除任何資料"

    return {
        "ok": ok,
        "paper_id": normalized_paper_id,
        "index_deleted": index_deleted,
        "index_error": index_error,
        "html_deleted": html_deleted,
        "html_path": html_path,
        "html_error": html_error,
        "cache_cleared": True,
        "message": message,
    }


def rename_paper(paper_id: str, new_name: str) -> Dict[str, Any]:
    """Rename an HTML paper file and remove stale LanceDB index rows.

    The caller should rebuild the index afterwards so the renamed paper
    becomes searchable again.
    """
    normalized_old = _normalize_paper_id(paper_id)
    if not normalized_old:
        return {
            "ok": False,
            "paper_id": "",
            "new_name": new_name,
            "message": "paper_id 不可為空",
        }

    # Sanitize new_name: allow word chars, hyphens, dots; strip .html suffix
    raw_new = str(new_name or "").strip()
    if raw_new.lower().endswith(".html"):
        raw_new = raw_new[:-5].strip()
    normalized_new = re.sub(r"[^\w\-.]", "_", raw_new).strip("_.")
    if not normalized_new:
        return {
            "ok": False,
            "paper_id": normalized_old,
            "new_name": new_name,
            "message": "新名稱不合法（僅允許字母、數字、連字號、底線、點）",
        }
    if normalized_new == normalized_old:
        return {
            "ok": False,
            "paper_id": normalized_old,
            "new_name": new_name,
            "message": "新名稱與舊名稱相同",
        }

    # Locate source HTML file
    storytellers_root = STORYTELLERS_DIR.expanduser().resolve(strict=False)
    old_path: Optional[Path] = None
    for html_file in storytellers_root.glob("*.html"):
        if html_file.stem.lower() == normalized_old.lower():
            old_path = html_file
            break
    if old_path is None:
        return {
            "ok": False,
            "paper_id": normalized_old,
            "new_name": new_name,
            "message": f"找不到 HTML 檔案：{normalized_old}.html",
        }

    new_path = storytellers_root / f"{normalized_new}.html"
    if new_path.exists():
        return {
            "ok": False,
            "paper_id": normalized_old,
            "new_name": normalized_new,
            "message": f"目標檔名已存在：{normalized_new}.html",
        }

    # Remove stale LanceDB records for old paper_id
    index_deleted = False
    index_error = ""
    db = get_lance_db()
    if db is not None:
        try:
            tables = set(db.list_tables().tables)
            if "papers" in tables:
                table = db.open_table("papers")
                table.delete(f"paper_id = {_lancedb_string_literal(normalized_old)}")
                index_deleted = True
        except Exception as e:
            index_error = str(e)

    # Remove from index metadata
    metadata = _load_index_metadata()
    metadata_papers = metadata.get("papers") if isinstance(metadata.get("papers"), dict) else {}
    display_titles = metadata.get("display_titles") if isinstance(metadata.get("display_titles"), dict) else {}
    metadata_papers.pop(normalized_old, None)
    metadata_papers.pop(old_path.stem, None)
    # Keep custom display title override with the new paper_id.
    if normalized_old in display_titles:
        display_titles[normalized_new] = str(display_titles.pop(normalized_old))
    if old_path.stem in display_titles:
        display_titles[normalized_new] = str(display_titles.pop(old_path.stem))
    metadata["papers"] = metadata_papers
    metadata["display_titles"] = display_titles
    try:
        INDEX_METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        INDEX_METADATA_FILE.write_text(
            __import__("json").dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # Rename the file
    try:
        old_path.rename(new_path)
    except Exception as e:
        return {
            "ok": False,
            "paper_id": normalized_old,
            "new_name": normalized_new,
            "message": f"重新命名失敗：{e}",
        }

    clear_lance_db_cache()

    return {
        "ok": True,
        "paper_id": normalized_old,
        "new_paper_id": normalized_new,
        "new_name": normalized_new,
        "old_filename": old_path.name,
        "new_filename": new_path.name,
        "index_deleted": index_deleted,
        "index_error": index_error,
        "cache_cleared": True,
        "message": f"已重新命名為 {normalized_new}.html（如有索引已移除，請重建索引）",
    }


def create_table(db, overwrite: bool = True):
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

    mode = "overwrite" if overwrite else "create"
    try:
        return db.create_table("papers", schema=schema, mode=mode)
    except TypeError:
        try:
            return db.create_table("papers", schema=schema)
        except Exception as e:
            print(f"建立表時出錯: {e}")
            return None
    except Exception as e:
        print(f"建立表時出錯: {e}")
        return None


def _open_or_create_papers_table(db: Any):
    tables = _list_table_names(db)
    if "papers" in tables:
        try:
            return db.open_table("papers")
        except Exception as e:
            print(f"開啟 papers table 出錯: {e}")
            return None
    return create_table(db, overwrite=False)


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


def _build_rows_for_html_file(html_file: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    paper = parse_paper_metadata(html_file.name)
    if not paper:
        return [], {"paper_id": html_file.stem, "error": "metadata_parse_failed"}

    title_prefix = f"論文：{paper['title']}\n\n"
    chunks = split_into_chunks(paper["content"])
    rows: List[Dict[str, Any]] = []
    embedding_failures = 0

    for i, chunk_text in enumerate(chunks):
        embedding_text = title_prefix + chunk_text
        embedding = get_embedding(embedding_text)
        if not embedding:
            embedding_failures += 1
            continue

        rows.append(
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

    summary = {
        "paper_id": paper["paper_id"],
        "filename": paper["filename"],
        "title": paper["title"],
        "chunks_total": len(chunks),
        "chunks_indexed": len(rows),
        "embedding_failures": embedding_failures,
    }
    if not rows:
        summary["error"] = "all_embeddings_failed"
    return rows, summary


def get_display_title_overrides() -> Dict[str, str]:
    """Return user-defined display title overrides keyed by paper_id."""
    metadata = _load_index_metadata()
    raw = metadata.get("display_titles")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for raw_pid, raw_title in raw.items():
        pid = _normalize_paper_id(raw_pid)
        title = str(raw_title or "").strip()
        if pid and title:
            out[pid] = title
    return out


def _sync_html_display_title(paper_id: str, display_title: str) -> Dict[str, Any]:
    """Sync display title to HTML <title> and first <h1> for the paper."""
    try:
        html_path = _resolve_storytellers_html_path(paper_id)
    except Exception as e:
        return {
            "ok": False,
            "html_synced": False,
            "html_path": "",
            "html_error": str(e),
            "title_updated": False,
            "h1_updated": False,
        }

    try:
        html_content = html_path.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "ok": False,
            "html_synced": False,
            "html_path": str(html_path),
            "html_error": f"讀取 HTML 失敗: {e}",
            "title_updated": False,
            "h1_updated": False,
        }

    safe_title = html.escape(display_title)
    updated = html_content

    # Keep browser tab title aligned with display name.
    new_title_tag = f"<title>{safe_title} - 說書人版</title>"
    updated, title_count = re.subn(
        r"<title>.*?</title>",
        new_title_tag,
        updated,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Keep visible page main heading aligned with display name.
    updated, h1_count = re.subn(
        r"(<h1\b[^>]*>)(.*?)(</h1>)",
        rf"\1📚 {safe_title}\3",
        updated,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if updated != html_content:
        try:
            html_path.write_text(updated, encoding="utf-8")
        except Exception as e:
            return {
                "ok": False,
                "html_synced": False,
                "html_path": str(html_path),
                "html_error": f"寫入 HTML 失敗: {e}",
                "title_updated": bool(title_count),
                "h1_updated": bool(h1_count),
            }

    return {
        "ok": True,
        "html_synced": updated != html_content,
        "html_path": str(html_path),
        "html_error": "",
        "title_updated": bool(title_count),
        "h1_updated": bool(h1_count),
    }


def update_paper_display_title(paper_id: str, display_title: str) -> Dict[str, Any]:
    """Update only display title metadata, without renaming filename or paper_id."""
    normalized_paper_id = _normalize_paper_id(paper_id)
    normalized_title = str(display_title or "").strip()
    if not normalized_paper_id:
        return {
            "ok": False,
            "paper_id": "",
            "display_title": normalized_title,
            "message": "paper_id 不可為空",
        }
    if not normalized_title:
        return {
            "ok": False,
            "paper_id": normalized_paper_id,
            "display_title": normalized_title,
            "message": "display_title 不可為空",
        }

    metadata = _load_index_metadata()
    display_titles = metadata.get("display_titles") if isinstance(metadata.get("display_titles"), dict) else {}
    display_titles[normalized_paper_id] = normalized_title
    metadata["display_titles"] = display_titles
    _save_index_metadata(metadata)

    html_sync = _sync_html_display_title(normalized_paper_id, normalized_title)
    html_sync_ok = bool(html_sync.get("ok"))
    html_sync_message = "已同步更新 HTML 標題"
    if not html_sync_ok:
        html_sync_message = str(html_sync.get("html_error", "HTML 標題同步失敗"))

    return {
        "ok": True,
        "paper_id": normalized_paper_id,
        "display_title": normalized_title,
        "html_sync_ok": html_sync_ok,
        "html_synced": bool(html_sync.get("html_synced")),
        "html_path": str(html_sync.get("html_path", "")),
        "title_updated": bool(html_sync.get("title_updated")),
        "h1_updated": bool(html_sync.get("h1_updated")),
        "html_sync_message": html_sync_message,
        "message": (
            "已更新顯示名稱並同步 HTML 大標題（不變更檔名與 paper_id）"
            if html_sync_ok
            else "已更新顯示名稱，但同步 HTML 大標題失敗"
        ),
    }


def incremental_index() -> Dict[str, Any]:
    """Incrementally sync HTML papers to LanceDB index with resumable progress."""
    db = get_lance_db()
    if db is None:
        return {"ok": False, "message": "無法連接 LanceDB", "mode": "incremental"}

    STORYTELLERS_DIR.mkdir(parents=True, exist_ok=True)
    html_files = sorted(STORYTELLERS_DIR.glob("*.html"), key=lambda path: path.name.lower())
    html_map = {html_file.stem: html_file for html_file in html_files}

    metadata = _load_index_metadata()
    metadata_papers = metadata.get("papers") if isinstance(metadata.get("papers"), dict) else {}
    current_config = _index_config()
    config_mismatch = metadata.get("index_config") != current_config

    if config_mismatch:
        try:
            if "papers" in _list_table_names(db):
                db.drop_table("papers")
        except Exception as e:
            return {
                "ok": False,
                "message": f"index config changed but failed to reset table: {e}",
                "mode": "incremental",
            }
        metadata = {
            "index_config": current_config,
            "papers": {},
            "display_titles": metadata.get("display_titles", {}),
        }
        metadata_papers = {}

    tbl = _open_or_create_papers_table(db)
    if tbl is None:
        return {"ok": False, "message": "無法建立或開啟 papers table", "mode": "incremental"}

    removed_ids = sorted(pid for pid in metadata_papers.keys() if pid not in html_map)
    for paper_id in removed_ids:
        try:
            tbl.delete(f"paper_id = {_lancedb_string_literal(paper_id)}")
        except Exception as e:
            return {
                "ok": False,
                "message": f"刪除舊索引失敗 ({paper_id}): {e}",
                "mode": "incremental",
            }
        metadata_papers.pop(paper_id, None)

    if config_mismatch or removed_ids:
        metadata["papers"] = metadata_papers
        metadata["index_config"] = current_config
        _save_index_metadata(metadata)

    changed_ids: List[str] = []
    for paper_id, html_file in html_map.items():
        current_signature = _paper_signature(html_file)
        previous = metadata_papers.get(paper_id, {})
        previous_signature = str(previous.get("signature", "")).strip() if isinstance(previous, dict) else ""
        if config_mismatch or current_signature != previous_signature:
            changed_ids.append(paper_id)

    processed = 0
    indexed_papers = 0
    indexed_chunks = 0
    failures: List[Dict[str, str]] = []

    for paper_id in changed_ids:
        html_file = html_map[paper_id]
        processed += 1
        rows, summary = _build_rows_for_html_file(html_file)
        if not rows:
            failures.append(
                {
                    "paper_id": paper_id,
                    "reason": str(summary.get("error", "index_rows_empty")),
                }
            )
            continue

        try:
            tbl.delete(f"paper_id = {_lancedb_string_literal(paper_id)}")
            tbl.add(rows)
        except Exception as e:
            failures.append({"paper_id": paper_id, "reason": f"table_write_failed: {e}"})
            continue

        indexed_papers += 1
        indexed_chunks += len(rows)
        metadata_papers[paper_id] = {
            "signature": _paper_signature(html_file),
            "filename": html_file.name,
            "chunks": len(rows),
        }
        metadata["papers"] = metadata_papers
        metadata["index_config"] = current_config
        _save_index_metadata(metadata)

    ok = len(failures) == 0
    message = (
        f"incremental sync done: changed={len(changed_ids)}, indexed={indexed_papers}, "
        f"removed={len(removed_ids)}, failures={len(failures)}"
    )
    return {
        "ok": ok,
        "mode": "incremental",
        "message": message,
        "processed": processed,
        "changed": len(changed_ids),
        "removed": len(removed_ids),
        "indexed_papers": indexed_papers,
        "indexed_chunks": indexed_chunks,
        "failures": failures,
        "config_mismatch": config_mismatch,
    }


def rebuild_index() -> bool:
    """Rebuild chunk embedding index."""
    db = get_lance_db()
    if db is None:
        print("❌ 無法連接資料庫")
        return False

    try:
        tables = _list_table_names(db)
        if "papers" in tables:
            db.drop_table("papers")
        tbl = create_table(db, overwrite=True)
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
        existing = _load_index_metadata()
        metadata = {
            "index_config": _index_config(),
            "papers": {
                html_file.stem: {
                    "signature": _paper_signature(html_file),
                    "filename": html_file.name,
                }
                for html_file in html_files
            },
            "display_titles": existing.get("display_titles", {}),
        }
        _save_index_metadata(metadata)
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
