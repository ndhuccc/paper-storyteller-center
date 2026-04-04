#!/usr/bin/env python3
"""Minimal storyteller generation pipeline for one PDF -> one HTML output."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
DEFAULT_MODEL = "deepseek-r1:8b"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MAX_SECTIONS = 10
LATEX_PLACEHOLDER = "LATEXPH"
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
)


def run_storyteller_pipeline(job: Dict[str, Any]) -> Dict[str, Any]:
    """Run one minimal storyteller generation job end-to-end."""
    payload = job.get("payload", {}) if isinstance(job, dict) else {}
    if not isinstance(payload, dict):
        payload = {}

    pdf_path = _resolve_pdf_path(job=job, payload=payload)
    if pdf_path is None:
        raise ValueError(
            "No readable PDF path found in job payload. "
            "Supported keys include pdf_path/source_pdf_path/input_path/file_path/path/pdf."
        )

    extracted_text = _extract_pdf_text(pdf_path)
    if not extracted_text.strip():
        raise RuntimeError(f"No text extracted from PDF: {pdf_path}")

    sections = _split_into_sections(extracted_text)
    if not sections:
        raise RuntimeError(f"Unable to build sections from extracted text: {pdf_path}")

    max_sections = _safe_positive_int(payload.get("max_sections"), DEFAULT_MAX_SECTIONS)
    model = str(payload.get("model") or DEFAULT_MODEL)
    ollama_base_url = str(payload.get("ollama_base_url") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")

    rendered_sections: List[Dict[str, Any]] = []
    llm_failures: List[str] = []

    for index, section in enumerate(sections[:max_sections], start=1):
        rewritten_text, used_llm, failure = _rewrite_section(
            section_title=section["title"],
            source_text=section["source_text"],
            model=model,
            ollama_base_url=ollama_base_url,
        )
        if failure:
            llm_failures.append(f"section {index}: {failure}")
        rendered_sections.append(
            {
                "index": index,
                "title": section["title"],
                "source_text": section["source_text"],
                "story_text": rewritten_text,
                "used_llm": used_llm,
            }
        )

    title = _resolve_title(payload=payload, pdf_path=pdf_path, sections=rendered_sections)
    output_path = _build_output_path(pdf_path=pdf_path, payload=payload)
    output_html = _build_story_html_document(
        title=title,
        pdf_path=pdf_path,
        rendered_sections=rendered_sections,
        model=model,
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
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections_generated": len(rendered_sections),
        "steps": [
            {"name": "ingest_source", "status": "done", "note": str(pdf_path)},
            {
                "name": "pdf_to_structured_content",
                "status": "done",
                "note": f"{len(sections)} detected sections",
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
                "style": "storyteller",
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


def _extract_pdf_text(pdf_path: Path) -> str:
    if shutil.which("pdftotext") is None:
        raise RuntimeError("pdftotext is required but was not found in PATH")

    commands = [
        ["pdftotext", "-layout", "-nopgbrk", "-enc", "UTF-8", str(pdf_path), "-"],
        ["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"],
    ]

    last_error = ""
    for cmd in commands:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        output = proc.stdout or ""
        if proc.returncode == 0 and output.strip():
            return _normalize_extracted_text(output)
        last_error = (proc.stderr or "").strip() or f"exit_code={proc.returncode}"

    raise RuntimeError(f"pdftotext extraction failed for {pdf_path}: {last_error}")


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

    compact = "\n".join(cleaned_lines)
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    return compact.strip()


def _split_into_sections(extracted_text: str) -> List[Dict[str, str]]:
    blocks = _split_blocks(extracted_text)
    if not blocks:
        return []

    sections: List[Dict[str, str]] = []
    current_title = "Overview"
    current_paragraphs: List[str] = []

    for block in blocks:
        if _looks_like_heading(block):
            if current_paragraphs:
                sections.append(
                    {
                        "title": current_title,
                        "source_text": "\n\n".join(current_paragraphs).strip(),
                    }
                )
                current_paragraphs = []
            current_title = block
            continue
        current_paragraphs.append(block)

    if current_paragraphs:
        sections.append(
            {
                "title": current_title,
                "source_text": "\n\n".join(current_paragraphs).strip(),
            }
        )

    if not sections:
        return [{"title": "Content", "source_text": "\n\n".join(blocks)}]
    return sections


def _split_blocks(extracted_text: str) -> List[str]:
    blocks: List[str] = []
    for raw_block in re.split(r"\n\s*\n+", extracted_text):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        merged = _merge_wrapped_lines(lines)
        if merged:
            blocks.append(merged)
    return blocks


def _merge_wrapped_lines(lines: List[str]) -> str:
    if not lines:
        return ""
    merged = lines[0]
    for line in lines[1:]:
        if merged.endswith("-") and line and line[0].islower():
            merged = merged[:-1] + line
        else:
            merged = merged + " " + line
    return merged.strip()


def _looks_like_heading(block: str) -> bool:
    text = re.sub(r"\s+", " ", block).strip()
    if not text or len(text) > 120:
        return False
    if re.search(r"[.?!:;。？！：；]$", text):
        return False
    if re.match(r"^\d+(\.\d+){0,3}\s+[A-Za-z0-9\u4e00-\u9fff]", text):
        return True
    if re.match(r"^(section|chapter)\s+[0-9ivx]+", text, flags=re.IGNORECASE):
        return True

    lowered = text.lower()
    if any(lowered.startswith(hint) for hint in HEADING_HINTS):
        return True

    words = text.split()
    if words and len(words) <= 12 and text == text.upper():
        return True
    return False


def _rewrite_section(
    *,
    section_title: str,
    source_text: str,
    model: str,
    ollama_base_url: str,
) -> Tuple[str, bool, Optional[str]]:
    text = source_text.strip()
    if not text:
        return "", False, None
    if len(text) < 80:
        return text, False, None

    prompt = _build_story_prompt(section_title=section_title, source_text=text)
    formulas = _extract_latex_expressions(text)

    try:
        rewritten = _call_local_llm(
            prompt=prompt,
            model=model,
            ollama_base_url=ollama_base_url,
        )
    except Exception as exc:
        return text, False, f"{type(exc).__name__}: {exc}"

    cleaned = _strip_thinking_block(rewritten).strip() or text
    if formulas:
        missing = [formula for formula in formulas if formula not in cleaned]
        if missing:
            cleaned = cleaned.rstrip() + "\n\n公式保留：\n" + "\n".join(missing)

    return cleaned, True, None


def _build_story_prompt(*, section_title: str, source_text: str) -> str:
    clipped = source_text[:3000]
    return f"""你是專業論文說書人，請把論文段落改寫成易懂的繁體中文敘事說明。

