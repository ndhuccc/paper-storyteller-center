#!/usr/bin/env python3
"""GUI-facing coordination service for Paper Storyteller Center."""

from typing import Dict, List, Optional, Tuple

from html_loader import load_paper_html as service_load_paper_html
from paper_repository import get_all_papers as repository_get_all_papers
from qa_service import answer_with_search as service_answer_with_search
from qa_service import build_gui_prompt
from retrieval_service import clear_lance_db_cache
from retrieval_service import rebuild_index as service_rebuild_index
from retrieval_service import search_papers as service_search_papers


OLLAMA_BASE_URL = "http://localhost:11434"
LLM_MODEL = "deepseek-r1:8b"

QAResult = Tuple[str, List[Dict]]


def list_papers() -> List[Dict]:
    """List all indexed papers."""
    return repository_get_all_papers()


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
