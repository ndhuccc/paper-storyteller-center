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

from dotenv import load_dotenv

QAResult = Tuple[str, List[Dict]]
QADetailResult = Dict[str, Any]
PROJECT_DIR = Path(__file__).resolve().parent
STORYTELLERS_DIR = PROJECT_DIR / "htmls"


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


def generate_answer_with_metadata(
    *,
    prompt: str,
    model: str,
    provider: str = "",
    ollama_base_url: str,
    minimax_base_url: str = "",
    minimax_oauth_token: str = "",
    timeout: int = 180,
    fallbacks: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, str]:
    """依序嘗試主模型與備案清單，回傳實際成功的模型資訊。"""
    _errors: List[str] = []
    try:
        text = _call_single_model(
            prompt=prompt,
            model=model,
            provider=provider,
            ollama_base_url=ollama_base_url,
            minimax_base_url=minimax_base_url,
            minimax_oauth_token=minimax_oauth_token,
            timeout=timeout,
        )
        return {
            "text": text,
            "model": str(model or "").strip(),
            "provider": str(provider or ("gemini" if _is_gemini_model(model) else "ollama")).strip(),
        }
    except Exception as primary_exc:
        _errors.append(f"primary ({model}): {primary_exc}")

    for spec in (fallbacks or []):
        fb_model = spec.get("model", "")
        fb_provider = spec.get("provider", "")
        fb_ollama_url = spec.get("ollama_base_url") or ollama_base_url
        fb_minimax_base = spec.get("minimax_base_url", "")
        fb_minimax_token = spec.get("minimax_oauth_token", "")
        try:
            text = _call_single_model(
                prompt=prompt,
                model=fb_model,
                provider=fb_provider,
                ollama_base_url=fb_ollama_url,
                minimax_base_url=fb_minimax_base,
                minimax_oauth_token=fb_minimax_token,
                timeout=timeout,
            )
            return {
                "text": text,
                "model": str(fb_model or "").strip(),
                "provider": str(fb_provider or "ollama").strip() or "ollama",
            }
        except Exception as fb_exc:
            _errors.append(f"{fb_provider or 'ollama'}:{fb_model}: {fb_exc}")

    raise RuntimeError("; ".join(_errors))


def generate_answer(
    *,
    prompt: str,
    model: str,
    provider: str = "",
    ollama_base_url: str,
    minimax_base_url: str = "",
    minimax_oauth_token: str = "",
    timeout: int = 180,
    fallbacks: Optional[List[Dict[str, str]]] = None,
) -> str:
    """依序嘗試主模型與備案清單，全部失敗才拋出例外。"""
    return generate_answer_with_metadata(
        prompt=prompt,
        model=model,
        provider=provider,
        ollama_base_url=ollama_base_url,
        minimax_base_url=minimax_base_url,
        minimax_oauth_token=minimax_oauth_token,
        timeout=timeout,
        fallbacks=fallbacks,
    ).get("text", "")


