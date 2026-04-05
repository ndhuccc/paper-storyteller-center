#!/usr/bin/env python3
"""GUI-facing coordination service for Paper Storyteller Center."""

from typing import Any, Dict, List, Optional, Tuple

from generation_service import cancel_job as generation_cancel_job
from generation_service import get_job as generation_get_job
from generation_service import launch_job_background as generation_launch_job_background
from generation_service import list_jobs as generation_list_jobs
from generation_service import retry_job as generation_retry_job
from generation_service import run_job as generation_run_job
from generation_service import submit_job as generation_submit_job
from html_loader import load_paper_html as service_load_paper_html
from paper_repository import PAPER_STATUS_READY
from paper_repository import delete_paper as repository_delete_paper
from paper_repository import get_all_papers as repository_get_all_papers
from paper_repository import normalize_manifest_paper
from paper_repository import rename_paper as repository_rename_paper
from paper_repository import resolve_manifest_paper_from_generation_output
from qa_service import answer_with_search as service_answer_with_search
from qa_service import answer_with_search_detailed as service_answer_with_search_detailed
from qa_service import build_gui_prompt
from retrieval_service import clear_lance_db_cache
from retrieval_service import rebuild_index as service_rebuild_index
from retrieval_service import search_papers as service_search_papers
from storyteller_pipeline import DEFAULT_REWRITE_FALLBACK_CHAIN
from storyteller_pipeline import DEFAULT_REWRITE_MODEL


OLLAMA_BASE_URL = "http://localhost:11434"
LLM_MODEL = "models/gemini-3.1-flash-lite-preview"
QA_PRIMARY_ENGINE: Dict[str, str] = {"model": LLM_MODEL, "provider": "gemini"}
QA_FALLBACK_CHAIN: List[Dict[str, str]] = [
    {"model": "gemma4:e2b",     "provider": "ollama",     "ollama_base_url": "http://localhost:11434"},
    {"model": "MiniMax-M2.5",   "provider": "minimax.io"},
    {"model": "deepseek-r1:8b", "provider": "ollama",     "ollama_base_url": "http://localhost:11434"},
]
ENGINE_LABELS: Dict[str, str] = {
    "models/gemini-3.1-flash-lite-preview": "Gemini 3.1 Flash Lite",
    "gemma4:e2b": "Gemma 4 E2B",
    "MiniMax-M2.5": "MiniMax M2.5",
    "deepseek-r1:8b": "DeepSeek R1 8B",
}
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io"

QAResult = Tuple[str, List[Dict]]


def list_papers() -> List[Dict]:
    """List repository paper manifest (indexed and/or html-available)."""
    return [normalize_manifest_paper(paper) for paper in repository_get_all_papers()]


def normalize_paper(paper: Dict) -> Dict:
    """Normalize one paper manifest row for stable center usage."""
    return normalize_manifest_paper(paper)


def is_paper_ready(paper: Dict) -> bool:
    """Whether paper can safely enter retrieval/Q&A flow."""
    return normalize_manifest_paper(paper).get("paper_status") == PAPER_STATUS_READY


