#!/usr/bin/env python3
"""依 htmls/*_{job_id}.html 將 jobs/*.json 內的 output_path 等欄位對齊為實際檔案路徑。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from storyteller_pipeline import STORYTELLERS_DIR  # noqa: E402


def _replace_exact(obj: object, old: str, new: str) -> object:
    if isinstance(obj, dict):
        return {k: _replace_exact(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_exact(x, old, new) for x in obj]
    if isinstance(obj, str) and obj == old:
        return new
    return obj


def main() -> None:
    jobs_dir = PROJECT_DIR / "jobs"
    for job_path in sorted(jobs_dir.glob("*.json")):
        if job_path.name.endswith(".tmp.json"):
            continue
        try:
            data = json.loads(job_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        job_id = str(data.get("job_id") or "").strip()
        if not job_id:
            continue
        suffix = f"_{job_id}.html"
        matches = [p for p in STORYTELLERS_DIR.glob("*.html") if p.name.endswith(suffix)]
        if len(matches) != 1:
            continue
        new_abs = str(matches[0].resolve())
        result = data.get("result")
        if not isinstance(result, dict):
            continue
        old = str(result.get("output_path") or "").strip()
        if not old or old == new_abs:
            continue
        updated = _replace_exact(data, old, new_abs)
        tmp = job_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(job_path)
        print(f"repaired {job_id[:8]}… -> {matches[0].name}")


if __name__ == "__main__":
    main()