def _call_single_model(
    *,
    prompt: str,
    model: str,
    provider: str = "",
    ollama_base_url: str,
    minimax_base_url: str = "",
    minimax_oauth_token: str = "",
    timeout: int = 180,
) -> str:
    """單次呼叫一個模型（Gemini、MiniMax 或 Ollama）。"""
    if _is_gemini_model(model):
        return _generate_answer_with_gemini(prompt=prompt, model=model, timeout=timeout)
    p = (provider or "").strip().lower()
    if p in {"minimax.io", "minimax", "minimax-portal"}:
        return _call_minimax_qa_llm(
            prompt=prompt,
            model=model,
            oauth_token=minimax_oauth_token,
            base_url=minimax_base_url or "https://api.minimax.io",
            timeout=timeout,
        )
    req = urllib.request.Request(
        f"{ollama_base_url}/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read()).get("response", "").strip()


def _call_minimax_qa_llm(
    *, prompt: str, model: str, oauth_token: str, base_url: str, timeout: int = 180
) -> str:
    """呼叫 MiniMax Portal chatcompletion API 生成 QA 回答。"""
    token = str(oauth_token or "").strip()
    if not token:
        load_dotenv(dotenv_path=STORYTELLERS_DIR / ".env", override=False)
        load_dotenv(dotenv_path=Path.home() / ".env", override=False)
        token = str(
            os.getenv("MINIMAX_PORTAL_OAUTH_TOKEN") or os.getenv("MINIMAX_OAUTH_TOKEN") or ""
        ).strip()
    if not token:
        raise RuntimeError("missing MINIMAX_PORTAL_OAUTH_TOKEN for MiniMax QA")

    normalized_base = str(base_url or "https://api.minimax.io").rstrip("/")
    endpoint = (
        f"{normalized_base}/text/chatcompletion_v2"
        if normalized_base.endswith("/v1")
        else f"{normalized_base}/v1/text/chatcompletion_v2"
    )
    req = urllib.request.Request(
        endpoint,
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read())
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        text = payload.get("reply", "")
    text = str(text or "").strip()
    if not text:
        raise RuntimeError(f"MiniMax QA returned empty content")
    return text


def _is_gemini_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return normalized.startswith("models/") or normalized.startswith("gemini")


def _configure_gemini() -> str:
    from gemini_keys import pick_working_gemini_api_key

    return pick_working_gemini_api_key()


def _generate_answer_with_gemini(*, prompt: str, model: str, timeout: int) -> str:
    from gemini_client import generate_content_text

    gemini_api_key = _configure_gemini()
    if not gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY not found for Gemini answer generation")

    return generate_content_text(
        api_key=gemini_api_key,
        model=model,
        contents=prompt,
        timeout=int(timeout),
    )


def answer_with_results(
    *,
    question: str,
    results: List[Dict],
    model: str,
    provider: str = "",
    prompt_builder: Callable[[str, str], str],
    context_content_limit: int,
    not_found_message: str,
    error_prefix: str,
    ollama_base_url: str,
    minimax_base_url: str = "",
    minimax_oauth_token: str = "",
    fallbacks: Optional[List[Dict[str, str]]] = None,
) -> QAResult:
    """以已取得的論文結果產生回答。"""
    detail = answer_with_results_detailed(
        question=question,
        results=results,
        model=model,
        provider=provider,
        prompt_builder=prompt_builder,
        context_content_limit=context_content_limit,
        not_found_message=not_found_message,
        error_prefix=error_prefix,
        ollama_base_url=ollama_base_url,
        minimax_base_url=minimax_base_url,
        minimax_oauth_token=minimax_oauth_token,
        fallbacks=fallbacks,
    )
    return str(detail.get("answer", "")), detail.get("sources", [])


def answer_with_results_detailed(
    *,
    question: str,
    results: List[Dict],
    model: str,
    provider: str = "",
    prompt_builder: Callable[[str, str], str],
    context_content_limit: int,
    not_found_message: str,
    error_prefix: str,
    ollama_base_url: str,
    minimax_base_url: str = "",
    minimax_oauth_token: str = "",
    fallbacks: Optional[List[Dict[str, str]]] = None,
) -> QADetailResult:
    """以已取得的論文結果產生回答，附帶實際使用模型資訊。"""
    if not results:
        return {"answer": not_found_message, "sources": [], "used_model": "", "used_provider": ""}

    enriched_results = enrich_results_with_citations(results)
    context = build_context(enriched_results, context_content_limit)
    prompt = prompt_builder(question, context)
    try:
        answer_meta = generate_answer_with_metadata(
            prompt=prompt,
            model=model,
            provider=provider,
            ollama_base_url=ollama_base_url,
            minimax_base_url=minimax_base_url,
            minimax_oauth_token=minimax_oauth_token,
            fallbacks=fallbacks,
        )
        return {
            "answer": str(answer_meta.get("text", "")),
            "sources": enriched_results,
            "used_model": str(answer_meta.get("model", "")).strip(),
            "used_provider": str(answer_meta.get("provider", "")).strip(),
        }
    except Exception as e:
        return {
            "answer": f"{error_prefix}{e}",
            "sources": enriched_results,
            "used_model": "",
            "used_provider": "",
        }


def answer_with_search(
    *,
    question: str,
    search_fn: Callable[[str, int], List[Dict]],
    top_k: int,
    model: str,
    provider: str = "",
    prompt_builder: Callable[[str, str], str],
    context_content_limit: int,
    not_found_message: str,
    error_prefix: str,
    ollama_base_url: str,
    minimax_base_url: str = "",
    minimax_oauth_token: str = "",
    forced_papers: Optional[List[Dict]] = None,
    fallbacks: Optional[List[Dict[str, str]]] = None,
) -> QAResult:
    """先搜尋（或使用指定論文），再產生回答。"""
    detail = answer_with_search_detailed(
        question=question,
        search_fn=search_fn,
        top_k=top_k,
        model=model,
        provider=provider,
        prompt_builder=prompt_builder,
        context_content_limit=context_content_limit,
        not_found_message=not_found_message,
        error_prefix=error_prefix,
        ollama_base_url=ollama_base_url,
        minimax_base_url=minimax_base_url,
        minimax_oauth_token=minimax_oauth_token,
        forced_papers=forced_papers,
        fallbacks=fallbacks,
    )
    return str(detail.get("answer", "")), detail.get("sources", [])


def answer_with_search_detailed(
    *,
    question: str,
    search_fn: Callable[[str, int], List[Dict]],
    top_k: int,
    model: str,
    provider: str = "",
    prompt_builder: Callable[[str, str], str],
    context_content_limit: int,
    not_found_message: str,
    error_prefix: str,
    ollama_base_url: str,
    minimax_base_url: str = "",
    minimax_oauth_token: str = "",
    forced_papers: Optional[List[Dict]] = None,
    fallbacks: Optional[List[Dict[str, str]]] = None,
) -> QADetailResult:
    """先搜尋（或使用指定論文），再產生回答，附帶實際使用模型資訊。"""
    if forced_papers:
        results = forced_papers
    else:
        results = search_fn(question, top_k)

    return answer_with_results_detailed(
        question=question,
        results=results,
        model=model,
        provider=provider,
        prompt_builder=prompt_builder,
        context_content_limit=context_content_limit,
        not_found_message=not_found_message,
        error_prefix=error_prefix,
        ollama_base_url=ollama_base_url,
        minimax_base_url=minimax_base_url,
        minimax_oauth_token=minimax_oauth_token,
        fallbacks=fallbacks,
    )
