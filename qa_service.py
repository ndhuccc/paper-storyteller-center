#!/usr/bin/env python3
"""
Q&A 服務模組
職責：組裝上下文、建立 prompt、呼叫 LLM 生成回答。
"""

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import google.generativeai as genai
from dotenv import load_dotenv

QAResult = Tuple[str, List[Dict]]
STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"


def build_cli_prompt(question: str, context: str) -> str:
    """paper_center.py 使用的 prompt 風格。"""
    return f"""你是一個專業的論文說書人，擅長比較和解釋學術論文的內容。

根據以下論文內容，回答用戶的問題。請用繁體中文回答，並引用相關的論文標題。
若內容中有 paper_id / chunk_index / section 提示，請在回答最後補一行「來源」做簡短標註：

=== 論文內容 ===
{context}

=== 用戶問題 ===
{question}

回答："""


def build_gui_prompt(question: str, context: str) -> str:
    """paper_center_gui.py 使用的 prompt 風格。"""
    return f"""你是專業論文說書人，用繁體中文回答，並引用論文標題。
若內容中有 paper_id / chunk_index / section 提示，請在回答最後補一行「來源」做簡短標註。

=== 論文內容 ===
{context}

=== 問題 ===
{question}

回答："""


def _compact_text(text: Any, max_len: int = 220) -> str:
    raw = " ".join(str(text or "").split()).strip()
    if not raw:
        return ""
    if len(raw) <= max_len:
        return raw
    return f"{raw[: max_len - 3]}..."


def _safe_chunk_index(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _extract_section_label(result: Dict[str, Any]) -> str:
    for key in ("section", "section_title", "section_name", "heading"):
        label = str(result.get(key, "")).strip()
        if label:
            return label
    return ""


def _build_citation(result: Dict[str, Any], rank: int) -> Dict[str, Any]:
    paper_id = str(result.get("paper_id", result.get("id", ""))).strip()
    chunk_index = _safe_chunk_index(result.get("chunk_index"))
    section = _extract_section_label(result)
    snippet = _compact_text(
        result.get("chunk_text") or result.get("content", ""),
        max_len=180,
    )
    distance = result.get("_distance")
    similarity: Optional[float] = None
    if isinstance(distance, (int, float)):
        similarity = 1.0 - float(distance)

    return {
        "rank": rank,
        "paper_id": paper_id,
        "title": str(result.get("title", "未知")).strip() or "未知",
        "chunk_index": chunk_index,
        "section": section,
        "chunk_snippet": snippet,
        "similarity": similarity,
    }


def enrich_results_with_citations(results: List[Dict]) -> List[Dict]:
    """回傳原 results 的相容增強版，每筆附上 citation metadata。"""
    enriched: List[Dict] = []
    for idx, result in enumerate(results, start=1):
        source = dict(result)
        citation = _build_citation(source, rank=idx)
        source["citation"] = citation

        # 保持舊流程可用：沒有 id 時，用 paper_id 做保底
        source_id = str(source.get("id", "")).strip()
        if not source_id and citation.get("paper_id"):
            source["id"] = citation["paper_id"]

        enriched.append(source)
    return enriched


def build_context(results: List[Dict], content_limit: int) -> str:
    """把論文結果組成上下文，附帶來源定位資訊（paper/chunk/section）。"""
    context_parts: List[str] = []
    for idx, result in enumerate(results, start=1):
        citation = _build_citation(result, rank=idx)
        lines = [f"=== {citation['title']} ===", f"paper_id: {citation['paper_id'] or '-'}"]
        if citation.get("chunk_index") is not None:
            lines.append(f"chunk_index: {citation['chunk_index']}")
        if citation.get("section"):
            lines.append(f"section: {citation['section']}")
        if citation.get("chunk_snippet"):
            lines.append(f"chunk_snippet: {citation['chunk_snippet']}")
        context_body = str(result.get("content") or result.get("chunk_text") or "")
        lines.append(context_body[:content_limit])
        context_parts.append("\n".join(lines))
    return "\n\n".join(context_parts)


def generate_answer(
    *,
    prompt: str,
    model: str,
    ollama_base_url: str,
    timeout: int = 180,
) -> str:
    """依模型提供者呼叫 Ollama 或 Gemini 生成回答。"""
    if _is_gemini_model(model):
        return _generate_answer_with_gemini(prompt=prompt, model=model, timeout=timeout)

    req = urllib.request.Request(
        f"{ollama_base_url}/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read()).get("response", "").strip()


def _is_gemini_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return normalized.startswith("models/") or normalized.startswith("gemini")


def _configure_gemini() -> str:
    load_dotenv(dotenv_path=STORYTELLERS_DIR / ".env", override=False)
    load_dotenv(dotenv_path=Path.home() / ".env", override=False)
    gemini_api_key = str(os.getenv("GEMINI_API_KEY") or "").strip()
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
    return gemini_api_key


def _generate_answer_with_gemini(*, prompt: str, model: str, timeout: int) -> str:
    gemini_api_key = _configure_gemini()
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not found for Gemini answer generation")

    llm = genai.GenerativeModel(model)
    try:
        response = llm.generate_content(prompt, request_options={"timeout": timeout})
    except TypeError:
        response = llm.generate_content(prompt)

    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty answer content")
    return text


def answer_with_results(
    *,
    question: str,
    results: List[Dict],
    model: str,
    prompt_builder: Callable[[str, str], str],
    context_content_limit: int,
    not_found_message: str,
    error_prefix: str,
    ollama_base_url: str,
) -> QAResult:
    """以已取得的論文結果產生回答。"""
    if not results:
        return not_found_message, []

    enriched_results = enrich_results_with_citations(results)
    context = build_context(enriched_results, context_content_limit)
    prompt = prompt_builder(question, context)
    try:
        answer = generate_answer(
            prompt=prompt,
            model=model,
            ollama_base_url=ollama_base_url,
        )
        return answer, enriched_results
    except Exception as e:
        return f"{error_prefix}{e}", enriched_results


def answer_with_search(
    *,
    question: str,
    search_fn: Callable[[str, int], List[Dict]],
    top_k: int,
    model: str,
    prompt_builder: Callable[[str, str], str],
    context_content_limit: int,
    not_found_message: str,
    error_prefix: str,
    ollama_base_url: str,
    forced_papers: Optional[List[Dict]] = None,
) -> QAResult:
    """先搜尋（或使用指定論文），再產生回答。"""
    if forced_papers:
        results = forced_papers
    else:
        results = search_fn(question, top_k)

    return answer_with_results(
        question=question,
        results=results,
        model=model,
        prompt_builder=prompt_builder,
        context_content_limit=context_content_limit,
        not_found_message=not_found_message,
        error_prefix=error_prefix,
        ollama_base_url=ollama_base_url,
    )
