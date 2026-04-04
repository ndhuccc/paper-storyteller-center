#!/usr/bin/env python3
"""
Q&A 服務模組
職責：組裝上下文、建立 prompt、呼叫 LLM 生成回答。
"""

import json
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

QAResult = Tuple[str, List[Dict]]


def build_cli_prompt(question: str, context: str) -> str:
    """paper_center.py 使用的 prompt 風格。"""
    return f"""你是一個專業的論文說書人，擅長比較和解釋學術論文的內容。

根據以下論文內容，回答用戶的問題。請用繁體中文回答，並引用相關的論文標題：

=== 論文內容 ===
{context}

=== 用戶問題 ===
{question}

回答："""


def build_gui_prompt(question: str, context: str) -> str:
    """paper_center_gui.py 使用的 prompt 風格。"""
    return f"""你是專業論文說書人，用繁體中文回答，並引用論文標題。

=== 論文內容 ===
{context}

=== 問題 ===
{question}

回答："""


def build_context(results: List[Dict], content_limit: int) -> str:
    """把論文結果組成目前沿用的上下文字串格式。"""
    context_parts = [
        f"=== {r.get('title', '未知')} ===\n{r.get('content', '')[:content_limit]}"
        for r in results
    ]
    return "\n\n".join(context_parts)


def generate_answer(
    *,
    prompt: str,
    model: str,
    ollama_base_url: str,
    timeout: int = 180,
) -> str:
    """呼叫 Ollama 生成回答。"""
    req = urllib.request.Request(
        f"{ollama_base_url}/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read()).get("response", "").strip()


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

    context = build_context(results, context_content_limit)
    prompt = prompt_builder(question, context)
    try:
        answer = generate_answer(
            prompt=prompt,
            model=model,
            ollama_base_url=ollama_base_url,
        )
        return answer, results
    except Exception as e:
        return f"{error_prefix}{e}", results


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
