#!/usr/bin/env python3
"""Repository helpers for Paper Story Rewriting Center.

設計原則：
1. repository 層不依賴 Streamlit runtime。
2. 可在 GUI / CLI / background service 中重複使用。
3. 僅負責 paper metadata 與 paper list 讀取，不承擔 UI 邏輯。
"""

import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from retrieval_service import LANCEDB_PATH
from retrieval_service import delete_paper as retrieval_delete_paper
from retrieval_service import get_display_title_overrides as retrieval_get_display_title_overrides
from retrieval_service import rename_paper as retrieval_rename_paper
from retrieval_service import update_paper_display_title as retrieval_update_paper_display_title
from storyteller_pipeline import PROJECT_DIR as _PIPELINE_PROJECT_DIR
from storyteller_pipeline import STYLE_DISPLAY_NAMES


STORYTELLERS_DIR = _PIPELINE_PROJECT_DIR / "htmls"

# 說書人 HTML 內 `<strong>童話故事版本</strong>` 等標籤 → 風格 key
_STYLE_LABEL_TO_KEY: Dict[str, str] = {
    str(label).strip(): key for key, label in STYLE_DISPLAY_NAMES.items()
}

MANIFEST_SOURCE_INDEX_AND_HTML = "index_and_html"
MANIFEST_SOURCE_HTML_ONLY = "html_only"
MANIFEST_SOURCE_INDEX_ONLY = "index_only"
MANIFEST_SOURCE_UNKNOWN = "unknown"

PAPER_STATUS_READY = "ready"
PAPER_STATUS_GENERATED_NOT_INDEXED = "generated_not_indexed"
PAPER_STATUS_INDEX_ONLY = "index_only"
PAPER_STATUS_UNAVAILABLE = "unavailable"

