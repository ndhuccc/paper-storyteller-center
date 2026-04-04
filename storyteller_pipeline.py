#!/usr/bin/env python3
"""Stub pipeline for future storyteller generation flow."""

from typing import Any, Dict


def run_storyteller_pipeline(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run storyteller generation pipeline (stub only).

    NOTE:
    This intentionally does not implement the Hybrid PDF-to-HTML flow yet.
    It only returns structured placeholder output for integration scaffolding.
    """
    return {
        "pipeline": "storyteller_hybrid_generation",
        "implemented": False,
        "message": "Storyteller Hybrid generation pipeline is not implemented yet.",
        "job_id": job.get("job_id"),
        "input": job.get("payload", {}),
        "steps": [
            {"name": "ingest_source", "status": "pending", "note": "stub"},
            {"name": "pdf_to_structured_content", "status": "pending", "note": "stub"},
            {"name": "html_story_render", "status": "pending", "note": "stub"},
        ],
        "artifacts": [],
    }