def resolve_generation_manifest_paper(
    *,
    output_path: Any = "",
    filename: Any = "",
    paper_id: Any = "",
    manifest_papers: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Resolve one manifest paper from generation output metadata."""
    papers = manifest_papers if manifest_papers is not None else list_papers()
    return resolve_manifest_paper_from_generation_output(
        papers,
        output_path=output_path,
        filename=filename,
        paper_id=paper_id,
    )


def get_all_papers() -> List[Dict]:
    """Backward-compatible alias for GUI usage."""
    return list_papers()


def search(query: str, top_k: int = 10, similarity_threshold: float = 0.0) -> List[Dict]:
    """Search relevant papers by semantic similarity."""
    return service_search_papers(query, top_k=top_k, similarity_threshold=similarity_threshold)


def _normalize_provider(spec: Dict[str, Any]) -> str:
    provider = str(spec.get("provider", "")).strip().lower()
    model = str(spec.get("model", "")).strip().lower()
    if provider:
        return provider
    if model.startswith("models/") or model.startswith("gemini"):
        return "gemini"
    return "ollama"


def _engine_id(spec: Dict[str, Any]) -> str:
    provider = _normalize_provider(spec)
    model = str(spec.get("model", "")).strip()
    return f"{provider}:{model}"


def _engine_label(spec: Dict[str, Any]) -> str:
    model = str(spec.get("model", "")).strip()
    return ENGINE_LABELS.get(model, model or "未命名模型")


def _engine_description(spec: Dict[str, Any]) -> str:
    provider = _normalize_provider(spec)
    model = str(spec.get("model", "")).strip()
    if provider == "gemini":
        return f"Google Gemini · {model}"
    if provider.startswith("minimax"):
        return f"MiniMax Portal · {model}"
    ollama_url = str(spec.get("ollama_base_url", "")).strip()
    if ollama_url:
        return f"Ollama · {model} · {ollama_url}"
    return f"{provider or 'ollama'} · {model}"


def _engine_option(spec: Dict[str, Any], index: int) -> Dict[str, Any]:
    return {
        "id": _engine_id(spec),
        "label": _engine_label(spec),
        "model": str(spec.get("model", "")).strip(),
        "provider": _normalize_provider(spec),
        "description": _engine_description(spec),
        "default_order": index,
    }


def _resolve_engine_sequence(
    requested_order: Optional[List[str]],
    default_specs: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    specs = [dict(spec) for spec in default_specs]
    by_id = {_engine_id(spec): spec for spec in specs}
    ordered: List[Dict[str, str]] = []
    seen: set[str] = set()

    for engine_id in (requested_order or []):
        normalized = str(engine_id or "").strip()
        if not normalized or normalized in seen or normalized not in by_id:
            continue
        ordered.append(dict(by_id[normalized]))
        seen.add(normalized)

    for spec in specs:
        engine_id = _engine_id(spec)
        if engine_id in seen:
            continue
        ordered.append(dict(spec))
        seen.add(engine_id)

    return ordered


def get_qa_engine_options() -> List[Dict[str, Any]]:
    engines = [QA_PRIMARY_ENGINE, *QA_FALLBACK_CHAIN]
    return [_engine_option(spec, index + 1) for index, spec in enumerate(engines)]


def get_generation_engine_options() -> List[Dict[str, Any]]:
    engines = [{"model": DEFAULT_REWRITE_MODEL, "provider": "gemini"}, *DEFAULT_REWRITE_FALLBACK_CHAIN]
    return [_engine_option(spec, index + 1) for index, spec in enumerate(engines)]


def resolve_qa_engine_chain(requested_order: Optional[List[str]] = None) -> Dict[str, Any]:
    ordered_specs = _resolve_engine_sequence(requested_order, [QA_PRIMARY_ENGINE, *QA_FALLBACK_CHAIN])
    primary = ordered_specs[0] if ordered_specs else dict(QA_PRIMARY_ENGINE)
    fallbacks = [dict(spec) for spec in ordered_specs[1:]]
    return {
        "primary_model": str(primary.get("model", LLM_MODEL)).strip() or LLM_MODEL,
        "primary_provider": _normalize_provider(primary),
        "primary_ollama_base_url": str(primary.get("ollama_base_url") or OLLAMA_BASE_URL).strip() or OLLAMA_BASE_URL,
        "primary_minimax_base_url": str(primary.get("minimax_base_url") or DEFAULT_MINIMAX_BASE_URL).strip() or DEFAULT_MINIMAX_BASE_URL,
        "primary_minimax_oauth_token": str(primary.get("minimax_oauth_token", "")).strip(),
        "fallbacks": fallbacks,
        "ordered_engines": [_engine_option(spec, index + 1) for index, spec in enumerate(ordered_specs)],
        "ordered_ids": [_engine_id(spec) for spec in ordered_specs],
    }


def resolve_generation_engine_chain(requested_order: Optional[List[str]] = None) -> Dict[str, Any]:
    ordered_specs = _resolve_engine_sequence(
        requested_order,
        [{"model": DEFAULT_REWRITE_MODEL, "provider": "gemini"}, *DEFAULT_REWRITE_FALLBACK_CHAIN],
    )
    primary = ordered_specs[0] if ordered_specs else {"model": DEFAULT_REWRITE_MODEL, "provider": "gemini"}
    fallbacks = [dict(spec) for spec in ordered_specs[1:]]
    return {
        "primary_model": str(primary.get("model", DEFAULT_REWRITE_MODEL)).strip() or DEFAULT_REWRITE_MODEL,
        "fallbacks": fallbacks,
        "ordered_engines": [_engine_option(spec, index + 1) for index, spec in enumerate(ordered_specs)],
        "ordered_ids": [_engine_id(spec) for spec in ordered_specs],
    }


def answer(question: str, forced_papers: Optional[List[Dict]] = None) -> QAResult:
    """Generate answer with forced papers or auto-search results."""
    detail = answer_detailed(question=question, forced_papers=forced_papers)
    return str(detail.get("answer", "")), detail.get("sources", [])


def answer_detailed(
    question: str,
    forced_papers: Optional[List[Dict]] = None,
    engine_order: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate answer with engine metadata and effective engine order."""
    resolved = resolve_qa_engine_chain(engine_order)
    detail = service_answer_with_search_detailed(
        question=question,
        search_fn=search,
        top_k=3,
        model=resolved["primary_model"],
        provider=resolved["primary_provider"],
        prompt_builder=build_gui_prompt,
        context_content_limit=3000,
        not_found_message="抱歉，沒有找到相關論文內容。",
        error_prefix="生成錯誤：",
        ollama_base_url=resolved["primary_ollama_base_url"],
        minimax_base_url=resolved["primary_minimax_base_url"],
        minimax_oauth_token=resolved["primary_minimax_oauth_token"],
        forced_papers=forced_papers,
        fallbacks=resolved["fallbacks"],
    )
    detail["engine_order"] = resolved["ordered_engines"]
    detail["requested_engine_order"] = resolved["ordered_ids"]
    return detail


def load_html(paper_id: str) -> str:
    """Load paper HTML content by paper id."""
    return service_load_paper_html(paper_id)


def rebuild_index() -> bool:
    """Rebuild index and clear retrieval cache on success."""
    ok = service_rebuild_index()
    if ok:
        clear_lance_db_cache()
    return ok


def delete_paper(paper_id: str) -> Dict[str, Any]:
    """Delete one paper artifact and indexed rows."""
    result = repository_delete_paper(paper_id)
    clear_lance_db_cache()
    if isinstance(result, dict):
        return result
    return {
        "ok": False,
        "paper_id": str(paper_id or "").strip(),
        "message": "delete_paper 回傳格式錯誤",
    }


def rename_paper(paper_id: str, new_name: str) -> Dict[str, Any]:
    """Rename one paper HTML file and remove stale index rows."""
    result = repository_rename_paper(paper_id, new_name)
    clear_lance_db_cache()
    if isinstance(result, dict):
        return result
    return {
        "ok": False,
        "paper_id": str(paper_id or "").strip(),
        "new_name": str(new_name or "").strip(),
        "message": "rename_paper 回傳格式錯誤",
    }


def submit_generation_job(payload: Optional[Dict] = None) -> Dict:
    """Submit a storyteller generation job (skeleton)."""
    return generation_submit_job(payload=payload)


def list_generation_jobs(limit: int = 20, status: Optional[str] = None) -> List[Dict]:
    """List storyteller generation jobs (skeleton)."""
    return generation_list_jobs(limit=limit, status=status)


def get_generation_job(job_id: str) -> Optional[Dict]:
    """Get one storyteller generation job (skeleton)."""
    return generation_get_job(job_id)


def run_generation_job(job_id: str) -> Optional[Dict]:
    """Run one storyteller generation job via stub pipeline."""
    return generation_run_job(job_id)


def launch_generation_job(job_id: str) -> Optional[Dict]:
    """Launch one storyteller generation job in detached background process."""
    return generation_launch_job_background(job_id)


def retry_generation_job(job_id: str) -> Optional[Dict]:
    """Retry one storyteller generation job in detached background process."""
    return generation_retry_job(job_id)


def cancel_generation_job(job_id: str) -> Optional[Dict]:
    """Soft-cancel one pending/running storyteller generation job."""
    return generation_cancel_job(job_id)
