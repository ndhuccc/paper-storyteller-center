#!/usr/bin/env python3
"""Minimal storyteller generation pipeline for one PDF -> one HTML output."""

from __future__ import annotations

import html
import json
import markdown
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai
from dotenv import load_dotenv


STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
DEFAULT_REWRITE_MODEL = "models/gemini-3.1-flash-lite-preview"
DEFAULT_REWRITE_FALLBACK_CHAIN: List[Dict[str, str]] = [
    {"model": "gemma4:e2b",     "provider": "ollama",     "ollama_base_url": "http://134.208.2.42:11434"},
    {"model": "MiniMax-M2.5",   "provider": "minimax.io"},
    {"model": "deepseek-r1:8b", "provider": "ollama",     "ollama_base_url": "http://134.208.2.42:11434"},
]
DEFAULT_PDF_EXTRACTION_MODEL = "models/gemini-3.1-flash-lite-preview"
PDF_EXTRACTION_FALLBACK_MODEL = "gemini-2.5-flash"
DEFAULT_OLLAMA_BASE_URL = "http://134.208.2.42:11434"
DEFAULT_MINIMAX_PORTAL_BASE_URL = "https://api.minimax.io"
DEFAULT_MAX_SECTIONS = 0
DEFAULT_REWRITE_CHUNK_CHARS = 3000
DEFAULT_REWRITE_MODE = "paragraph"
DEFAULT_APPEND_MISSING_FORMULAS = False
DEFAULT_REWRITE_RESPONSE_FORMAT = "markdown"
DEFAULT_STYLE = "storyteller"
LATEX_PLACEHOLDER = "LATEXPH"
GEMINI_EXTRACTION_PROMPT = (
    "請完美提取這份 PDF 的純文字內容，保留原有的章節標題結構（使用 Markdown 標題如 # 或 ##），"
    "並將所有的數學公式完美轉換為標準 LaTeX 語法（使用 $$...$$ 或 $...$ 包裝）。"
    "請直接輸出乾淨的 Markdown 內容，不要包含任何開場白或多餘解釋。"
)
HEADING_HINTS = (
    "abstract",
    "introduction",
    "background",
    "related work",
    "method",
    "methods",
    "approach",
    "experiment",
    "experiments",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgement",
    "acknowledgements",
    "appendix",
)

load_dotenv(dotenv_path=STORYTELLERS_DIR / ".env", override=False)
load_dotenv(dotenv_path=Path.home() / ".env", override=False)

STYLE_PROMPTS: Dict[str, str] = {
    "storyteller": """說書人（故事化解說 + Why-first + 讀者視角 + 公式拆解）

【角色視角】
- 你是擅長把技術內容講成故事的通俗作家，目標讀者是初學者。
- 少用「本文討論」「該方法」等論文語氣。
- 多用「我們一起看」「想像一下」「為什麼會這樣？」等引導讀者的口吻。

【結構方式（逐單元改寫）】
- 先逐段消化內容，抓住段落重點，不可捨棄原文任一段落。
- 以 subsection 為改寫單元（若無 subsection 則以 section 為單元）。
- 每個改寫單元依序進行：
  1. 開頭：用日常情境、問題或懸念引入，讓讀者先有感覺再看技術。
  2. 中段：逐步揭示單元的核心重點，保持「先動機與直覺，再方法步驟」節奏。
  3. 洞察：說明關鍵洞察與推論，解釋「為什麼這樣設計、為什麼這樣做會有效」。
  4. 結尾：交代此單元重點的重要性與影響力，讓讀者知道「這段說了什麼、為何重要」。
- 改寫後篇幅可隨單元重點數量彈性調整；每個改寫單元給予適當的 Markdown 標題。

【表達規則】
- 技術術語**第一次出現時**，立刻用白話解釋（括號或破折號皆可）。
- 首次出現的重要概念請加 **粗體**。
- 優先用生活化、結構相似的類比幫助理解（交通、廚房、工廠、排隊、導航、團隊協作等），類比必須貼合原意。
- 適度擬人化、提問、製造節奏感，增加閱讀流暢度。

【文字風格】
- 長短句交替，像在說故事，不像在念教科書。
- 溫暖度 7／10、視覺化程度 8／10、數學密度 4／10、詼諧感 0.5／1。

【專業術語表（每個改寫單元必須附上）】
- 每個改寫單元的正文之後，必須附上一張「📖 本節術語表」。
- 列出該單元正文中出現的所有專業術語（包含縮寫、模型名稱、方法名稱）。
- 格式為 Markdown 表格，欄位：術語 | 白話解釋；每列一個術語。
- 解釋文字須讓完全沒有背景的初學者也能理解，避免再用另一個術語解釋術語。
- 若同一術語在前面單元已列過，本單元仍需重複列出（方便讀者單獨閱讀本節）。""",
    "blog": """科普部落格（鉤子句 + 段落標題 + 結尾留問題）
- 第一段先用一句有吸引力的鉤子句開場。
- 內文用 2-3 個短標題分段（例如【問題背景】、【方法重點】）。
- 最後留下一個延伸思考問題。""",
    "podcast": """Podcast（口語化、對話感）
- 用口語自然的語氣，像主持人在向聽眾說明。
- 允許適度使用「你可以想像」「我們來看」等對話引導句。
- 內容要順暢、有節奏，但不能偏離原文技術重點。""",
    "fairy": """童話故事（擬人化、主角/挑戰/勝利結構）
- 把方法中的關鍵元件擬人化為角色。
- 結構採「主角 → 挑戰 → 解法/勝利」。
- 保留技術正確性，不把數學內容改寫成錯誤寓言。""",
    "lazy": """懶人包（bullet points、圖像化、快速抓重點）
- 以 4-6 個條列點整理重點，每點一句到兩句。
- 先講結論，再補充必要背景。
- 用具象比喻幫助快速理解，但不要發明不存在的結果。""",
    "question": """問題驅動（先問問題、再逐層解釋）
- 先提出 1-2 個核心問題引導讀者。
- 依序回答：問題是什麼 → 為什麼難 → 作者怎麼解。
- 結尾收斂到實驗結果或限制。""",
    "log": """實驗日誌（研究過程記錄、工程師視角）
- 用工程師觀點描述研究流程與決策取捨。
- 強調「觀察到什麼問題、做了什麼調整、得到什麼結果」。
- 語氣客觀，像可追蹤的實驗紀錄。""",
}