規則：
1. 保留原文技術重點，不要發明新實驗數據。
2. 保留所有數學式的 LaTeX 分隔符與內容，包含 $...$、$$...$$、\\(...\\)、\\[...\\]，不可改成 Unicode 偽公式。
3. 輸出 2-4 個短段落，不要使用條列或 markdown 標題。
4. 如果原文太破碎，先做最小整理再說明，但不要脫離原意。

章節標題：
{section_title}

原文段落：
{clipped}

請直接輸出改寫結果："""


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


def _strip_thinking_block(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


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

        story_html = _text_to_html_blocks(str(section["story_text"]))
        section_items.append(
            f"""
    <section id="{section_id}">
        <h2>Section {idx} - {safe_title}</h2>
        <div class="story-block">
{story_html}
        </div>
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
    <script>
    window.MathJax = {{
      tex: {{
        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
        processEscapes: true
      }},
      options: {{
        skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
      }}
    }};
    </script>
    <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: "Noto Sans TC", "PingFang TC", "Segoe UI", sans-serif;
            line-height: 1.8;
            max-width: 940px;
            margin: 0 auto;
            padding: 24px;
            background: #f8fafc;
            color: #1e293b;
        }}
        h1 {{
            color: #1e3a8a;
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
            margin-top: 36px;
            font-size: 22px;
            border-left: 5px solid #3b82f6;
            padding-left: 12px;
        }}
        .story-block {{
            background: #ffffff;
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
        }}
        p {{
            margin: 14px 0;
            text-align: justify;
        }}
        .formula {{
            overflow-x: auto;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 12px;
            margin: 12px 0;
            text-align: center;
        }}
    </style>
</head>
<body>
    <h1>📚 {safe_title}</h1>
    <div class="meta">
        <strong>說書人版本（Patch 6A MVP）</strong><br>
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


def _text_to_html_blocks(text: str) -> str:
    protected, formulas = _protect_latex(text)
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", protected) if block.strip()]

    html_blocks: List[str] = []
    for block in blocks:
        escaped = html.escape(block).replace("\n", "<br>")
        restored = _restore_formula_placeholders(escaped, formulas)
        compact = re.sub(r"\s+", "", restored)
        if (compact.startswith("$$") and compact.endswith("$$")) or (
            compact.startswith("\\[") and compact.endswith("\\]")
        ):
            html_blocks.append(f'            <div class="formula">{restored}</div>')
        else:
            html_blocks.append(f"            <p>{restored}</p>")
    return "\n".join(html_blocks)


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
        restored = restored.replace(f"{LATEX_PLACEHOLDER}{idx}X", formula)
    return restored


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed
