#!/usr/bin/env python3
"""JSON-file-backed storage for generation jobs."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent
# 任務 JSON 位於專案根目錄 ./jobs/
DEFAULT_JOBS_DIR = PROJECT_DIR / "jobs"


class JobStore:
    """Persist jobs as one JSON file per job id."""

    def __init__(self, jobs_dir: Optional[Path] = None):
        self.jobs_dir = jobs_dir or DEFAULT_JOBS_DIR
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def save_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(job.get("job_id", "")).strip()
        if not job_id:
            raise ValueError("job_id is required")

        path = self._job_path(job_id)
        temp_path = path.with_suffix(".json.tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        temp_path.replace(path)
        return job

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        path = self._job_path(job_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def list_jobs(self, limit: Optional[int] = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
        jobs: List[Dict[str, Any]] = []
        for path in self.jobs_dir.glob("*.json"):
            if path.name.endswith(".tmp.json"):
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    job = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if status and job.get("status") != status:
                continue
            jobs.append(job)

        jobs.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        if limit is None:
            return jobs
        if limit <= 0:
            return []
        return jobs[:limit]

    def update_job(self, job_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        job = self.get_job(job_id)
        if not job:
            return None
        job.update(updates)
        return self.save_job(job)