def run_storyteller_pipeline(job: Dict[str, Any], *, phase_reporter=None) -> Dict[str, Any]:
    """Run one minimal storyteller generation job end-to-end.

    Args:
        job: Job dict from the job store.
        phase_reporter: Optional callable(label: str) -> None.  Called at each
            major pipeline phase so the caller can persist progress (e.g. write a
            ``phase`` field back to the job store for frontend polling).
    """
    payload = job.get("payload", {}) if isinstance(job, dict) else {}
    if not isinstance(payload, dict):
        payload = {}

    pdf_path = _resolve_pdf_path(job=job, payload=payload)
    if pdf_path is None:
        raise ValueError(
            "No readable PDF path found in job payload. "
            "Supported keys include pdf_path/source_pdf_path/input_path/file_path/path/pdf."
        )

    if phase_reporter:
        phase_reporter("PDF 文字掃描中…")
    extracted_text, extraction_warning, pdf_extraction_model = _extract_pdf_text(pdf_path)
    if not extracted_text.strip():
        raise RuntimeError(f"No text extracted from PDF: {pdf_path}")

    if phase_reporter:
        phase_reporter("段落結構解析中…")
    sections = _split_into_sections(extracted_text)
    if not sections:
        raise RuntimeError(f"Unable to build sections from extracted text: {pdf_path}")

    max_sections = _safe_positive_int(payload.get("max_sections"), DEFAULT_MAX_SECTIONS)
    rewrite_chunk_chars = _safe_positive_int(
        payload.get("rewrite_chunk_chars"),
        DEFAULT_REWRITE_CHUNK_CHARS,
    )
    rewrite_mode = _normalize_rewrite_mode(payload.get("rewrite_mode"))
    rewrite_response_format = _normalize_rewrite_response_format(
        payload.get("rewrite_response_format")
    )
    append_missing_formulas = _normalize_bool(
        payload.get("append_missing_formulas"),
        DEFAULT_APPEND_MISSING_FORMULAS,
    )
    primary_model = str(payload.get("model") or DEFAULT_REWRITE_MODEL)
    fallback_chain_raw = payload.get("rewrite_fallback_chain")
    if isinstance(fallback_chain_raw, list) and fallback_chain_raw:
        fallback_chain: List[Dict[str, str]] = [
            {k: str(v) for k, v in spec.items() if isinstance(v, (str, int, float))}
            for spec in fallback_chain_raw if isinstance(spec, dict)
        ]
    else:
        fallback_chain = [dict(spec) for spec in DEFAULT_REWRITE_FALLBACK_CHAIN]
    # Derive legacy single-fallback fields for backward-compat summary/return values.
    _first_fallback = fallback_chain[0] if fallback_chain else {}
    fallback_model: str = _first_fallback.get("model", "")
    fallback_provider: str = _first_fallback.get("provider", "")
    ollama_base_url = str(payload.get("ollama_base_url") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
    minimax_base_url = str(
        payload.get("minimax_base_url") or os.getenv("MINIMAX_PORTAL_BASE_URL") or DEFAULT_MINIMAX_PORTAL_BASE_URL
    ).rstrip("/")
    minimax_oauth_token = str(
        payload.get("minimax_oauth_token")
        or payload.get("minimax_token")
        or os.getenv("MINIMAX_PORTAL_OAUTH_TOKEN")
        or os.getenv("MINIMAX_OAUTH_TOKEN")
        or ""
    ).strip()
    style = _normalize_style(payload.get("style"))

    rendered_sections: List[Dict[str, Any]] = []
    llm_failures: List[str] = []
    rewrite_models_used: set[str] = set()
    if extraction_warning:
        llm_failures.append(extraction_warning)

    selected_sections = sections
    skipped_sections = 0
    if max_sections > 0:
        selected_sections = sections[:max_sections]
        skipped_sections = max(0, len(sections) - len(selected_sections))
        if skipped_sections > 0:
            llm_failures.append(
                f"Skipped {skipped_sections} sections because max_sections={max_sections}."
            )

    rewrite_chunks_generated = 0
    total_sections = len(selected_sections)
    for index, section in enumerate(selected_sections, start=1):
        if phase_reporter:
            phase_reporter(f"說書改寫中（第 {index}／{total_sections} 節）…")
        rewrite_parts = _split_section_into_rewrite_parts(
            section_title=section["title"],
            source_text=section["source_text"],
            max_chunk_chars=rewrite_chunk_chars,
            rewrite_mode=rewrite_mode,
        )
        rewrite_chunks_generated += len(rewrite_parts)

        section_story_parts: List[str] = []
        section_terms: List[Dict[str, str]] = []
        section_formula_explanations: List[Dict[str, str]] = []
        section_used_llm = False
        section_used_models: set[str] = set()

        for part_index, part in enumerate(rewrite_parts, start=1):
            rewritten_text, terms, formula_explanations, used_llm, failure, used_model = _rewrite_section(
                section_title=part["title"],
                source_text=part["source_text"],
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                style=style,
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
            )
            if failure:
                llm_failures.append(
                    f"section {index} part {part_index}/{len(rewrite_parts)}: {failure}"
                )
            if used_model:
                section_used_models.add(used_model)
                rewrite_models_used.add(used_model)
            if used_llm:
                section_used_llm = True
            if rewritten_text.strip():
                section_story_parts.append(rewritten_text.strip())
            if terms:
                section_terms.extend(terms)
            if formula_explanations:
                section_formula_explanations.extend(formula_explanations)

        section_story_text = "\n\n".join(section_story_parts).strip()
        if not section_story_text:
            section_story_text = section["source_text"]

        rendered_sections.append(
            {
                "index": index,
                "title": section["title"],
                "source_text": section["source_text"],
                "story_text": section_story_text,
                "terms": section_terms,
                "formula_explanations": section_formula_explanations,
                "used_llm": section_used_llm,
                "used_model": ", ".join(sorted(section_used_models)),
                "rewrite_chunks": len(rewrite_parts),
            }
        )

    rewrite_model = primary_model
    fallback_models_used = rewrite_models_used - {primary_model}
    if fallback_models_used and primary_model in rewrite_models_used:
        rewrite_model = f"{primary_model} (partial fallback: {', '.join(sorted(fallback_models_used))})"
    elif fallback_models_used and primary_model not in rewrite_models_used:
        rewrite_model = ", ".join(sorted(fallback_models_used))

    if phase_reporter:
        phase_reporter("輸出 HTML…")
    title = _resolve_title(payload=payload, pdf_path=pdf_path, sections=rendered_sections)
    output_path = _build_output_path(pdf_path=pdf_path, payload=payload)
    output_html = _build_story_html_document(
        title=title,
        pdf_path=pdf_path,
        rendered_sections=rendered_sections,
        model=rewrite_model,
    )

    STORYTELLERS_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_html, encoding="utf-8")

    return {
        "pipeline": "storyteller_hybrid_generation",
        "implemented": True,
        "job_id": job.get("job_id"),
        "input": payload,
        "pdf_path": str(pdf_path),
        "output_path": str(output_path),
        "model": rewrite_model,
        "rewrite_model_primary": primary_model,
        "rewrite_model_fallback": fallback_model,
        "rewrite_model_fallback_provider": fallback_provider,
        "pdf_extraction_model": pdf_extraction_model,
        "style": style,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections_detected": len(sections),
        "sections_processed": len(selected_sections),
        "sections_skipped_by_limit": skipped_sections,
        "sections_generated": len(rendered_sections),
        "rewrite_chunks_generated": rewrite_chunks_generated,
        "rewrite_chunk_chars": rewrite_chunk_chars,
        "rewrite_mode": rewrite_mode,
        "rewrite_response_format": rewrite_response_format,
        "append_missing_formulas": append_missing_formulas,
        "max_sections": max_sections,
        "steps": [
            {"name": "ingest_source", "status": "done", "note": str(pdf_path)},
            {
                "name": "pdf_to_structured_content",
                "status": "done",
                "note": (
                    f"{len(sections)} detected / {len(selected_sections)} processed"
                    f" / {skipped_sections} skipped by max_sections"
                ),
            },
            {
                "name": "html_story_render",
                "status": "done",
                "note": str(output_path),
            },
        ],
        "artifacts": [
            {
                "type": "html",
                "style": style,
                "path": str(output_path),
                "filename": output_path.name,
            }
        ],
        "warnings": llm_failures,
    }


