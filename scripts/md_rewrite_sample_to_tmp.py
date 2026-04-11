#!/usr/bin/env python3
"""Rewrite sample.md with a pipeline style and write HTML under tmp/.

Default style: env ``SAMPLE_MD_STYLE`` if set and valid, else the pipeline's first style key.
Output: one combined file ``tmp/{style}_sample.html`` (e.g. ``tmp/blog_sample.html``).

Environment:
  SAMPLE_MD_STYLE=blog       Default style when ``--style`` is omitted.
  SAMPLE_MD_MAX_SECTIONS=N  Process only the first N sections (smoke test).
  SAMPLE_MD_OFFLINE=1       Skip LLM; story column shows a short notice + original text (no API keys).

CLI:
  --style STYLE              storyteller, blog, professor, …
  --per-section              Also write tmp/{style}_NN_slug.html per === chunk.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Repo root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storyteller_pipeline import (  # noqa: E402
    DEFAULT_ANTI_REPEAT_LEVEL,
    DEFAULT_APPEND_MISSING_FORMULAS,
    DEFAULT_CONCISE_LEVEL,
    DEFAULT_GEMINI_PREFLIGHT_ENABLED,
    DEFAULT_GEMINI_PREFLIGHT_TIMEOUT_SECONDS,
    DEFAULT_GEMINI_REWRITE_TIMEOUT_SECONDS,
    DEFAULT_MINIMAX_PORTAL_BASE_URL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_REWRITE_FALLBACK_CHAIN,
    DEFAULT_REWRITE_FALLBACK_TIMEOUT_SECONDS,
    DEFAULT_REWRITE_MODEL,
    DEFAULT_REWRITE_RESPONSE_FORMAT,
    STYLE_PROMPTS,
    _build_story_html_document,
    _post_rewrite_blog_audit,
    _post_rewrite_storyteller_audit,
    _rewrite_section,
    _slugify,
)


def _first_style_key() -> str:
    return next(iter(STYLE_PROMPTS.keys()))


def _default_style_from_env() -> str:
    s = os.getenv("SAMPLE_MD_STYLE", "").strip().lower()
    if s in STYLE_PROMPTS:
        return s
    return _first_style_key()


def _parse_sections(md: str) -> list[tuple[str, str]]:
    """Split on standalone === lines; each chunk: first line = title, rest = body."""
    parts = re.split(r"(?m)^===\s*$", md.strip())
    out: list[tuple[str, str]] = []
    for raw in parts:
        chunk = raw.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        title = lines[0].strip() if lines else "Section"
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        if not body:
            body = title
            title = "Preface"
        out.append((title, body))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Rewrite sample.md to tmp/ HTML.")
    ap.add_argument(
        "--style",
        choices=list(STYLE_PROMPTS.keys()),
        default=_default_style_from_env(),
        help="Rewrite style (default: SAMPLE_MD_STYLE or pipeline first style).",
    )
    ap.add_argument(
        "--per-section",
        action="store_true",
        help="Also emit one HTML per === section (tmp/{style}_NN_*.html).",
    )
    args = ap.parse_args()

    style = args.style
    md_path = ROOT / "sample.md"
    out_dir = ROOT / "tmp"
    out_dir.mkdir(parents=True, exist_ok=True)

    text = md_path.read_text(encoding="utf-8")
    sections_raw = _parse_sections(text)
    max_sec = os.getenv("SAMPLE_MD_MAX_SECTIONS", "").strip()
    if max_sec.isdigit():
        sections_raw = sections_raw[: int(max_sec)]
    if not sections_raw:
        print("No sections found in sample.md", file=sys.stderr)
        return 1

    model = os.getenv("STORYTELLER_REWRITE_MODEL", DEFAULT_REWRITE_MODEL)
    ollama_base = (
        os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_BASE_URL
    )
    minimax_base = os.getenv("MINIMAX_PORTAL_BASE_URL", DEFAULT_MINIMAX_PORTAL_BASE_URL)
    minimax_token = (
        os.getenv("MINIMAX_PORTAL_OAUTH_TOKEN", "").strip()
        or os.getenv("MINIMAX_OAUTH_TOKEN", "").strip()
    )

    introduced: list[str] = []
    rendered: list[dict] = []
    used_models: list[str] = []
    offline = os.getenv("SAMPLE_MD_OFFLINE", "").strip().lower() in ("1", "true", "yes")

    for idx, (title, body) in enumerate(sections_raw, start=1):
        if offline:
            story_text = (
                "> **離線模式（`SAMPLE_MD_OFFLINE=1`）**：未呼叫語言模型；下方為原文預覽。\n\n"
                + body
            )
            terms, formula_expls, ok, err_note, used_model = [], [], True, None, "offline"
        else:
            story_text, terms, formula_expls, ok, err_note, used_model = _rewrite_section(
                section_title=title,
                source_text=body,
                model=model,
                fallback_chain=DEFAULT_REWRITE_FALLBACK_CHAIN,
                ollama_base_url=ollama_base,
                minimax_base_url=minimax_base,
                minimax_oauth_token=minimax_token,
                style=style,
                rewrite_response_format=DEFAULT_REWRITE_RESPONSE_FORMAT,
                append_missing_formulas=DEFAULT_APPEND_MISSING_FORMULAS,
                style_params=None,
                section_index=idx,
                section_count=len(sections_raw),
                introduced_concepts=introduced,
            )
        used_models.append(used_model or model)
        if err_note:
            story_text = (
                f"{story_text}\n\n<!-- rewrite note: {err_note} -->"
                if story_text
                else f"<!-- rewrite failed: {err_note} -->\n\n{body}"
            )
        print(f"[{idx}/{len(sections_raw)}] rewrite ok={ok} title={title[:60]!r}", flush=True)
        rendered.append(
            {
                "index": idx,
                "title": title,
                "source_text": body,
                "story_text": story_text or body,
                "terms": terms,
                "formula_explanations": formula_expls,
            }
        )

    if not offline and rendered:
        audit_notes: list[str] = []
        if style == "storyteller":
            print("post_rewrite_storyteller_audit…", flush=True)
            _post_rewrite_storyteller_audit(
                rendered,
                primary_model=model,
                fallback_chain=DEFAULT_REWRITE_FALLBACK_CHAIN,
                ollama_base_url=ollama_base,
                minimax_base_url=minimax_base,
                minimax_oauth_token=minimax_token,
                rewrite_response_format=DEFAULT_REWRITE_RESPONSE_FORMAT,
                append_missing_formulas=DEFAULT_APPEND_MISSING_FORMULAS,
                style_params={},
                concise_level=DEFAULT_CONCISE_LEVEL,
                anti_repeat_level=DEFAULT_ANTI_REPEAT_LEVEL,
                gemini_preflight_enabled=DEFAULT_GEMINI_PREFLIGHT_ENABLED,
                gemini_preflight_timeout_seconds=DEFAULT_GEMINI_PREFLIGHT_TIMEOUT_SECONDS,
                gemini_rewrite_timeout_seconds=DEFAULT_GEMINI_REWRITE_TIMEOUT_SECONDS,
                fallback_timeout_seconds=DEFAULT_REWRITE_FALLBACK_TIMEOUT_SECONDS,
                llm_failures=audit_notes,
            )
        elif style == "blog":
            print("post_rewrite_blog_audit…", flush=True)
            _post_rewrite_blog_audit(
                rendered,
                primary_model=model,
                fallback_chain=DEFAULT_REWRITE_FALLBACK_CHAIN,
                ollama_base_url=ollama_base,
                minimax_base_url=minimax_base,
                minimax_oauth_token=minimax_token,
                rewrite_response_format=DEFAULT_REWRITE_RESPONSE_FORMAT,
                append_missing_formulas=DEFAULT_APPEND_MISSING_FORMULAS,
                style_params={},
                concise_level=DEFAULT_CONCISE_LEVEL,
                anti_repeat_level=DEFAULT_ANTI_REPEAT_LEVEL,
                gemini_preflight_enabled=DEFAULT_GEMINI_PREFLIGHT_ENABLED,
                gemini_preflight_timeout_seconds=DEFAULT_GEMINI_PREFLIGHT_TIMEOUT_SECONDS,
                gemini_rewrite_timeout_seconds=DEFAULT_GEMINI_REWRITE_TIMEOUT_SECONDS,
                fallback_timeout_seconds=DEFAULT_REWRITE_FALLBACK_TIMEOUT_SECONDS,
                llm_failures=audit_notes,
            )
        for line in audit_notes:
            print(f"[audit] {line}", flush=True)

    # One combined HTML (full sample, one file — easy to open)
    combined_model = used_models[-1] if used_models else model
    doc = _build_story_html_document(
        title="sample.md 改寫測試",
        pdf_path=md_path,
        rendered_sections=rendered,
        model=combined_model,
        style=style,
    )
    combined_path = out_dir / f"{style}_sample.html"
    combined_path.write_text(doc, encoding="utf-8")
    print(f"Wrote {combined_path}")

    if args.per_section:
        for row in rendered:
            idx = row["index"]
            title = str(row["title"])
            slug = _slugify(re.sub(r"^#+\s*", "", title))[:48] or f"sec{idx}"
            single_doc = _build_story_html_document(
                title=f"{title} (sample.md §{idx})",
                pdf_path=md_path,
                rendered_sections=[row],
                model=combined_model,
                style=style,
            )
            path = out_dir / f"{style}_{idx:02d}_{slug}.html"
            path.write_text(single_doc, encoding="utf-8")
            print(f"Wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
