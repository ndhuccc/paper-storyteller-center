#!/usr/bin/env python3
"""Generation job service for storyteller pipeline orchestration."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from job_store import JobStore
from retrieval_service import clear_lance_db_cache
from retrieval_service import rebuild_index as retrieval_rebuild_index
from storyteller_pipeline import run_storyteller_pipeline


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: str, completed_at: str) -> Optional[int]:
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except (TypeError, ValueError):
        return None

    delta_ms = int((completed - started).total_seconds() * 1000)
    return max(delta_ms, 0)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def _coerce_warnings(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    warning = str(raw).strip()
    return [warning] if warning else []


def _coerce_artifacts(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    artifacts: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        artifact = dict(item)
        path_value = artifact.get("path")
        if isinstance(path_value, str) and path_value.strip():
            artifact_path = Path(path_value).expanduser()
            artifact["exists"] = artifact_path.exists()
            if artifact_path.exists() and artifact_path.is_file():
                artifact["size_bytes"] = artifact_path.stat().st_size
        artifacts.append(artifact)
    return artifacts


def _build_success_result(
    *,
    job: Dict[str, Any],
    payload: Dict[str, Any],
    pipeline_output: Dict[str, Any],
    started_at: str,
    completed_at: str,
) -> Dict[str, Any]:
    warnings = _coerce_warnings(pipeline_output.get("warnings"))
    artifacts = _coerce_artifacts(pipeline_output.get("artifacts"))
    steps = pipeline_output.get("steps", [])
    pipeline_name = pipeline_output.get("pipeline") or "storyteller_pipeline"
    implemented = bool(pipeline_output.get("implemented", True))
    output_pdf_path = pipeline_output.get("pdf_path")
    output_html_path = pipeline_output.get("output_path")
    generated_at = pipeline_output.get("generated_at")
    model = pipeline_output.get("model")
    sections_generated = pipeline_output.get("sections_generated")

    result: Dict[str, Any] = {
        "ok": True,
        "job_id": job.get("job_id"),
        "pipeline": pipeline_name,
        "implemented": implemented,
        "input": payload,
        "pdf_path": output_pdf_path,
        "output_path": output_html_path,
        "model": model,
        "generated_at": generated_at,
        "sections_generated": sections_generated,
        "steps": steps,
        "pipeline_detail": {
            "name": pipeline_name,
            "implemented": implemented,
            "raw_output": pipeline_output,
        },
        "output": {
            "pdf_path": output_pdf_path,
            "output_path": output_html_path,
            "generated_at": generated_at,
        },
        "artifacts": artifacts,
        "warnings": warnings,
        "errors": [],
        "metadata": {
            "status": STATUS_SUCCEEDED,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": _elapsed_ms(started_at, completed_at),
            "model": model,
            "sections_generated": sections_generated,
            "steps": steps,
            "input_payload_keys": sorted(payload.keys()),
        },
    }

    result["metadata"]["auto_index"] = {
        "requested": False,
        "attempted": False,
        "ok": None,
        "message": None,
    }

    if _coerce_bool(payload.get("auto_index")):
        auto_index = {
            "requested": True,
            "attempted": True,
            "ok": False,
            "message": None,
        }
        try:
            rebuild_ok = retrieval_rebuild_index()
            if rebuild_ok:
                clear_lance_db_cache()
                auto_index["ok"] = True
                auto_index["message"] = "Index rebuild completed"
            else:
                auto_index["message"] = "Index rebuild returned False"
                result["warnings"].append("auto_index requested but index rebuild failed")
                result["errors"].append(
                    {
                        "stage": "auto_index",
                        "type": "IndexRebuildFailed",
                        "message": "Index rebuild returned False",
                    }
                )
        except Exception as exc:
            auto_index["message"] = f"{type(exc).__name__}: {exc}"
            result["warnings"].append("auto_index requested but index rebuild raised exception")
            result["errors"].append(
                {
                    "stage": "auto_index",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        result["metadata"]["auto_index"] = auto_index

    return result


def _build_failed_result(
    *,
    job: Dict[str, Any],
    payload: Dict[str, Any],
    started_at: str,
    completed_at: str,
    exc: Exception,
) -> Dict[str, Any]:
    output_pdf_path = payload.get("pdf_path") or payload.get("source_pdf_path")
    return {
        "ok": False,
        "job_id": job.get("job_id"),
        "pipeline": "storyteller_pipeline",
        "implemented": False,
        "input": payload,
        "pdf_path": output_pdf_path,
        "output_path": None,
        "model": None,
        "generated_at": None,
        "sections_generated": 0,
        "steps": [],
        "pipeline_detail": {
            "name": "storyteller_pipeline",
            "implemented": False,
            "raw_output": None,
        },
        "output": {
            "pdf_path": output_pdf_path,
            "output_path": None,
            "generated_at": None,
        },
        "artifacts": [],
        "warnings": [],
        "errors": [
            {
                "stage": "pipeline",
                "type": type(exc).__name__,
                "message": str(exc),
            }
        ],
        "metadata": {
            "status": STATUS_FAILED,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": _elapsed_ms(started_at, completed_at),
            "input_payload_keys": sorted(payload.keys()),
            "auto_index": {
                "requested": _coerce_bool(payload.get("auto_index")),
                "attempted": False,
                "ok": None,
                "message": "pipeline failed before auto-index",
            },
        },
    }


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

        payload = job.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

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
            pipeline_output = run_storyteller_pipeline(job)
            completed_at = _utc_now_iso()
            result = _build_success_result(
                job=job,
                payload=payload,
                pipeline_output=pipeline_output,
                started_at=started_at,
                completed_at=completed_at,
            )
            return self.store.update_job(
                job_id,
                {
                    "status": STATUS_SUCCEEDED,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "result": result,
                    "error": None,
                },
            )
        except Exception as exc:
            completed_at = _utc_now_iso()
            result = _build_failed_result(
                job=job,
                payload=payload,
                started_at=started_at,
                completed_at=completed_at,
                exc=exc,
            )
            return self.store.update_job(
                job_id,
                {
                    "status": STATUS_FAILED,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "result": result,
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
