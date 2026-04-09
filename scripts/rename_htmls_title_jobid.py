#!/usr/bin/env python3
"""
將 htmls/ 內既有說書人 HTML 依「論文標題_任務識別號.html」重新命名，
並與 storyteller_pipeline._build_output_path 規則一致。

依 jobs/ 中 succeeded 任務的 output_path basename 對應檔案；若多個任務共用同一
basename，則複製成多份檔名（各自 job_id 不同）。

若帶 --no-update-jobs，則只搬移檔案、不寫回 jobs/*.json。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 專案根目錄
PROJECT_DIR = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(PROJECT_DIR))

from storyteller_pipeline import (  # noqa: E402
    STORYTELLERS_DIR,
    _build_output_path,
    _safe_html_filename_segment,
)


def _parse_title_from_html(html_path: Path) -> str:
    try:
        text = html_path.read_text(encoding="utf-8", errors="ignore")[:50000]
    except OSError:
        return ""
    m = re.search(r"<title>([^<]+)</title>", text, re.I | re.DOTALL)
    if not m:
        return ""
    raw = html.unescape(m.group(1)).strip()
    for suf in (" - 說書人版", " - 說書人", " | 說書人版"):
        if raw.endswith(suf):
            raw = raw[: -len(suf)].strip()
    return raw


def _resolve_title(job: Dict[str, Any], html_path: Path) -> str:
    payload = job.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    t = payload.get("title") or payload.get("paper_title")
    if isinstance(t, str) and t.strip():
        return t.strip()
    from_html = _parse_title_from_html(html_path)
    if from_html:
        return from_html
    pdf_path = Path(str(payload.get("pdf_path") or payload.get("source_pdf_path") or "paper.pdf"))
    return pdf_path.stem or "paper"


def _resolve_html_in_project(result: Dict[str, Any], basename: str) -> Optional[Path]:
    """優先使用 htmls/basename；否則僅接受舊絕對路徑（檔名一致），且排除專案根目錄下的誤判。"""
    local = STORYTELLERS_DIR / basename
    if local.is_file():
        return local
    project_root = PROJECT_DIR.resolve()

    def _pick(raw: str) -> Optional[Path]:
        if not raw.strip():
            return None
        p = Path(raw)
        if not p.is_file() or p.name != basename:
            return None
        parent = p.resolve().parent
        if parent == STORYTELLERS_DIR.resolve():
            return p
        # 允許從其他目錄（例如舊 Storytellers）帶入；排除專案根目錄的雜檔
        if parent == project_root:
            return None
        return p

    out = str(result.get("output_path") or "").strip()
    picked = _pick(out)
    if picked:
        return picked
    out2 = str((result.get("output") or {}).get("output_path") or "").strip()
    return _pick(out2)


def _load_jobs() -> List[Dict[str, Any]]:
    jobs_dir = PROJECT_DIR / "jobs"
    out: List[Dict[str, Any]] = []
    for path in sorted(jobs_dir.glob("*.json")):
        if path.name.endswith(".tmp.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(data)
    return out


def _target_path_for_job(job: Dict[str, Any], html_path: Path) -> Path:
    payload = dict(job.get("payload") or {})
    payload.pop("output_filename", None)
    payload.pop("output_name", None)
    title = _resolve_title(job, html_path)
    pdf_path = Path(str(payload.get("pdf_path") or payload.get("source_pdf_path") or "paper.pdf"))
    return _build_output_path(pdf_path, payload, title=title, job={"job_id": job.get("job_id")})


def _replace_path_strings(obj: Any, old: str, new: str) -> Any:
    if isinstance(obj, dict):
        return {k: _replace_path_strings(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_path_strings(x, old, new) for x in obj]
    if isinstance(obj, str) and old in obj:
        return obj.replace(old, new)
    return obj


def _update_job_file(job_path: Path, new_abs: str) -> None:
    """以 result.output_path（及 output.output_path）為舊路徑基準替換，避免與 JSON 內實際字串不一致。"""
    data = json.loads(job_path.read_text(encoding="utf-8"))
    result = data.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    old_main = str(result.get("output_path") or "").strip()
    out2 = str((result.get("output") or {}).get("output_path") or "").strip()
    if old_main:
        data = _replace_path_strings(data, old_main, new_abs)
    if out2 and out2 != old_main:
        data = _replace_path_strings(data, out2, new_abs)
    tmp = job_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(job_path)


def _orphan_surrogate_id(filename: str) -> str:
    """無任務紀錄時以檔名雜湊當作識別尾碼（12 hex）。"""
    return hashlib.sha256(filename.encode("utf-8")).hexdigest()[:12]


def plan_renames(
    dry_run: bool,
    update_jobs: bool,
    rename_orphans: bool,
) -> Tuple[List[str], List[str]]:
    """
    Returns (messages, errors).
    """
    messages: List[str] = []
    errors: List[str] = []
    jobs = _load_jobs()

    # basename -> list of (job, result_path)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for job in jobs:
        if job.get("status") != "succeeded":
            continue
        result = job.get("result") or {}
        if not isinstance(result, dict):
            continue
        out = str(result.get("output_path") or "").strip()
        if not out:
            out = str((result.get("output") or {}).get("output_path") or "").strip()
        if not out:
            continue
        basename = Path(out).name
        if not basename.lower().endswith(".html"):
            continue
        html_path = _resolve_html_in_project(result, basename)
        if not html_path:
            errors.append(
                f"skip job {job.get('job_id')}: 找不到 {basename}（htmls/ 或舊路徑）"
            )
            continue
        groups.setdefault(basename, []).append(job)

    # sort jobs in each group by created_at
    for basename in groups:
        groups[basename].sort(
            key=lambda j: str(j.get("created_at") or ""),
            reverse=True,
        )

    processed_basenames: set[str] = set()
    jobs_to_update: List[Tuple[str, str]] = []  # job_id, new_abs

    orphan_before = {p.name for p in STORYTELLERS_DIR.glob("*.html")}
    orphans_unmatched = sorted(orphan_before - set(groups.keys()))

    for basename, job_list in sorted(groups.items()):
        src = STORYTELLERS_DIR / basename
        first_r = job_list[0].get("result") or {}
        alt = _resolve_html_in_project(first_r, basename)
        html_for_meta = src
        if not src.is_file():
            if alt and alt.is_file():
                html_for_meta = alt
                if alt.resolve() != src.resolve():
                    if not dry_run:
                        shutil.copy2(alt, src)
                    messages.append(f"copy 至 htmls: {alt} -> {src}")
            else:
                errors.append(f"skip group {basename}: 找不到來源檔")
                continue

        targets: List[Tuple[Dict[str, Any], Path]] = []
        for job in job_list:
            t = _target_path_for_job(job, html_for_meta)
            targets.append((job, t))

        # 去重：同一目標只保留一筆
        seen_dst: set[str] = set()
        unique_targets: List[Tuple[Dict[str, Any], Path]] = []
        for job, t in targets:
            key = str(t.resolve())
            if key in seen_dst:
                errors.append(
                    f"警告: job {job.get('job_id')} 目標重複，略過: {t.name}"
                )
                continue
            seen_dst.add(key)
            unique_targets.append((job, t))

        if not unique_targets:
            continue

        tmp_moves: List[Tuple[Path, Path]] = []

        if len(unique_targets) == 1:
            job, dst = unique_targets[0]
            if src.resolve() == dst.resolve():
                messages.append(f"skip（已是目標名）: {src.name}")
                processed_basenames.add(basename)
                continue
            if dst.exists() and src.resolve() != dst.resolve():
                errors.append(
                    f"衝突: 目標已存在且非來源 {dst.name}，請手動處理"
                )
                continue
            if dry_run:
                messages.append(f"rename {src.name} -> {dst.name}")
            else:
                tmp = dst.parent / f".rename_{uuid.uuid4().hex}.html"
                shutil.move(str(src), str(tmp))
                tmp_moves.append((tmp, dst))
                jobs_to_update.append((str(job.get("job_id")), str(dst.resolve())))
            processed_basenames.add(basename)
        else:
            # 多任務共用同一檔：複製到各自目標
            copies_ok = 0
            for i, (job, dst) in enumerate(unique_targets):
                if dst.exists():
                    err = f"衝突: 目標已存在 {dst.name}"
                    errors.append(err)
                    continue
                if dry_run:
                    messages.append(
                        f"copy {src.name} -> {dst.name} (job {str(job.get('job_id'))[:8]}…)"
                    )
                    copies_ok += 1
                else:
                    shutil.copy2(src, dst)
                    jobs_to_update.append((str(job.get("job_id")), str(dst.resolve())))
                    copies_ok += 1
            if not dry_run and copies_ok == len(unique_targets):
                try:
                    src.unlink()
                except OSError as exc:
                    errors.append(f"刪除共用來源失敗 {src}: {exc}")
            elif not dry_run and copies_ok < len(unique_targets):
                errors.append(
                    f"多任務複製未全部成功，保留共用來源: {src.name}"
                )
            processed_basenames.add(basename)

        if not dry_run and tmp_moves:
            for tmp, dst in tmp_moves:
                shutil.move(str(tmp), str(dst))

    if update_jobs and not dry_run and jobs_to_update:
        jobs_dir = PROJECT_DIR / "jobs"
        for job_id, new_abs in jobs_to_update:
            jp = jobs_dir / f"{job_id}.json"
            if not jp.is_file():
                errors.append(f"找不到 job 檔以更新路徑: {job_id}")
                continue
            _update_job_file(jp, new_abs)
            messages.append(f"updated job JSON: {job_id[:8]}…")

    if rename_orphans:
        for name in orphans_unmatched:
            src = STORYTELLERS_DIR / name
            if not src.is_file():
                continue
            title = _parse_title_from_html(src) or Path(name).stem
            title_part = _safe_html_filename_segment(title, max_len=120) or "untitled"
            sid = _orphan_surrogate_id(name)
            dst = STORYTELLERS_DIR / f"{title_part}_{sid}.html"
            if src.resolve() == dst.resolve():
                continue
            if dst.exists():
                errors.append(f"orphan 目標已存在，略過: {name} -> {dst.name}")
                continue
            if dry_run:
                messages.append(f"orphan rename {name} -> {dst.name}")
            else:
                tmp = dst.parent / f".rename_{uuid.uuid4().hex}.html"
                shutil.move(str(src), str(tmp))
                shutil.move(str(tmp), str(dst))
                messages.append(f"orphan renamed {name} -> {dst.name}")
    else:
        for name in orphans_unmatched:
            errors.append(
                f"未對應任務的檔案（未改名）: {name} — 使用 --rename-orphans 一併改名或手動處理"
            )

    return messages, errors


def main() -> None:
    ap = argparse.ArgumentParser(description="Rename htmls to 論文標題_任務識別號.html")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出將執行的動作，不寫檔",
    )
    ap.add_argument(
        "--no-update-jobs",
        action="store_true",
        help="只搬移/複製 html，不更新 jobs/*.json",
    )
    ap.add_argument(
        "--rename-orphans",
        action="store_true",
        help="對無 jobs 對應的 html 以「標題_檔名雜湊」命名（非 UUID 任務碼）",
    )
    args = ap.parse_args()
    msgs, errs = plan_renames(
        dry_run=args.dry_run,
        update_jobs=not args.no_update_jobs,
        rename_orphans=args.rename_orphans,
    )
    for m in msgs:
        print(m)
    for e in errs:
        print("!", e)


if __name__ == "__main__":
    main()
