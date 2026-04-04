#!/usr/bin/env python3
"""Generation job service for storyteller pipeline orchestration."""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from job_store import JobStore
from paper_repository import get_all_papers as repository_get_all_papers
from paper_repository import resolve_manifest_paper_from_generation_output
from retrieval_service import clear_lance_db_cache
from retrieval_service import rebuild_index as retrieval_rebuild_index
from storyteller_pipeline import run_storyteller_pipeline


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
AUTO_INDEX_MODE_FULL_REBUILD = "full_rebuild"


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


def _extract_output_filename(*, pipeline_output: Dict[str, Any], output_path: Any) -> str:
    direct = str(
        pipeline_output.get("filename")
        or pipeline_output.get("output_filename")
        or ""
    ).strip()
    if direct:
        return Path(direct).name

    artifacts = pipeline_output.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            if str(artifact.get("type", "")).strip().lower() != "html":
                continue
            filename = str(artifact.get("filename", "")).strip()
            if filename:
                return Path(filename).name
            path_value = str(artifact.get("path", "")).strip()
            if path_value:
                return Path(path_value).name

    text_path = str(output_path or "").strip()
    if text_path:
        return Path(text_path).name
    return ""


def _compact_manifest_paper(paper: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "paper_id": str(paper.get("paper_id", paper.get("id", ""))).strip(),
        "id": str(paper.get("id", paper.get("paper_id", ""))).strip(),
        "title": str(paper.get("title", "")).strip(),
        "filename": str(paper.get("filename", "")).strip(),
        "filepath": str(paper.get("filepath", "")).strip(),
        "paper_status": str(paper.get("paper_status", "")).strip(),
        "manifest_source": str(paper.get("manifest_source", "")).strip(),
        "has_html": _coerce_bool(paper.get("has_html")),
        "is_indexed": _coerce_bool(paper.get("is_indexed")),
    }


