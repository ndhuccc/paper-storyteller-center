#!/usr/bin/env python3
"""Generation job service (skeleton for future storyteller flow)."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from job_store import JobStore
from storyteller_pipeline import run_storyteller_pipeline


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GenerationService:
    """Manage generation jobs and invoke pipeline execution."""

    def __init__(self, store: Optional[JobStore] = None):
        self.store = store or JobStore()

    def create_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = _utc_now_iso()
        job = {
            "job_id": str(uuid4()),
            "job_type": "storyteller_generation",
            "status": STATUS_PENDING,
            "payload": payload or {},
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }
        return self.store.save_job(job)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_job(job_id)

    def list_jobs(self, limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.store.list_jobs(limit=limit, status=status)

    def run_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.store.get_job(job_id)
        if not job:
            return None

        started_at = _utc_now_iso()
        job = self.store.update_job(
            job_id,
            {
                "status": STATUS_RUNNING,
                "updated_at": started_at,
                "started_at": started_at,
                "error": None,
            },
        )
        if not job:
            return None

        try:
            result = run_storyteller_pipeline(job)
            completed_at = _utc_now_iso()
            return self.store.update_job(
                job_id,
                {
                    "status": STATUS_SUCCEEDED,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "result": result,
                },
            )
        except Exception as exc:
            completed_at = _utc_now_iso()
            return self.store.update_job(
                job_id,
                {
                    "status": STATUS_FAILED,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


_service = GenerationService()


def submit_job(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a generation job."""
    return _service.create_job(payload=payload)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get one generation job."""
    return _service.get_job(job_id)


def list_jobs(limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List generation jobs."""
    return _service.list_jobs(limit=limit, status=status)


def run_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Run one generation job with stub pipeline."""
    return _service.run_job(job_id)