def _resolve_pdf_path(job: Dict[str, Any], payload: Dict[str, Any]) -> Optional[Path]:
    candidate_values: List[str] = []

    # Most common payload keys first.
    for key in (
        "pdf_path",
        "source_pdf_path",
        "input_pdf_path",
        "input_path",
        "file_path",
        "path",
        "pdf",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            candidate_values.append(value.strip())

    source = payload.get("source")
    if isinstance(source, dict):
        for key in ("pdf_path", "path", "file_path"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                candidate_values.append(value.strip())

    # Fall back to scanning job-level keys if needed.
    for key in (
        "pdf_path",
        "source_pdf_path",
        "input_path",
        "file_path",
        "path",
        "pdf",
    ):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            candidate_values.append(value.strip())

    seen: set[str] = set()
    for candidate in candidate_values:
        for path in _candidate_pdf_paths(candidate):
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.exists() and path.is_file() and path.suffix.lower() == ".pdf":
                return path.resolve()
    return None


def _candidate_pdf_paths(raw_value: str) -> List[Path]:
    value = raw_value.strip()
    if not value:
        return []

    base = Path(value).expanduser()
    paths = [base]
    if not base.is_absolute():
        paths.append(Path.cwd() / base)
        paths.append(STORYTELLERS_DIR / base)
    return paths


def _extract_pdf_text(pdf_path: Path) -> Tuple[str, Optional[str], str]:
    gemini_api_key = _configure_gemini()
    if not gemini_api_key:
        return (
            _extract_pdf_text_with_pymupdf(pdf_path),
            "No Gemini API Key found; fell back to PyMuPDF.",
            "pymupdf (fallback)",
        )

    sample_file = None
    extraction_models = [DEFAULT_PDF_EXTRACTION_MODEL, PDF_EXTRACTION_FALLBACK_MODEL]
    model_errors: List[str] = []
    try:
        sample_file = genai.upload_file(path=str(pdf_path))
        _wait_for_gemini_file_ready(sample_file.name)

        for model_name in extraction_models:
            model = genai.GenerativeModel(model_name)
            try:
                response = model.generate_content(
                    [sample_file, GEMINI_EXTRACTION_PROMPT],
                    request_options={"timeout": 600},
                )
            except TypeError:
                response = model.generate_content([sample_file, GEMINI_EXTRACTION_PROMPT])
            except Exception as exc:
                model_errors.append(f"{model_name}: {type(exc).__name__}: {exc}")
                continue

            markdown_text = str(getattr(response, "text", "") or "").strip()
            if markdown_text:
                warning = None
                if model_name != DEFAULT_PDF_EXTRACTION_MODEL:
                    warning = (
                        "Primary extraction model failed; "
                        f"used fallback {model_name}."
                    )
                return _normalize_extracted_text(markdown_text), warning, model_name
            model_errors.append(f"{model_name}: empty extraction content")
    except Exception as e:
        model_errors.append(f"upload/process: {type(e).__name__}: {e}")
    finally:
        if sample_file is not None:
            try:
                genai.delete_file(sample_file.name)
            except Exception:
                pass

    detail = "; ".join(model_errors) if model_errors else "unknown reason"
    return (
        _extract_pdf_text_with_pymupdf(pdf_path),
        f"Gemini extraction failed ({detail}); fell back to PyMuPDF.",
        "pymupdf (fallback)",
    )


def _configure_gemini() -> str:
    # Reload env for long-lived workers that may receive new keys at runtime.
    load_dotenv(dotenv_path=STORYTELLERS_DIR / ".env", override=False)
    load_dotenv(dotenv_path=Path.home() / ".env", override=False)

    candidate_keys = [
        str(os.getenv("GOOGLE_API_KEY") or "").strip(),
        str(os.getenv("GEMINI_API_KEY") or "").strip(),
    ]
    seen: set[str] = set()
    for api_key in candidate_keys:
        if not api_key or api_key in seen:
            continue
        seen.add(api_key)
        if _is_gemini_key_working(api_key):
            return api_key
    return ""


def _is_gemini_key_working(api_key: str) -> bool:
    key = str(api_key or "").strip()
    if not key:
        return False
    try:
        genai.configure(api_key=key)
        # Probe one model to verify the key is accepted by this endpoint.
        next(genai.list_models(page_size=1), None)
        return True
    except Exception:
        return False


def _wait_for_gemini_file_ready(file_name: str, timeout_seconds: int = 90) -> None:
    deadline = time.time() + max(timeout_seconds, 1)
    while time.time() < deadline:
        uploaded = genai.get_file(file_name)
        state_name = str(getattr(getattr(uploaded, "state", None), "name", "")).upper()
        if not state_name or state_name == "ACTIVE":
            return
        if state_name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {file_name}")
        time.sleep(1.5)
    raise TimeoutError(f"Timed out waiting for Gemini file processing: {file_name}")


def _extract_pdf_text_with_pymupdf(pdf_path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF extraction") from exc

    try:
        document = fitz.open(str(pdf_path))
    except Exception as exc:
        raise RuntimeError(f"Failed to open PDF with PyMuPDF: {pdf_path}") from exc

    try:
        lines = _extract_structured_pdf_lines(document)
    finally:
        document.close()

    if not lines:
        return ""

    structured_text = _compose_text_from_pdf_lines(lines)
    return _normalize_extracted_text(structured_text)


def _extract_structured_pdf_lines(document: Any) -> List[Dict[str, Any]]:
    line_items: List[Dict[str, Any]] = []
    for page_index, page in enumerate(document):
        page_dict = page.get_text("dict")
        blocks = [block for block in page_dict.get("blocks", []) if block.get("type") == 0]
        blocks.sort(key=lambda block: _bbox_sort_key(block.get("bbox")))

        for block in blocks:
            for line in block.get("lines", []):
                spans = [span for span in line.get("spans", []) if str(span.get("text", "")).strip()]
                if not spans:
                    continue
                text = _join_pdf_spans(spans)
                if not text:
                    continue

                size_values = [float(span.get("size") or 0.0) for span in spans]
                max_size = max(size_values) if size_values else 0.0
                is_bold = any(
                    _is_bold_font_name(str(span.get("font", ""))) or (int(span.get("flags", 0)) & 16)
                    for span in spans
                )

                line_bbox = line.get("bbox") or spans[0].get("bbox") or [0.0, 0.0, 0.0, 0.0]
                x0 = float(line_bbox[0]) if len(line_bbox) > 0 else 0.0
                y0 = float(line_bbox[1]) if len(line_bbox) > 1 else 0.0

                line_items.append(
                    {
                        "page": page_index,
                        "x0": x0,
                        "y0": y0,
                        "text": text,
                        "font_size": max_size,
                        "is_bold": bool(is_bold),
                    }
                )

    line_items.sort(key=lambda item: (item["page"], item["y0"], item["x0"]))
    body_font = _estimate_body_font_size(line_items)
    for item in line_items:
        item["is_heading_hint"] = _is_structural_heading_line(
            text=item["text"],
            font_size=float(item.get("font_size") or 0.0),
            is_bold=bool(item.get("is_bold")),
            body_font_size=body_font,
        )
    return line_items


def _compose_text_from_pdf_lines(lines: List[Dict[str, Any]]) -> str:
    if not lines:
        return ""

    sections: List[str] = []
    current_paragraph = ""
    previous_line: Optional[Dict[str, Any]] = None

    def _flush_paragraph() -> None:
        nonlocal current_paragraph
        paragraph = current_paragraph.strip()
        if paragraph:
            sections.append(paragraph)
        current_paragraph = ""

    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text or _is_simple_page_artifact_line(text):
            previous_line = line
            continue

        if bool(line.get("is_heading_hint")):
            _flush_paragraph()
            sections.append(_normalize_heading_text(text))
            previous_line = line
            continue

        start_new_paragraph = False
        if previous_line is None:
            start_new_paragraph = True
        elif line["page"] != previous_line["page"]:
            start_new_paragraph = True
        elif _is_list_item_start(text):
            start_new_paragraph = True
        else:
            vertical_gap = float(line["y0"]) - float(previous_line.get("y0", line["y0"]))
            previous_text = str(previous_line.get("text", "")).strip()
            if vertical_gap > 18:
                start_new_paragraph = True
            elif previous_text and re.search(r"[.?!:;。？！：；]$", previous_text):
                if vertical_gap > 10 and not re.match(r"^[a-z0-9(\[\"'“‘]", text):
                    start_new_paragraph = True

        if start_new_paragraph:
            _flush_paragraph()
            current_paragraph = text
        else:
            current_paragraph = _merge_text_fragments(current_paragraph, text)

        previous_line = line

    _flush_paragraph()
    return "\n\n".join(section for section in sections if section.strip())


def _bbox_sort_key(bbox: Any) -> Tuple[float, float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 2:
        return (0.0, 0.0)
    try:
        return (float(bbox[1]), float(bbox[0]))
    except (TypeError, ValueError):
        return (0.0, 0.0)


def _join_pdf_spans(spans: List[Dict[str, Any]]) -> str:
    text = "".join(str(span.get("text", "")) for span in spans)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _is_bold_font_name(font_name: str) -> bool:
    lowered = font_name.lower()
    return any(token in lowered for token in ("bold", "black", "heavy", "demi"))


def _estimate_body_font_size(lines: List[Dict[str, Any]]) -> float:
    if not lines:
        return 11.0

    candidates: List[float] = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        size = float(line.get("font_size") or 0.0)
        if size <= 0:
            continue
        if len(text) < 25:
            continue
        if _looks_like_heading(text):
            continue
        candidates.append(size)

    if not candidates:
        candidates = [float(line.get("font_size") or 0.0) for line in lines if float(line.get("font_size") or 0.0) > 0]
    if not candidates:
        return 11.0
    return float(median(candidates))


def _is_structural_heading_line(*, text: str, font_size: float, is_bold: bool, body_font_size: float) -> bool:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned or len(cleaned) > 160:
        return False

    if _looks_like_heading(cleaned):
        return True

    if re.search(r"[.?!。？！]$", cleaned):
        return False

    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*|[\u4e00-\u9fff]+|\d+(?:\.\d+)*", cleaned)
    if not words or len(words) > 16:
        return False

    size_ratio = font_size / max(body_font_size, 1.0)
    if size_ratio >= 1.18:
        return True
    if is_bold and size_ratio >= 1.05 and len(words) <= 12:
        return True
    return False


def _normalize_extracted_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    lines = [line.rstrip() for line in normalized.splitlines()]
    cleaned_lines: List[str] = []
    for line in lines:
        striped = line.strip()
        if not striped:
            cleaned_lines.append("")
            continue
        if re.fullmatch(r"\d{1,4}", striped):
            continue
        if re.fullmatch(r"(?i)page\s+\d+(\s+of\s+\d+)?", striped):
            continue
        cleaned_lines.append(striped)

    cleaned_lines = _drop_repeated_page_artifacts(cleaned_lines)
    compact = "\n".join(cleaned_lines)
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    return compact.strip()


def _drop_repeated_page_artifacts(lines: List[str]) -> List[str]:
    if not lines:
        return []

    counts: Dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        key = _artifact_signature(stripped)
        if key:
            counts[key] = counts.get(key, 0) + 1

    repeated = {key for key, count in counts.items() if count >= 3}
    if not repeated:
        return list(lines)

    filtered: List[str] = []
    for line in lines:
        stripped = line.strip()
        key = _artifact_signature(stripped) if stripped else None
        if key and key in repeated:
            continue
        filtered.append(line)
    return filtered


def _artifact_signature(line: str) -> Optional[str]:
    text = re.sub(r"\s+", " ", line).strip()
    if not text or len(text) > 90:
        return None
    if len(text.split()) > 12:
        return None
    if re.search(r"[.?!。？！]$", text):
        return None
    if re.fullmatch(r"[\W_]+", text):
        return None
    if _looks_like_heading(text):
        return None

    lowered = text.lower()
    has_metadata_term = bool(
        re.search(
            r"\b(arxiv|preprint|proceedings|conference|journal|copyright|doi|accepted|manuscript)\b",
            lowered,
        )
    )
    if not has_metadata_term and not _is_mostly_title_or_upper(text):
        return None

    normalized = re.sub(r"\d+", "0", lowered)
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized) < 4:
        return None
    return normalized


def _is_mostly_title_or_upper(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*", text)
    if not words:
        return False
    if text == text.upper():
        return True
    titled = sum(1 for word in words if word[0].isupper())
    return titled / len(words) >= 0.7


def _split_into_sections(extracted_text: str) -> List[Dict[str, str]]:
    blocks = _split_blocks(extracted_text)
    if not blocks:
        return []

    sections: List[Dict[str, str]] = []
    current_title = "Overview"
    current_paragraphs: List[str] = []

    for block in blocks:
        inline_heading = _split_inline_heading_block(block)
        if inline_heading is not None:
            heading, body = inline_heading
            if current_paragraphs:
                _append_or_merge_section(
                    sections=sections,
                    title=current_title,
                    source_text="\n\n".join(current_paragraphs).strip(),
                )
                current_paragraphs = []
            if _same_heading(heading, current_title) and not body:
                continue
            current_title = heading
            if body:
                current_paragraphs.append(body)
            continue

        if _looks_like_heading(block):
            normalized_heading = _normalize_heading_text(block)
            if current_paragraphs:
                _append_or_merge_section(
                    sections=sections,
                    title=current_title,
                    source_text="\n\n".join(current_paragraphs).strip(),
                )
                current_paragraphs = []
            if _same_heading(normalized_heading, current_title):
                continue
            current_title = normalized_heading
            continue
        current_paragraphs.append(block)

    if current_paragraphs:
        _append_or_merge_section(
            sections=sections,
            title=current_title,
            source_text="\n\n".join(current_paragraphs).strip(),
        )

    if not sections:
        return [{"title": "Content", "source_text": "\n\n".join(blocks)}]
    return sections


def _append_or_merge_section(*, sections: List[Dict[str, str]], title: str, source_text: str) -> None:
    text = source_text.strip()
    if not text:
        return
    if sections and _same_heading(sections[-1].get("title", ""), title):
        previous = str(sections[-1].get("source_text", "")).strip()
        sections[-1]["source_text"] = f"{previous}\n\n{text}".strip() if previous else text
        return
    sections.append({"title": title, "source_text": text})


def _same_heading(left: str, right: str) -> bool:
    left_key = re.sub(r"\s+", " ", str(left or "")).strip().casefold()
    right_key = re.sub(r"\s+", " ", str(right or "")).strip().casefold()
    return bool(left_key and right_key and left_key == right_key)


def _split_inline_heading_block(block: str) -> Optional[Tuple[str, str]]:
    text = re.sub(r"\s+", " ", block).strip()
    if not text:
        return None

    hints_pattern = "|".join(re.escape(hint) for hint in HEADING_HINTS)
    match = re.match(
        rf"^(?P<head>{hints_pattern})\s*(?:[-–—:]\s+|\s{{2,}})(?P<body>.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        heading = _normalize_heading_text(match.group("head"))
        body = match.group("body").strip()
        if len(body) >= 30:
            return heading, body

    abstract_inline = re.match(r"^(abstract)\s+(?P<body>.+)$", text, flags=re.IGNORECASE)
    if abstract_inline:
        body = abstract_inline.group("body").strip()
        if len(body) >= 40 and re.search(r"[.?!。？！]", body):
            return "Abstract", body

    return None


def _normalize_heading_text(text: str) -> str:
    heading = re.sub(r"\s+", " ", text).strip(" \t-–—:")
    heading = re.sub(r"^#{1,6}\s+", "", heading).strip()
    heading = re.sub(r"\s+#+\s*$", "", heading).strip()
    if not heading:
        return heading
    if heading.isupper() or heading.islower():
        return heading.title()
    return heading


def _split_blocks(extracted_text: str) -> List[str]:
    lines = _drop_repeated_page_artifacts(extracted_text.splitlines())
    normalized_text = "\n".join(lines)
    blocks: List[str] = []
    for raw_block in re.split(r"\n\s*\n+", normalized_text):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        lines = [line for line in lines if not _is_simple_page_artifact_line(line)]
        if not lines:
            continue
        merged = _merge_wrapped_lines(lines)
        if merged:
            blocks.append(merged)
    return _merge_block_continuations(blocks)


def _is_simple_page_artifact_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return True
    if re.fullmatch(r"\d{1,4}\s*[/\-]\s*\d{1,4}", text):
        return True
    if re.fullmatch(r"[-–—]?\s*\d{1,4}\s*[-–—]?", text):
        return True
    if re.fullmatch(r"(?i)page\s+\d+(\s+of\s+\d+)?", text):
        return True
    return False


def _merge_block_continuations(blocks: List[str]) -> List[str]:
    if not blocks:
        return []

    merged_blocks: List[str] = [blocks[0]]
    for block in blocks[1:]:
        previous = merged_blocks[-1]
        if _should_merge_blocks(previous, block):
            merged_blocks[-1] = _merge_text_fragments(previous, block)
        else:
            merged_blocks.append(block)
    return merged_blocks


def _should_merge_blocks(previous: str, current: str) -> bool:
    left = previous.strip()
    right = current.strip()
    if not left or not right:
        return False
    if _looks_like_heading(right):
        return False
    if re.match(r"(?i)^(figure|fig\.|table)\s+\d+[:.]", right):
        return False
    if left.endswith("-"):
        return True
    if re.search(r"[.?!:;。？！：；]$", left):
        return False
    if re.match(r"^[a-z0-9(\[\"'“‘]", right):
        return True
    if re.match(r"(?i)^(and|or|but|because|which|that|where|when|with|for|to|of|in|on|by|as)\b", right):
        return True
    return False


def _merge_wrapped_lines(lines: List[str]) -> str:
    if not lines:
        return ""
    merged_lines: List[str] = [lines[0].strip()]
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_list_item_start(stripped):
            merged_lines.append(stripped)
        else:
            merged_lines[-1] = _merge_text_fragments(merged_lines[-1], stripped)
    return "\n".join(merged_lines).strip()


def _is_list_item_start(line: str) -> bool:
    return bool(re.match(r"^([\-*•]\s+|\d{1,2}[.)]\s+)", line))


def _merge_text_fragments(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if left.endswith("-") and re.match(r"^[A-Za-z\u4e00-\u9fff]", right):
        return left[:-1] + right
    if right[0] in ",.;:!?)]}%":
        return left + right
    return left + " " + right


def _looks_like_heading(block: str) -> bool:
    text = re.sub(r"\s+", " ", block).strip()
    if not text or len(text) > 160:
        return False
    if re.match(r"^#{1,6}\s+.+$", text):
        return True
    if re.search(r"[.?!。？！]$", text):
        return False
    if re.match(r"(?i)^https?://", text):
        return False
    if re.match(r"(?i)^(figure|fig\.|table)\s+\d+[:.]", text):
        return False
    if re.match(r"^\(?\d+(\.\d+){0,4}\)?[.)]?\s+[A-Za-z0-9\u4e00-\u9fff]", text):
        return True
    if re.match(r"(?i)^[ivxlcdm]{1,8}(?:-[A-Z])?[.)]?\s+[A-Za-z0-9\u4e00-\u9fff]", text):
        return True
    if re.match(r"(?i)^appendix\s+[a-z0-9ivxlcdm]+([.:)\s]|$)", text):
        return True
    if re.match(r"(?i)^(section|chapter|part)\s+[0-9a-zivxlcdm]+(?:\.\d+)*([.:)\s]|$)", text):
        return True

    lowered = text.lower().rstrip(":")
    if any(
        lowered == hint or lowered.startswith(f"{hint} ") or lowered.startswith(f"{hint}:")
        for hint in HEADING_HINTS
    ):
        return True

    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*|[\u4e00-\u9fff]+|\d+(?:\.\d+)*", text)
    if not words or len(words) > 14:
        return False

    alpha_words = [word for word in words if re.search(r"[A-Za-z\u4e00-\u9fff]", word)]
    if not alpha_words:
        return False
    if text == text.upper() and len(words) <= 12:
        return True

    titled = sum(1 for word in alpha_words if _is_title_like_word(word))
    lowercase = sum(1 for word in alpha_words if word[:1].islower())
    if titled >= max(2, int(len(alpha_words) * 0.6)) and lowercase <= max(1, len(alpha_words) // 3):
        return True
    return False


def _is_title_like_word(word: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", word):
        return True
    if word.isupper():
        return True
    return word[:1].isupper()


def _rewrite_section(
    *,
    section_title: str,
    source_text: str,
    model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    style: str,
    rewrite_response_format: str,
    append_missing_formulas: bool,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]], bool, Optional[str], str]:
    """Rewrite a section into storyteller style.
    
    Returns: (story_text, terms, formula_explanations, success, error)
    """
    text = source_text.strip()
    if not text:
        return "", [], [], False, None, ""
    if len(text) < 80:
        return text, [], [], False, None, ""

    prompt = _build_story_prompt(
        section_title=section_title,
        source_text=text,
        style=style,
        response_format=rewrite_response_format,
    )
    formulas = _extract_latex_expressions(text)

    used_model = model
    try:
        if model.startswith("models/"):
            rewritten = _call_gemini_llm(prompt=prompt, model=model)
        else:
            rewritten = _call_local_llm(
                prompt=prompt,
                model=model,
                ollama_base_url=ollama_base_url,
            )
    except Exception as exc:
        primary_failure = f"{type(exc).__name__}: {exc}"
        _errors = [f"primary ({model}): {primary_failure}"]
        _succeeded = False
        for spec in (fallback_chain or []):
            fb_model = spec.get("model", "")
            fb_provider = (spec.get("provider") or "").strip().lower()
            fb_ollama_url = spec.get("ollama_base_url") or ollama_base_url
            fb_minimax_base = spec.get("minimax_base_url") or minimax_base_url
            fb_minimax_token = spec.get("minimax_oauth_token") or minimax_oauth_token
            try:
                if fb_provider in {"minimax.io", "minimax", "minimax-portal"}:
                    rewritten = _call_minimax_portal_llm(
                        prompt=prompt,
                        model=fb_model,
                        oauth_token=fb_minimax_token,
                        base_url=fb_minimax_base,
                    )
                else:
                    rewritten = _call_local_llm(
                        prompt=prompt,
                        model=fb_model,
                        ollama_base_url=fb_ollama_url,
                    )
                used_model = fb_model
                fallback_note = (
                    f"primary rewrite model failed ({primary_failure}); "
                    f"used fallback {fb_provider}:{fb_model}"
                )
                _succeeded = True
                break
            except Exception as fb_exc:
                _errors.append(f"{fb_provider or 'ollama'}:{fb_model}: {type(fb_exc).__name__}: {fb_exc}")
        if not _succeeded:
            return text, [], [], False, "; ".join(_errors), ""
    else:
        fallback_note = None

    story_text, terms, formula_explanations = _parse_rewrite_response(
        rewritten=rewritten,
        fallback_text=text,
    )

    # Check for missing formulas
    missing_formula_note = None
    if formulas:
        missing = [formula for formula in formulas if formula not in story_text]
        if missing:
            if append_missing_formulas:
                story_text = story_text.rstrip() + "\n\n公式保留：\n" + "\n".join(missing)
            else:
                missing_formula_note = (
                    f"detected {len(missing)} source formulas not echoed literally; "
                    "auto-append disabled"
                )

    return story_text, terms, formula_explanations, True, _merge_notes(fallback_note, missing_formula_note), used_model


def _build_story_prompt(*, section_title: str, source_text: str, style: str, response_format: str) -> str:
    source_text = source_text.strip()
    style_key = _normalize_style(style)
    response_format = _normalize_rewrite_response_format(response_format)
    style_hint = STYLE_PROMPTS.get(style_key, STYLE_PROMPTS[DEFAULT_STYLE])
    base_prompt = f"""你是頂尖的論文說書人，請把論文段落改寫成「易懂、可信、具教學感」的繁體中文說明。

改寫風格：
{style_hint}

規則：
1. 保留原文技術重點，不要發明新實驗數據。
2. 保留所有數學式的 LaTeX 分隔符與內容，包含 $...$、$$...$$、\\(...\\)、\\[...\\]，不可改成 Unicode 偽公式。
3. 請優先說明「為什麼」：為什麼要這樣設計、為什麼這會有效、相比直覺做法差在哪裡。
4. 使用生活化類比輔助理解，類比必須貼合原意，不能偏離技術內容。
5. 若內容含有任何公式，必須同時做到三件事：
   - 逐一解釋變數與公式在做什麼（白話文字版）。
   - 提供至少一個具體數值代入例子，並算出可解讀的結果。
   - 解釋這個數字結果在實務上代表什麼意義。
6. 可使用 Markdown 結構強化可讀性（例如 `**重點**`、清單、短小標），但不要過度冗長。
7. 如果原文太破碎，先做最小整理再說明，但不要脫離原意。
8. 直接輸出改寫內容，不要任何開場白、對話語氣或像「好的/以下是改寫」這類前綴。

章節標題：
{section_title}

原文段落：
{source_text}

"""

    if response_format == "json":
        return base_prompt + """

請用以下 JSON 格式輸出（務必嚴格遵守 JSON 語法）：
{{
    "story_text": "改寫後的說書內容（Markdown 格式）",
    "terms": [
        {{"term": "術語1", "explanation": "白話解釋"}},
        {{"term": "術語2", "explanation": "白話解釋"}}
    ],
    "formula_explanations": [
        {{
            "formula": "原始 LaTeX 公式",
            "explanation": "白話解釋（變數代表什麼）",
            "numerical_example": "數值範例演示（帶入數字、計算過程、意義解讀）"
        }}
    ]
}}

請直接輸出 JSON："""

    return base_prompt + """
請直接輸出乾淨的 Markdown 正文。
不要輸出 JSON。
不要輸出 code fence。
不要輸出 ```markdown 或 ```json。
若有公式，請直接把公式自然保留在正文中，使用原本的 LaTeX 定界符。
若需要整理術語、變數意義、公式對照或數值示例，可直接在正文中使用 Markdown 表格呈現。
若使用 Markdown 表格，請確保欄位名稱清楚、單格內容不要過長，並避免輸出表格以外的結構化包裝。"""



def _normalize_style(style: Any) -> str:
    normalized = str(style or "").strip().lower()
    if normalized in STYLE_PROMPTS:
        return normalized
    return DEFAULT_STYLE


def _call_local_llm(*, prompt: str, model: str, ollama_base_url: str, timeout: int = 240) -> str:
    req = urllib.request.Request(
        f"{ollama_base_url}/api/generate",
        data=json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read())
    return str(payload.get("response", "")).strip()


def _call_gemini_llm(*, prompt: str, model: str, timeout: int = 240) -> str:
    gemini_api_key = _configure_gemini()
    if not gemini_api_key:
        raise RuntimeError("missing GOOGLE_API_KEY/GEMINI_API_KEY")

    gm = genai.GenerativeModel(model)
    try:
        response = gm.generate_content(prompt, request_options={"timeout": timeout})
    except TypeError:
        response = gm.generate_content(prompt)
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty rewrite content")
    return text


def _call_minimax_portal_llm(*, prompt: str, model: str, oauth_token: str, base_url: str, timeout: int = 240) -> str:
    token = str(oauth_token or "").strip()
    if not token:
        raise RuntimeError("missing MINIMAX_PORTAL_OAUTH_TOKEN")

    normalized_base = str(base_url or DEFAULT_MINIMAX_PORTAL_BASE_URL).rstrip("/")
    endpoint_candidates: List[str] = []
    if normalized_base.endswith("/v1"):
        endpoint_candidates.append(f"{normalized_base}/text/chatcompletion_v2")
        endpoint_candidates.append(f"{normalized_base}/text/chatcompletion_pro")
    else:
        endpoint_candidates.append(f"{normalized_base}/v1/text/chatcompletion_v2")
        endpoint_candidates.append(f"{normalized_base}/v1/text/chatcompletion_pro")

    request_payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    last_error: Optional[str] = None
    for endpoint in endpoint_candidates:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(request_payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read())
        except Exception as exc:
            last_error = f"{endpoint}: {type(exc).__name__}: {exc}"
            continue

        provider_error = _extract_provider_error(payload)
        if provider_error:
            last_error = f"{endpoint}: {provider_error}"
            continue

        text = _extract_text_from_chat_payload(payload)
        if text:
            return text
        last_error = f"{endpoint}: empty response content"

    raise RuntimeError(last_error or "minimax-portal call failed")


def _parse_rewrite_response(
    *,
    rewritten: str,
    fallback_text: str,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
    cleaned = _strip_thinking_block(rewritten).strip()
    if not cleaned:
        return fallback_text, [], []

    parsed = _try_parse_rewrite_payload(cleaned)
    if parsed is not None:
        story_text = str(parsed.get("story_text") or "").strip() or fallback_text
        terms = _normalize_dict_list(parsed.get("terms"))
        formula_explanations = _normalize_dict_list(parsed.get("formula_explanations"))
        return story_text, terms, formula_explanations

    extracted_story = _extract_json_string_field(cleaned, "story_text")
    if extracted_story:
        return extracted_story, [], []

    return cleaned or fallback_text, [], []


def _try_parse_rewrite_payload(text: str) -> Optional[Dict[str, Any]]:
    candidates = _rewrite_json_candidates(text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _rewrite_json_candidates(text: str) -> List[str]:
    stripped = text.strip()
    candidates: List[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        candidate = value.strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    _add(stripped)

    fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, flags=re.IGNORECASE)
    for block in fenced_blocks:
        _add(block)

    decoder = json.JSONDecoder()
    for start_index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, end_index = decoder.raw_decode(stripped[start_index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            _add(stripped[start_index : start_index + end_index])

    return candidates


def _extract_json_string_field(text: str, field_name: str) -> str:
    pattern = rf'"{re.escape(field_name)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return ""

    raw_value = match.group(1)
    try:
        decoded = json.loads(f'"{raw_value}"')
    except json.JSONDecodeError:
        decoded = raw_value.encode("utf-8", errors="ignore").decode("unicode_escape", errors="ignore")
    return str(decoded).strip()


def _normalize_dict_list(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append({str(key): str(val) for key, val in item.items()})
    return normalized


def _extract_text_from_chat_payload(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts = [
                        str(item.get("text", "")).strip()
                        for item in content
                        if isinstance(item, dict) and str(item.get("text", "")).strip()
                    ]
                    merged = "\n".join(parts).strip()
                    if merged:
                        return merged
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    for key in ("reply", "output_text", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _extract_provider_error(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    base_resp = payload.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code")
        status_msg = str(base_resp.get("status_msg") or "").strip()
        if status_code not in (None, 0, "0"):
            code_text = str(status_code)
            if status_msg:
                return f"provider error code={code_text}: {status_msg}"
            return f"provider error code={code_text}"

    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or "").strip()
        if code and message:
            return f"provider error {code}: {message}"
        if message:
            return f"provider error: {message}"

    return ""


def _strip_thinking_block(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return _strip_conversational_chatter(cleaned)


def _strip_conversational_chatter(text: str) -> str:
    lines = text.splitlines()
    chatter_patterns = (
        r"^(?:好的|好|沒問題|當然可以|當然|可以|以下(?:是|為)|以下提供|我將|我會|讓我們|先來|改寫如下|重寫如下|答案如下|說明如下|內容如下)\b",
        r"^(?:sure|certainly|absolutely|of course|here(?:'s| is)|below(?: is| are)|let'?s)\b",
    )
    leading_prefix = re.compile("|".join(f"(?:{pattern})" for pattern in chatter_patterns), flags=re.IGNORECASE)

    idx = 0
    while idx < len(lines):
        candidate = lines[idx].strip(" \t`#>*-")
        if not candidate:
            idx += 1
            continue
        if leading_prefix.match(candidate):
            idx += 1
            continue
        break

    cleaned = "\n".join(lines[idx:]).strip()
    cleaned = re.sub(
        r"^\s*(?:好的|好|沒問題|當然可以|當然|可以|以下(?:是|為)|以下提供|改寫如下|重寫如下|答案如下|說明如下|內容如下|sure|certainly|absolutely|of course|here(?:'s| is)|below(?: is| are))\s*[：:，,。\-\s]*",
        "",
        cleaned,
        count=1,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _extract_latex_expressions(text: str) -> List[str]:
    patterns = [
        r"\\\[(.*?)\\\]",
        r"\$\$(.*?)\$\$",
        r"\\\((.*?)\\\)",
        r"(?<!\$)\$([^\$\n]{1,300}?)\$(?!\$)",
    ]
    hits: List[Tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.DOTALL):
            expr = match.group(0).strip()
            if expr:
                hits.append((match.start(), expr))

    hits.sort(key=lambda item: item[0])
    unique: List[str] = []
    seen: set[str] = set()
    for _, expr in hits:
        if expr in seen:
            continue
        seen.add(expr)
        unique.append(expr)
    return unique


def _resolve_title(payload: Dict[str, Any], pdf_path: Path, sections: List[Dict[str, Any]]) -> str:
    candidate = payload.get("title") or payload.get("paper_title")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()

    if sections:
        first_title = str(sections[0].get("title", "")).strip()
        if first_title and first_title.lower() not in {"overview", "content"}:
            return first_title
    return pdf_path.stem


def _build_output_path(pdf_path: Path, payload: Dict[str, Any]) -> Path:
    custom_name = payload.get("output_filename") or payload.get("output_name")
    if isinstance(custom_name, str) and custom_name.strip():
        filename = custom_name.strip()
        if not filename.lower().endswith(".html"):
            filename = f"{filename}.html"
    else:
        filename = f"{_slugify(pdf_path.stem)}_storyteller.html"
    return STORYTELLERS_DIR / filename


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or "story"


def _build_story_html_document(
    *,
    title: str,
    pdf_path: Path,
    rendered_sections: List[Dict[str, Any]],
    model: str,
) -> str:
    toc_items: List[str] = []
    section_items: List[str] = []
    for section in rendered_sections:
        idx = section["index"]
        section_id = f"section-{idx}"
        safe_title = html.escape(str(section["title"]))
        toc_items.append(f'<li><a href="#{section_id}">Section {idx} - {safe_title}</a></li>')

        story_html = _text_to_html_blocks(str(section.get("story_text", "")))
        
        # Build terms table
        terms = section.get("terms", [])
        terms_html = ""
        if terms:
            term_rows = []
            for t in terms:
                term = html.escape(t.get("term", ""))
                explanation = html.escape(t.get("explanation", ""))
                term_rows.append(f"<tr><td><strong>{term}</strong></td><td>{explanation}</td></tr>")
            terms_html = f"""
        <div class="terms-box">
            <h3>📚 技術術語表</h3>
            <table class="term-table">
                <tr><th>術語</th><th>白話解釋</th></tr>
                {''.join(term_rows)}
            </table>
        </div>"""
        
        # Build formula explanations
        formula_expls = section.get("formula_explanations", [])
        formula_html = ""
        if formula_expls:
            formula_blocks = []
            for f in formula_expls:
                formula = html.escape(f.get("formula", ""))
                explanation = html.escape(f.get("explanation", ""))
                example = html.escape(f.get("numerical_example", ""))
                formula_blocks.append(f"""
            <div class="formula-box">
                <div class="formula-content">$${formula}$$</div>
                <div class="formula-explanation">
                    <strong>白話解釋：</strong>{explanation}
                </div>
                <div class="example-box">
                    <strong>📊 數值範例演示：</strong><br>
                    {example}
                </div>
            </div>""")
            formula_html = ''.join(formula_blocks)
        
        section_items.append(
            f"""
    <section id="{section_id}">
        <h2>Section {idx} - {safe_title}</h2>
        <div class="story-block">
            <h3>📖 故事化改寫</h3>
{story_html}
        </div>
        {formula_html}
        {terms_html}
    </section>"""
        )

    generated_at = datetime.now(timezone.utc).isoformat()
    safe_title = html.escape(title)
    safe_pdf = html.escape(str(pdf_path))
    safe_model = html.escape(model)
    toc_html = "\n".join(toc_items)
    sections_html = "\n".join(section_items)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title} - 說書人版</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
    <script>
    document.addEventListener("DOMContentLoaded", function() {{
      renderMathInElement(document.body, {{
        delimiters: [
          {{left: "$$", right: "$$", display: true}},
          {{left: "\\\\[", right: "\\\\]", display: true}},
          {{left: "$", right: "$", display: false}},
          {{left: "\\\\(", right: "\\\\)", display: false}}
        ],
        throwOnError: false
      }});
    }});
    </script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: "Noto Sans TC", "PingFang TC", "Segoe UI", sans-serif;
            line-height: 1.8;
            max-width: 940px;
            margin: 0 auto;
            padding: 24px;
            background: #fafafa;
            color: #1e293b;
        }}
        h1 {{
            color: #1a1a2e;
            border-bottom: 4px solid #3b82f6;
            padding-bottom: 12px;
            margin-bottom: 8px;
            font-size: 28px;
        }}
        .meta {{
            color: #64748b;
            font-size: 13px;
            margin-bottom: 24px;
        }}
        .toc {{
            background: #f1f5f9;
            padding: 16px 20px;
            border-radius: 10px;
            margin-bottom: 28px;
        }}
        .toc h3 {{
            margin: 0 0 10px;
            color: #1d4ed8;
        }}
        .toc ul {{
            margin: 0;
            padding-left: 20px;
        }}
        .toc li {{
            margin: 6px 0;
        }}
        .toc a {{
            color: #1d4ed8;
            text-decoration: none;
        }}
        h2 {{
            color: #1d4ed8;
            margin-top: 40px;
            font-size: 22px;
            border-left: 5px solid #3b82f6;
            padding-left: 12px;
        }}
        h3 {{
            color: #0369a1;
            margin-top: 24px;
            margin-bottom: 12px;
        }}
        .story-block {{
            background: #ffffff;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
            margin: 16px 0;
        }}
        p {{
            margin: 14px 0;
            text-align: justify;
        }}
        .formula-box {{
            background: #f8fafc;
            border-left: 4px solid #3b82f6;
            border-radius: 8px;
            padding: 16px;
            margin: 16px 0;
        }}
        .formula-content {{
            background: #ffffff;
            padding: 12px;
            border-radius: 6px;
            text-align: center;
            font-size: 1.1em;
            margin-bottom: 12px;
        }}
        .formula-explanation {{
            margin: 12px 0;
            line-height: 1.6;
        }}
        .example-box {{
            background: #ecfdf5;
            border-left: 4px solid #10b981;
            border-radius: 8px;
            padding: 12px 16px;
            margin-top: 12px;
        }}
        .terms-box {{
            background: #f0f9ff;
            border-radius: 12px;
            padding: 16px;
            margin: 16px 0;
        }}
        .terms-box h3 {{
            margin-top: 0;
            color: #0c4a6e;
        }}
        .term-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0;
        }}
        .term-table th, .term-table td {{
            border: 1px solid #e2e8f0;
            padding: 12px;
            text-align: left;
        }}
        .term-table th {{
            background: #f1f5f9;
            color: #1e293b;
        }}
        .term-table tr:nth-child(even) {{
            background: #fafafa;
        }}
    </style>
</head>
<body>
    <h1>📚 {safe_title}</h1>
    <div class="meta">
        <strong>說書人版本（v1.5 - 術語表 + 公式詳解）</strong><br>
        Source PDF: {safe_pdf}<br>
        Model: {safe_model}<br>
        Generated at (UTC): {html.escape(generated_at)}
    </div>
    <div class="toc">
        <h3>📋 目錄</h3>
        <ul>
            {toc_html}
        </ul>
    </div>
    {sections_html}
</body>
</html>"""


def _ensure_list_blank_lines(text: str) -> str:
    """Insert a blank line before markdown list items and table rows not already preceded by one.

    The Python ``markdown`` library (with ``tables`` + ``nl2br`` extensions)
    requires at least one blank line before a list block or GFM table when
    preceded by non-list/non-table text.  This pre-processor guarantees the
    blank line so that lists render as ``<ul>/<ol><li>`` and tables render as
    ``<table>`` instead of literal text with ``<br>`` separators.
    """
    lines = text.split("\n")
    result: List[str] = []
    list_re = re.compile(r"^[ \t]*(?:[-*+]|\d+\.)[ \t]+")
    table_re = re.compile(r"^[ \t]*\|")
    for line in lines:
        prev = result[-1] if result else ""
        is_list = bool(list_re.match(line))
        is_table = bool(table_re.match(line))
        prev_is_list = bool(list_re.match(prev))
        prev_is_table = bool(table_re.match(prev))
        if is_list or is_table:
            # Blank line needed before list/table if previous is non-blank, non-compatible
            if prev.strip() and not prev_is_list and not prev_is_table:
                result.append("")
        elif line.strip() and prev_is_table:
            # Table blocks need a closing blank line; without it the next
            # paragraph text is absorbed as an extra table row.
            result.append("")
        result.append(line)
    return "\n".join(result)


def _text_to_html_blocks(text: str) -> str:
    protected, formulas = _protect_latex(text)
    markdown_input = _inject_display_formula_blocks(protected, formulas)
    markdown_input = _ensure_list_blank_lines(markdown_input)
    rendered = markdown.markdown(
        markdown_input,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    restored = _restore_formula_placeholders(rendered, formulas)
    restored = re.sub(
        r"<p>\s*(<div class=\"formula\">.*?</div>)\s*</p>",
        r"\1",
        restored,
        flags=re.DOTALL,
    )
    return "\n".join(f"            {line}" for line in restored.splitlines())


def _inject_display_formula_blocks(text: str, formulas: List[str]) -> str:
    injected = text
    for idx, formula in enumerate(formulas):
        compact = re.sub(r"\s+", "", formula)
        if (compact.startswith("$$") and compact.endswith("$$")) or (
            compact.startswith("\\[") and compact.endswith("\\]")
        ):
            placeholder = f"{LATEX_PLACEHOLDER}{idx}X"
            injected = re.sub(
                rf"(?m)^[ \t]*{re.escape(placeholder)}[ \t]*$",
                f"\n<div class=\"formula\">{placeholder}</div>\n",
                injected,
            )
    return injected


def _protect_latex(text: str) -> Tuple[str, List[str]]:
    formulas: List[str] = []

    def _capture(match: re.Match[str]) -> str:
        formulas.append(match.group(0))
        return f"{LATEX_PLACEHOLDER}{len(formulas) - 1}X"

    protected = text
    protected = re.sub(r"\\\[(.*?)\\\]", _capture, protected, flags=re.DOTALL)
    protected = re.sub(r"\$\$(.*?)\$\$", _capture, protected, flags=re.DOTALL)
    protected = re.sub(r"\\\((.*?)\\\)", _capture, protected, flags=re.DOTALL)
    protected = re.sub(r"(?<!\$)\$([^\$\n]{1,300}?)\$(?!\$)", _capture, protected)
    return protected, formulas


def _restore_formula_placeholders(text: str, formulas: List[str]) -> str:
    restored = text
    for idx, formula in enumerate(formulas):
        escaped_formula = html.escape(formula)
        if _is_display_formula(formula):
            replacement = escaped_formula
        else:
            replacement = f"<span class=\"math inline\">{escaped_formula}</span>"
        restored = restored.replace(f"{LATEX_PLACEHOLDER}{idx}X", replacement)
    return restored


def _is_display_formula(formula: str) -> bool:
    compact = re.sub(r"\s+", "", formula)
    return (compact.startswith("$$") and compact.endswith("$$")) or (
        compact.startswith("\\[") and compact.endswith("\\]")
    )


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _normalize_rewrite_response_format(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"markdown", "json"}:
        return normalized
    return DEFAULT_REWRITE_RESPONSE_FORMAT


def _merge_notes(first: Optional[str], second: Optional[str]) -> Optional[str]:
    left = str(first or "").strip()
    right = str(second or "").strip()
    if left and right:
        return f"{left}; {right}"
    if left:
        return left
    if right:
        return right
    return None


def _normalize_rewrite_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"paragraph", "chunk"}:
        return mode
    return DEFAULT_REWRITE_MODE


def _split_section_into_rewrite_parts(
    *,
    section_title: str,
    source_text: str,
    max_chunk_chars: int,
    rewrite_mode: str,
) -> List[Dict[str, str]]:
    normalized_mode = _normalize_rewrite_mode(rewrite_mode)
    if normalized_mode == "paragraph":
        chunks = _paragraph_parts_for_rewrite(source_text, max_chunk_chars)
    else:
        chunks = _chunk_text_for_rewrite(source_text, max_chunk_chars)

    if not chunks:
        return [{"title": section_title, "source_text": source_text.strip()}]

    if len(chunks) == 1:
        return [{"title": section_title, "source_text": chunks[0]}]

    total = len(chunks)
    part_label = "paragraph" if normalized_mode == "paragraph" else "part"
    parts: List[Dict[str, str]] = []
    for idx, chunk in enumerate(chunks, start=1):
        parts.append(
            {
                "title": f"{section_title} ({part_label} {idx}/{total})",
                "source_text": chunk,
            }
        )
    return parts


def _paragraph_parts_for_rewrite(source_text: str, max_chunk_chars: int) -> List[str]:
    text = str(source_text or "").strip()
    if not text:
        return []

    max_chars = max(int(max_chunk_chars or DEFAULT_REWRITE_CHUNK_CHARS), 400)
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not raw_paragraphs:
        raw_paragraphs = [text]

    parts: List[str] = []
    for paragraph in raw_paragraphs:
        if len(paragraph) <= max_chars:
            parts.append(paragraph)
            continue
        parts.extend(_split_long_text(paragraph, max_chars))
    return [part for part in parts if part.strip()]


def _chunk_text_for_rewrite(source_text: str, max_chunk_chars: int) -> List[str]:
    text = str(source_text or "").strip()
    if not text:
        return []

    max_chars = max(int(max_chunk_chars or DEFAULT_REWRITE_CHUNK_CHARS), 400)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: List[str] = []
    current = ""

    def _append_piece(piece: str) -> None:
        nonlocal current
        candidate = piece.strip()
        if not candidate:
            return
        if not current:
            current = candidate
            return
        joined = f"{current}\n\n{candidate}"
        if len(joined) <= max_chars:
            current = joined
            return
        chunks.append(current)
        current = candidate

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            _append_piece(paragraph)
            continue
        for piece in _split_long_text(paragraph, max_chars):
            _append_piece(piece)

    if current:
        chunks.append(current)
    return chunks


def _split_long_text(text: str, max_chars: int) -> List[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    sentence_candidates = re.split(r"(?<=[。！？.!?])\s+", normalized)
    sentences = [s.strip() for s in sentence_candidates if s.strip()]
    if len(sentences) <= 1:
        sentences = [normalized]

    pieces: List[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            start = 0
            while start < len(sentence):
                part = sentence[start : start + max_chars].strip()
                if part:
                    pieces.append(part)
                start += max_chars
            continue

        if not current:
            current = sentence
            continue

        candidate = f"{current} {sentence}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = sentence

    if current:
        pieces.append(current)
    return pieces
