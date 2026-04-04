#!/usr/bin/env python3
"""Repository helpers for Paper Storyteller Center.

設計原則：
1. repository 層不依賴 Streamlit runtime。
2. 可在 GUI / CLI / background service 中重複使用。
3. 僅負責 paper metadata 與 paper list 讀取，不承擔 UI 邏輯。
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Union


STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
LANCEDB_PATH = STORYTELLERS_DIR / "papers.lance"


@lru_cache(maxsize=1)
def _get_lance_db():
    """Create and cache LanceDB connection for repository queries."""
    try:
        import lancedb

        return lancedb.connect(str(LANCEDB_PATH))
    except Exception:
        return None


def _extract_text_from_html(html_content: str) -> str:
    """Extract plain text from HTML content."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_paper_metadata(html_path: Union[str, Path]) -> Optional[Dict]:
    """Parse paper metadata from an HTML file path."""
    filepath = Path(html_path)
    if not filepath.exists() or filepath.suffix.lower() != ".html":
        return None

    html_content = filepath.read_text(encoding="utf-8")
    plain_text = _extract_text_from_html(html_content)

    title_match = re.search(r"<title>([^<]+)</title>", html_content)
    title = title_match.group(1) if title_match else filepath.stem

    author_match = re.search(r"作者[：:]\s*([^<\n]+)", plain_text)
    authors = author_match.group(1).strip() if author_match else "未知"

    date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", plain_text)
    date = date_match.group(1) if date_match else "未知"

    return {
        "paper_id": filepath.stem,
        "filename": filepath.name,
        "filepath": str(filepath),
        "title": title,
        "authors": authors,
        "date": date,
        "content": plain_text,
    }


def get_all_papers() -> List[Dict]:
    """Get all papers from LanceDB, deduplicated by paper_id."""
    db = _get_lance_db()
    if db is None:
        return []

    try:
        tbl = db.open_table("papers")
        all_rows = tbl.to_pandas().to_dict("records")

        # dedupe by paper_id and keep first row
        seen = {}
        for row in all_rows:
            pid = row.get("paper_id", row.get("id", ""))
            if pid not in seen:
                seen[pid] = row
        return list(seen.values())
    except Exception:
        return []