_MANIFEST_SOURCES = {
    MANIFEST_SOURCE_INDEX_AND_HTML,
    MANIFEST_SOURCE_HTML_ONLY,
    MANIFEST_SOURCE_INDEX_ONLY,
    MANIFEST_SOURCE_UNKNOWN,
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def _manifest_source_from_flags(has_html: bool, is_indexed: bool) -> str:
    if has_html and is_indexed:
        return MANIFEST_SOURCE_INDEX_AND_HTML
    if has_html:
        return MANIFEST_SOURCE_HTML_ONLY
    if is_indexed:
        return MANIFEST_SOURCE_INDEX_ONLY
    return MANIFEST_SOURCE_UNKNOWN


def resolve_paper_status(has_html: Any, is_indexed: Any, manifest_source: Any = "") -> str:
    """Resolve canonical paper status for center-facing usage."""
    source = str(manifest_source or "").strip().lower()
    if source == MANIFEST_SOURCE_INDEX_AND_HTML:
        return PAPER_STATUS_READY
    if source == MANIFEST_SOURCE_HTML_ONLY:
        return PAPER_STATUS_GENERATED_NOT_INDEXED
    if source == MANIFEST_SOURCE_INDEX_ONLY:
        return PAPER_STATUS_INDEX_ONLY

    html_flag = _as_bool(has_html)
    indexed_flag = _as_bool(is_indexed)
    if html_flag and indexed_flag:
        return PAPER_STATUS_READY
    if html_flag and not indexed_flag:
        return PAPER_STATUS_GENERATED_NOT_INDEXED
    if indexed_flag and not html_flag:
        return PAPER_STATUS_INDEX_ONLY
    return PAPER_STATUS_UNAVAILABLE


def normalize_manifest_paper(paper: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one manifest row so status/source/flags are explicit and stable."""
    item = dict(paper)
    has_html = _as_bool(item.get("has_html"))
    is_indexed = _as_bool(item.get("is_indexed"))

    manifest_source = str(item.get("manifest_source", "")).strip().lower()
    if manifest_source not in _MANIFEST_SOURCES:
        manifest_source = _manifest_source_from_flags(has_html=has_html, is_indexed=is_indexed)

    item["has_html"] = has_html
    item["is_indexed"] = is_indexed
    item["manifest_source"] = manifest_source
    item["paper_status"] = resolve_paper_status(
        has_html=has_html,
        is_indexed=is_indexed,
        manifest_source=manifest_source,
    )
    return item


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_filepath_for_match(raw_path: Any) -> str:
    text = _safe_text(raw_path)
    if not text or text == "-":
        return ""
    try:
        return str(Path(text).expanduser().resolve(strict=False))
    except Exception:
        return str(Path(text).expanduser())


def _normalize_filename_for_match(raw_filename: Any) -> str:
    text = _safe_text(raw_filename)
    if not text or text == "-":
        return ""
    return Path(text).name.strip().lower()


def _normalize_manifest_match_rows(manifest_papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for paper in manifest_papers:
        if not isinstance(paper, dict):
            continue
        normalized = normalize_manifest_paper(paper)
        pid = _normalize_paper_id(normalized.get("paper_id") or normalized.get("id"))
        filepath = _normalize_filepath_for_match(normalized.get("filepath"))
        filename_key = _normalize_filename_for_match(normalized.get("filename"))
        if not filename_key and filepath:
            filename_key = _normalize_filename_for_match(Path(filepath).name)

        rows.append(
            {
                "paper": normalized,
                "paper_id": pid,
                "filepath_key": filepath,
                "filename_key": filename_key,
            }
        )
    return rows


def _unique_match(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    deduped: Dict[str, Dict[str, Any]] = {}
    for row in candidates:
        key = row.get("paper_id") or _safe_text(row.get("paper", {}).get("id"))
        if not key:
            key = str(id(row.get("paper")))
        if key not in deduped:
            deduped[key] = row
    if len(deduped) != 1:
        return None
    return next(iter(deduped.values()))


def resolve_manifest_paper_from_generation_output(
    manifest_papers: List[Dict[str, Any]],
    *,
    output_path: Any = "",
    filename: Any = "",
    paper_id: Any = "",
) -> Dict[str, Any]:
    """Resolve one manifest paper from generation output metadata.

    Match priority:
    1. explicit paper_id
    2. exact output_path vs manifest filepath
    3. explicit filename (or output_path basename) vs manifest filename
    4. fallback: output filename stem vs manifest paper_id (only when unique)
    """
    rows = _normalize_manifest_match_rows(manifest_papers)

    requested_paper_id = _normalize_paper_id(paper_id)
    requested_output_path = _normalize_filepath_for_match(output_path)
    requested_filename = _normalize_filename_for_match(filename)
    if not requested_filename and requested_output_path:
        requested_filename = _normalize_filename_for_match(Path(requested_output_path).name)

    requested_stem = ""
    if requested_filename:
        requested_stem = _normalize_paper_id(Path(requested_filename).stem)
    elif requested_output_path:
        requested_stem = _normalize_paper_id(Path(requested_output_path).stem)

    matched_row: Optional[Dict[str, Any]] = None
    match_rule = ""

    if requested_paper_id:
        matched_row = _unique_match([row for row in rows if row.get("paper_id") == requested_paper_id])
        if matched_row:
            match_rule = "paper_id"

    if not matched_row and requested_output_path:
        matched_row = _unique_match([row for row in rows if row.get("filepath_key") == requested_output_path])
        if matched_row:
            match_rule = "output_path"

    if not matched_row and requested_filename:
        matched_row = _unique_match([row for row in rows if row.get("filename_key") == requested_filename])
        if matched_row:
            match_rule = "filename"

    if not matched_row and requested_stem:
        matched_row = _unique_match([row for row in rows if row.get("paper_id") == requested_stem])
        if matched_row:
            match_rule = "stem_fallback"

    matched_paper = matched_row.get("paper") if matched_row else None
    resolved_paper_id = ""
    if matched_row:
        resolved_paper_id = str(matched_row.get("paper_id", "")).strip()
    if not resolved_paper_id:
        resolved_paper_id = requested_paper_id or requested_stem

    return {
        "paper": matched_paper,
        "resolved_paper_id": resolved_paper_id,
        "match_rule": match_rule,
        "requested": {
            "paper_id": requested_paper_id,
            "output_path": requested_output_path,
            "filename": requested_filename,
        },
    }


@lru_cache(maxsize=1)
def _get_lance_db():
    """Create and cache LanceDB connection for repository queries."""
    try:
        import lancedb

        return lancedb.connect(str(LANCEDB_PATH))
    except Exception:
        return None


def clear_lance_db_cache() -> None:
    """Clear cached repository LanceDB connection."""
    _get_lance_db.cache_clear()


def _mtime_iso_utc(filepath: Path) -> str:
    try:
        return datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""


def _parse_rewrite_meta_from_html(html_content: str) -> Dict[str, str]:
    """Parse rewrite style + generation time from storyteller HTML (see storyteller_pipeline meta block)."""
    rewrite_style_label = ""
    rewrite_style = "unknown"
    generated_at = ""

    m = re.search(
        r'<div[^>]*class="meta"[^>]*>\s*<strong>([^<]+)</strong>',
        html_content,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        rewrite_style_label = str(m.group(1) or "").strip()
        rewrite_style = _STYLE_LABEL_TO_KEY.get(rewrite_style_label, "unknown")

    m2 = re.search(
        r"Generated at \(UTC\):\s*([^\s<]+)",
        html_content,
        re.IGNORECASE,
    )
    if m2:
        generated_at = str(m2.group(1) or "").strip()

    return {
        "rewrite_style": rewrite_style,
        "rewrite_style_label": rewrite_style_label,
        "generated_at": generated_at,
    }


def _enrich_from_html_file_if_needed(item: Dict[str, Any]) -> Dict[str, Any]:
    """For index-only manifest rows: fill style / generated_at by reading HTML if present."""
    out = dict(item)
    fp = Path(str(out.get("filepath") or ""))
    if not fp.is_file() or fp.suffix.lower() != ".html":
        return out
    try:
        content = fp.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    meta = _parse_rewrite_meta_from_html(content)
    if not str(out.get("generated_at") or "").strip():
        ga = meta.get("generated_at") or ""
        out["generated_at"] = ga if ga else _mtime_iso_utc(fp)
    if str(out.get("rewrite_style") or "") in ("", "unknown"):
        rs = meta.get("rewrite_style") or "unknown"
        if rs != "unknown":
            out["rewrite_style"] = rs
            out["rewrite_style_label"] = meta.get("rewrite_style_label") or ""
    elif not str(out.get("rewrite_style_label") or "").strip() and meta.get("rewrite_style_label"):
        out["rewrite_style_label"] = str(meta.get("rewrite_style_label") or "")
    return out


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

    try:
        html_content = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    plain_text = _extract_text_from_html(html_content)

    title_match = re.search(r"<title>([^<]+)</title>", html_content)
    title = title_match.group(1) if title_match else filepath.stem

    author_match = re.search(r"作者[：:]\s*([^<\n]+)", plain_text)
    authors = author_match.group(1).strip() if author_match else "未知"

    date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", plain_text)
    date = date_match.group(1) if date_match else "未知"

    meta_rw = _parse_rewrite_meta_from_html(html_content)
    generated_at = str(meta_rw.get("generated_at") or "").strip()
    if not generated_at:
        generated_at = _mtime_iso_utc(filepath)

    return normalize_manifest_paper(
        {
            "paper_id": filepath.stem,
            "id": filepath.stem,
            "filename": filepath.name,
            "filepath": str(filepath),
            "title": title,
            "authors": authors,
            "date": date,
            "content": plain_text,
            "rewrite_style": meta_rw.get("rewrite_style") or "unknown",
            "rewrite_style_label": str(meta_rw.get("rewrite_style_label") or "").strip(),
            "generated_at": generated_at,
            "has_html": True,
            "is_indexed": False,
            "manifest_source": MANIFEST_SOURCE_HTML_ONLY,
        }
    )


def _normalize_paper_id(raw_paper_id: Any) -> str:
    """Normalize paper id for manifest joins (row id may contain chunk suffix)."""
    paper_id = str(raw_paper_id or "").strip()
    if not paper_id:
        return ""

    m = re.match(r"^(.*)_chunk_\d+$", paper_id)
    if m:
        return m.group(1)
    return paper_id


def _is_meaningful(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text != "未知")


def _prefer(indexed_value: Any, html_value: Any) -> Any:
    """Prefer indexed metadata; fallback to HTML scan when indexed is empty."""
    if _is_meaningful(indexed_value):
        return indexed_value
    return html_value


def _apply_display_title_override(item: Dict[str, Any], overrides: Dict[str, str]) -> Dict[str, Any]:
    out = dict(item)
    paper_id = _normalize_paper_id(out.get("paper_id") or out.get("id"))
    override = str(overrides.get(paper_id, "")).strip() if paper_id else ""
    if override:
        out["title"] = override
    return out


def apply_display_title_overrides(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply custom display title overrides to a list of paper-like dict items."""
    overrides = retrieval_get_display_title_overrides()
    if not overrides:
        return [dict(item) for item in items]
    return [_apply_display_title_override(item, overrides) for item in items]


def scan_html_papers() -> List[Dict]:
    """Scan HTML papers from STORYTELLERS_DIR and parse basic metadata."""
    papers: List[Dict] = []
    for html_file in sorted(STORYTELLERS_DIR.glob("*.html")):
        parsed = parse_paper_metadata(html_file)
        if parsed:
            papers.append(parsed)
    return papers


def get_indexed_papers() -> List[Dict]:
    """Get deduplicated papers from LanceDB table by paper_id."""
    db = _get_lance_db()
    if db is None:
        return []

    try:
        tbl = db.open_table("papers")
        all_rows = tbl.to_pandas().to_dict("records")

        # Dedupe by normalized paper_id and keep first row.
        seen: Dict[str, Dict] = {}
        for row in all_rows:
            pid = _normalize_paper_id(row.get("paper_id") or row.get("id"))
            if not pid or pid in seen:
                continue

            filename = str(row.get("filename", "")).strip() or f"{pid}.html"
            filepath = STORYTELLERS_DIR / filename

            seen[pid] = {
                "paper_id": pid,
                "id": row.get("id") or pid,
                "filename": filename,
                "filepath": str(filepath),
                "title": row.get("title") or pid,
                "authors": row.get("authors") or "未知",
                "date": row.get("date") or "未知",
                "content": row.get("content") or "",
                "has_html": filepath.exists(),
                "is_indexed": True,
                "manifest_source": MANIFEST_SOURCE_INDEX_ONLY,
            }

        return [normalize_manifest_paper(item) for item in seen.values()]
    except Exception:
        return []


def merge_paper_sources(html_papers: List[Dict], indexed_papers: List[Dict]) -> List[Dict]:
    """Merge indexed papers and scanned HTML metadata into one manifest."""
    html_by_id = {
        str(p.get("paper_id", "")).strip(): p
        for p in html_papers
        if str(p.get("paper_id", "")).strip()
    }

    merged: List[Dict] = []

    # Keep indexed order stable, then append html-only papers.
    for indexed in indexed_papers:
        pid = str(indexed.get("paper_id", "")).strip()
        if not pid:
            continue

        html = html_by_id.pop(pid, None)
        if html:
            filename = str(html.get("filename") or indexed.get("filename") or f"{pid}.html")
            filepath = str(html.get("filepath") or (STORYTELLERS_DIR / filename))
            merged.append(
                {
                    "paper_id": pid,
                    "id": indexed.get("id") or html.get("id") or pid,
                    "filename": filename,
                    "filepath": filepath,
                    "title": _prefer(indexed.get("title"), html.get("title")) or pid,
                    "authors": _prefer(indexed.get("authors"), html.get("authors")) or "未知",
                    "date": _prefer(indexed.get("date"), html.get("date")) or "未知",
                    "content": _prefer(indexed.get("content"), html.get("content")) or "",
                    "rewrite_style": html.get("rewrite_style") or "unknown",
                    "rewrite_style_label": str(html.get("rewrite_style_label") or "").strip(),
                    "generated_at": str(html.get("generated_at") or "").strip(),
                    "has_html": True,
                    "is_indexed": True,
                    "manifest_source": MANIFEST_SOURCE_INDEX_AND_HTML,
                }
            )
        else:
            item = dict(indexed)
            item["paper_id"] = pid
            item["is_indexed"] = True
            item["manifest_source"] = MANIFEST_SOURCE_INDEX_ONLY
            item["has_html"] = bool(item.get("has_html"))
            item.setdefault("rewrite_style", "unknown")
            item.setdefault("rewrite_style_label", "")
            item.setdefault("generated_at", "")
            item = _enrich_from_html_file_if_needed(item)
            if not str(item.get("generated_at") or "").strip():
                fp2 = Path(str(item.get("filepath") or ""))
                if fp2.is_file():
                    item["generated_at"] = _mtime_iso_utc(fp2)
            merged.append(item)

    for pid in sorted(html_by_id.keys()):
        html = dict(html_by_id[pid])
        html["paper_id"] = pid
        html["id"] = html.get("id") or pid
        html["has_html"] = True
        html["is_indexed"] = False
        html["manifest_source"] = MANIFEST_SOURCE_HTML_ONLY
        merged.append(html)

    return [normalize_manifest_paper(item) for item in merged]


def build_paper_manifest() -> List[Dict]:
    """Build combined paper manifest from HTML files and indexed LanceDB rows."""
    html_papers = scan_html_papers()
    indexed_papers = get_indexed_papers()
    merged = merge_paper_sources(html_papers=html_papers, indexed_papers=indexed_papers)
    return apply_display_title_overrides(merged)


def get_all_papers() -> List[Dict]:
    """Backward-compatible paper list; now returns merged manifest."""
    return build_paper_manifest()


def delete_paper(paper_id: Any) -> Dict[str, Any]:
    """Delete paper artifact + index rows via retrieval service and clear repo cache."""
    normalized_paper_id = _normalize_paper_id(paper_id)
    if not normalized_paper_id:
        clear_lance_db_cache()
        return {
            "ok": False,
            "paper_id": "",
            "message": "paper_id 不可為空",
            "repository_cache_cleared": True,
        }

    result = retrieval_delete_paper(normalized_paper_id)
    clear_lance_db_cache()

    if not isinstance(result, dict):
        return {
            "ok": False,
            "paper_id": normalized_paper_id,
            "message": "delete_paper 回傳格式錯誤",
            "repository_cache_cleared": True,
        }

    output = dict(result)
    output["repository_cache_cleared"] = True
    return output


def rename_paper(paper_id: Any, new_name: str) -> Dict[str, Any]:
    """Rename paper HTML file and remove stale index rows; clear repo cache."""
    normalized_paper_id = _normalize_paper_id(paper_id)
    if not normalized_paper_id:
        clear_lance_db_cache()
        return {
            "ok": False,
            "paper_id": "",
            "new_name": new_name,
            "message": "paper_id 不可為空",
            "repository_cache_cleared": True,
        }

    result = retrieval_rename_paper(normalized_paper_id, new_name)
    clear_lance_db_cache()

    if not isinstance(result, dict):
        return {
            "ok": False,
            "paper_id": normalized_paper_id,
            "new_name": new_name,
            "message": "rename_paper 回傳格式錯誤",
            "repository_cache_cleared": True,
        }

    output = dict(result)
    output["repository_cache_cleared"] = True
    return output


def update_paper_display_title(paper_id: Any, display_title: str) -> Dict[str, Any]:
    """Update only paper display title (title field) without changing filename/paper_id."""
    normalized_paper_id = _normalize_paper_id(paper_id)
    if not normalized_paper_id:
        clear_lance_db_cache()
        return {
            "ok": False,
            "paper_id": "",
            "display_title": str(display_title or "").strip(),
            "message": "paper_id 不可為空",
            "repository_cache_cleared": True,
        }

    result = retrieval_update_paper_display_title(normalized_paper_id, display_title)
    clear_lance_db_cache()

    if not isinstance(result, dict):
        return {
            "ok": False,
            "paper_id": normalized_paper_id,
            "display_title": str(display_title or "").strip(),
            "message": "update_paper_display_title 回傳格式錯誤",
            "repository_cache_cleared": True,
        }

    output = dict(result)
    output["repository_cache_cleared"] = True
    return output