def _resolve_manifest_link_after_auto_index(
    *,
    payload: Dict[str, Any],
    pipeline_output: Dict[str, Any],
    output_path: Any,
) -> Dict[str, Any]:
    requested_paper_id = str(
        pipeline_output.get("paper_id")
        or payload.get("paper_id")
        or ""
    ).strip()
    requested_output_path = str(output_path or "").strip()
    requested_filename = _extract_output_filename(
        pipeline_output=pipeline_output,
        output_path=output_path,
    )

    resolution: Dict[str, Any] = {
        "attempted": True,
        "ok": False,
        "message": "",
        "resolved_paper_id": "",
        "match_rule": "",
        "requested": {
            "paper_id": requested_paper_id,
            "output_path": requested_output_path,
            "filename": requested_filename,
        },
        "paper": None,
    }

    try:
        manifest_papers = repository_get_all_papers()
        matched = resolve_manifest_paper_from_generation_output(
            manifest_papers,
            output_path=requested_output_path,
            filename=requested_filename,
            paper_id=requested_paper_id,
        )
        if not isinstance(matched, dict):
            resolution["message"] = "manifest resolver returned invalid response"
            return resolution

        resolution["resolved_paper_id"] = str(matched.get("resolved_paper_id", "")).strip()
        resolution["match_rule"] = str(matched.get("match_rule", "")).strip()

        matched_paper = matched.get("paper")
        if isinstance(matched_paper, dict):
            compact_paper = _compact_manifest_paper(matched_paper)
            resolution["ok"] = True
            resolution["paper"] = compact_paper
            rule = resolution["match_rule"] or "unknown_rule"
            status = compact_paper.get("paper_status") or "unknown"
            resolution["message"] = (
                f"manifest paper resolved by {rule}; paper_status={status}"
            )
            if not resolution["resolved_paper_id"]:
                resolution["resolved_paper_id"] = str(compact_paper.get("paper_id", "")).strip()
            return resolution

        resolution["ok"] = False
        resolution["message"] = "manifest paper not uniquely resolved after full rebuild"
        return resolution
    except Exception as exc:
        resolution["ok"] = False
        resolution["message"] = f"manifest resolution raised {type(exc).__name__}: {exc}"
        return resolution


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

    auto_index_requested = _coerce_bool(payload.get("auto_index"))
    auto_index = {
        "requested": auto_index_requested,
        "attempted": False,
        "mode": AUTO_INDEX_MODE_FULL_REBUILD,
        "state": "not_requested" if not auto_index_requested else "pending",
        "ok": None,
        "message": (
            "auto-index not requested; mode=full_rebuild"
            if not auto_index_requested
            else "auto-index requested; pending full rebuild"
        ),
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "manifest_resolution": {
            "attempted": False,
            "ok": None,
            "message": "manifest resolution skipped because auto-index did not run",
            "resolved_paper_id": "",
            "match_rule": "",
            "requested": {
                "paper_id": str(payload.get("paper_id", "")).strip(),
                "output_path": str(output_html_path or "").strip(),
                "filename": _extract_output_filename(
                    pipeline_output=pipeline_output,
                    output_path=output_html_path,
                ),
            },
            "paper": None,
        },
    }
    result["metadata"]["auto_index"] = auto_index

    if auto_index_requested:
        auto_index_started_at = _utc_now_iso()
        auto_index["attempted"] = True
        auto_index["started_at"] = auto_index_started_at
        try:
            rebuild_ok = retrieval_rebuild_index()
            auto_index_completed_at = _utc_now_iso()
            auto_index["completed_at"] = auto_index_completed_at
            auto_index["duration_ms"] = _elapsed_ms(auto_index_started_at, auto_index_completed_at)
            if rebuild_ok:
                clear_lance_db_cache()
                auto_index["ok"] = True
                auto_index["state"] = "succeeded"
                auto_index["message"] = "auto-index full rebuild completed"

                manifest_resolution = _resolve_manifest_link_after_auto_index(
                    payload=payload,
                    pipeline_output=pipeline_output,
                    output_path=output_html_path,
                )
                auto_index["manifest_resolution"] = manifest_resolution

                if manifest_resolution.get("ok") is True:
                    resolved_paper_id = str(manifest_resolution.get("resolved_paper_id", "")).strip()
                    if resolved_paper_id:
                        result["paper_id"] = resolved_paper_id
                        result["output"]["paper_id"] = resolved_paper_id
                        result["metadata"]["paper_id"] = resolved_paper_id
                    manifest_paper = manifest_resolution.get("paper")
                    if isinstance(manifest_paper, dict):
                        result["metadata"]["manifest_paper"] = manifest_paper
                else:
                    result["warnings"].append(
                        "auto_index succeeded but generated output was not uniquely resolved in manifest"
                    )
            else:
                auto_index["ok"] = False
                auto_index["state"] = "failed"
                auto_index["message"] = "auto-index full rebuild returned False"
                auto_index["manifest_resolution"] = {
                    **auto_index["manifest_resolution"],
                    "message": "manifest resolution skipped because full rebuild failed",
                }
                result["warnings"].append("auto_index requested but index rebuild failed")
                result["errors"].append(
                    {
                        "stage": "auto_index",
                        "type": "IndexRebuildFailed",
                        "message": "auto-index full rebuild returned False",
                    }
                )
        except Exception as exc:
            auto_index_completed_at = _utc_now_iso()
            auto_index["completed_at"] = auto_index_completed_at
            auto_index["duration_ms"] = _elapsed_ms(auto_index_started_at, auto_index_completed_at)
            auto_index["ok"] = False
            auto_index["state"] = "error"
            auto_index["message"] = f"auto-index full rebuild raised {type(exc).__name__}: {exc}"
            auto_index["manifest_resolution"] = {
                **auto_index["manifest_resolution"],
                "message": "manifest resolution skipped because full rebuild raised exception",
            }
            result["warnings"].append("auto_index requested but index rebuild raised exception")
            result["errors"].append(
                {
                    "stage": "auto_index",
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )

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
                "mode": AUTO_INDEX_MODE_FULL_REBUILD,
                "state": "skipped_pipeline_failed",
                "ok": None,
                "message": "pipeline failed before auto-index full rebuild",
                "started_at": None,
                "completed_at": None,
                "duration_ms": None,
                "manifest_resolution": {
                    "attempted": False,
                    "ok": None,
                    "message": "manifest resolution skipped because pipeline failed before auto-index",
                    "resolved_paper_id": "",
                    "match_rule": "",
                    "requested": {
                        "paper_id": str(payload.get("paper_id", "")).strip(),
                        "output_path": "",
                        "filename": "",
                    },
                    "paper": None,
                },
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
        if job.get("status") != STATUS_PENDING:
            return job

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

    def launch_job_background(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Launch one generation job in a detached background Python process."""
        job = self.store.get_job(job_id)
        if not job:
            return None
        if job.get("status") != STATUS_PENDING:
            return job

        module_dir = Path(__file__).resolve().parent
        cmd = [sys.executable, "-m", "generation_service", "--run-job", job_id]
        subprocess.Popen(
            cmd,
            cwd=str(module_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return job


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


def launch_job_background(job_id: str) -> Optional[Dict[str, Any]]:
    """Launch one generation job in background process."""
    return _service.launch_job_background(job_id)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generation job runner")
    parser.add_argument("--run-job", dest="run_job_id", help="Run one job by job id")
    return parser


def _main(argv: Optional[List[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.run_job_id:
        run_job(args.run_job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
