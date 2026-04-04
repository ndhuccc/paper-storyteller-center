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
from paper_repository import resolve_manifest_paper_from_generation_output
from qa_service import answer_with_search as service_answer_with_search
from qa_service import build_gui_prompt
from retrieval_service import clear_lance_db_cache
from retrieval_service import rebuild_index as service_rebuild_index
from retrieval_service import search_papers as service_search_papers


OLLAMA_BASE_URL = "http://localhost:11434"
LLM_MODEL = "models/gemini-3.1-flash-lite-preview"
QA_FALLBACK_CHAIN: List[Dict[str, str]] = [
    {"model": "gemma4:e2b",     "provider": "ollama",     "ollama_base_url": "http://134.208.2.42:11434"},
    {"model": "MiniMax-M2.5",   "provider": "minimax.io"},
    {"model": "deepseek-r1:8b", "provider": "ollama",     "ollama_base_url": "http://134.208.2.42:11434"},
]

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


def answer(question: str, forced_papers: Optional[List[Dict]] = None) -> QAResult:
    """Generate answer with forced papers or auto-search results."""
    return service_answer_with_search(
        question=question,
        search_fn=search,
        top_k=3,
        model=LLM_MODEL,
        prompt_builder=build_gui_prompt,
        context_content_limit=3000,
        not_found_message="抱歉，沒有找到相關論文內容。",
        error_prefix="生成錯誤：",
        ollama_base_url=OLLAMA_BASE_URL,
        forced_papers=forced_papers,
        fallbacks=QA_FALLBACK_CHAIN,
    )


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
