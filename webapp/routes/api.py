"""All REST API routes for Paper Story Rewriting Center."""
import ipaddress
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from typing import Any, Dict, List, Optional, Tuple

import html2text
import requests
from flask import Blueprint, jsonify, request, Response, send_file

import center_service
from storyteller_pipeline import STYLE_PARAMS
from paper_repository import (
    PAPER_STATUS_READY,
    PAPER_STATUS_GENERATED_NOT_INDEXED,
    PAPER_STATUS_INDEX_ONLY,
    PAPER_STATUS_UNAVAILABLE,
)

bp = Blueprint("api", __name__, url_prefix="/api")

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent  # repo root
STORYTELLERS_DIR = PROJECT_DIR / "htmls"
UPLOADS_DIR = PROJECT_DIR / "uploads"
MAX_UPLOAD_SIZE_MB = 50
UPLOAD_RETENTION_DAYS = 14
UPLOAD_MAX_FILES = 200
MAX_HTML_IMPORT_BYTES = 5 * 1024 * 1024  # 5 MiB
HTML_IMPORT_TIMEOUT_SECONDS = 30
_FETCH_USER_AGENT = (
    "PaperStoryRewritingCenter/1.0 (+https://github.com/) "
    "Mozilla/5.0 (compatible; HTML-import)"
)

STYLE_LABELS: Dict[str, str] = {
    "storyteller": "說書人（生活化類比，重點在「為什麼」）",
    "blog": "科普部落格（鉤子句 + 段落標題 + 結尾留問題）",
    "professor": "大教授（課堂講義 / 可複習）",
    "fairy": "童話故事（知識童話、角色化、寓意對應）",
    "lazy": "懶人包（結論先行、條列重點、快速吸收）",
    "question": "問題驅動（提問引導、逐層拆解、收束答案）",
    "log": "實驗日誌（研究過程記錄、工程師視角）",
}


def _ok(data: Any = None, **kwargs) -> Response:
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload)


def _err(message: str, status: int = 400) -> Response:
    return jsonify({"ok": False, "error": message}), status


def _sanitize(obj: Any) -> Any:
    """Recursively convert numpy/non-JSON-serializable types to Python natives."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    # numpy arrays: ndim==0 means scalar, ndim>0 means array
    if hasattr(obj, "ndim"):
        if obj.ndim == 0:
            return obj.item()          # scalar → Python native
        return [_sanitize(v) for v in obj.tolist()]  # array → list
    # other numpy scalar-like types (e.g. np.float32 without ndarray)
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            return obj
    return obj


def _sanitize_upload_filename(filename: str) -> str:
    name = str(filename or "").strip() or "uploaded.pdf"
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def _cleanup_old_uploaded_pdfs(keep: List[Path] = None) -> Dict[str, int]:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    keep_set = {p.resolve() for p in (keep or [])}
    removed = failed = 0
    now = time.time()
    cutoff = now - (UPLOAD_RETENTION_DAYS * 24 * 60 * 60)

    files = [f for f in UPLOADS_DIR.glob("*.pdf") if f.resolve() not in keep_set]
    for f in files:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            failed += 1

    remaining = sorted(
        [f for f in UPLOADS_DIR.glob("*.pdf") if f.resolve() not in keep_set],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    overflow = max(0, len(remaining) - UPLOAD_MAX_FILES)
    for f in remaining[-overflow:]:
        try:
            f.unlink()
            removed += 1
        except Exception:
            failed += 1

    return {"removed": removed, "failed": failed}


def _as_dict(v: Any) -> Dict:
    return v if isinstance(v, dict) else {}


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def _validate_html_import_url(url: str) -> Tuple[Optional[str], str]:
    """Return (normalized_url, err). err empty means success."""
    raw = str(url or "").strip()
    if not raw:
        return None, "請提供 url"
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None, "僅支援 http 或 https"
    if not parsed.netloc or not parsed.hostname:
        return None, "網址格式不正確"
    host = (parsed.hostname or "").strip().lower()
    if host in ("localhost",) or host.endswith(".local"):
        return None, "不允許此主機名稱"
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        return None, f"無法解析主機：{e}"
    if not infos:
        return None, "無法解析主機位址"
    for _fam, _typ, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not addr.is_global:
            return None, "不允許存取內部、本機或保留位址"
    return raw, ""


def _extract_html_title(html: str) -> str:
    m = re.search(
        r"<title[^>]*>([^<]+)</title>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def _html_to_markdown(html: str) -> str:
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0
    h.unicode_snob = True
    return h.handle(html).strip()


def _fetch_url_bytes_capped(url: str) -> bytes:
    headers = {
        "User-Agent": _FETCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    with requests.get(
        url,
        headers=headers,
        timeout=HTML_IMPORT_TIMEOUT_SECONDS,
        stream=True,
    ) as r:
        r.raise_for_status()
        total = 0
        out: List[bytes] = []
        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_HTML_IMPORT_BYTES:
                raise RuntimeError(
                    f"回應超過大小上限（{MAX_HTML_IMPORT_BYTES // (1024*1024)}MB），無法匯入"
                )
            out.append(chunk)
    return b"".join(out)


def _short_text(text: Any, max_len: int = 80) -> str:
    v = str(text or "").strip()
    if not v:
        return "-"
    return v if len(v) <= max_len else v[: max_len - 3] + "..."


def _first_non_empty(*values: Any) -> str:
    for v in values:
        t = str(v or "").strip()
        if t:
            return t
    return ""


def _extract_warnings(result: Dict) -> List[str]:
    raw = result.get("warnings")
    if isinstance(raw, list):
        return [str(i).strip() for i in raw if str(i).strip()]
    if raw is None:
        return []
    w = str(raw).strip()
    return [w] if w else []


def _error_to_text(item: Any) -> str:
    if isinstance(item, dict):
        stage = str(item.get("stage", "")).strip()
        err_type = str(item.get("type", "")).strip()
        message = str(item.get("message", "")).strip()
        prefix_parts = [p for p in [stage, err_type] if p]
        prefix = ":".join(prefix_parts)
        if prefix and message:
            return f"{prefix} - {message}"
        return prefix or message
    return str(item).strip()


def _extract_errors(result: Dict) -> List[str]:
    raw = result.get("errors")
    if isinstance(raw, list):
        return [t for t in (_error_to_text(i) for i in raw) if t]
    if raw is None:
        return []
    t = _error_to_text(raw)
    return [t] if t else []


def _step_to_text(step: Any) -> str:
    if isinstance(step, dict):
        name = str(step.get("name", step.get("step", step.get("stage", "step")))).strip() or "step"
        status = str(step.get("status", "")).strip()
        note = str(step.get("note", "")).strip()
        if status and note:
            return f"{name} ({status}) - {note}"
        if status:
            return f"{name} ({status})"
        if note:
            return f"{name} - {note}"
        return name
    return str(step).strip()


def _extract_steps(result: Dict) -> List[str]:
    steps = result.get("steps")
    if not isinstance(steps, list):
        steps = _as_dict(result.get("metadata")).get("steps", [])
    if not isinstance(steps, list):
        return []
    return [t for t in (_step_to_text(s) for s in steps) if t]


def _auto_index_state(result: Dict, payload: Dict) -> Dict:
    metadata = _as_dict(result.get("metadata"))
    auto_index = _as_dict(metadata.get("auto_index"))
    requested = auto_index.get("requested")
    if requested is None:
        requested = _as_bool(payload.get("auto_index"))
    mode = str(auto_index.get("mode", "")).strip() or "full_rebuild"
    manifest_resolution = _as_dict(auto_index.get("manifest_resolution"))
    manifest_requested = _as_dict(manifest_resolution.get("requested"))
    manifest_paper = _as_dict(manifest_resolution.get("paper"))
    return {
        "requested": _as_bool(requested),
        "attempted": _as_bool(auto_index.get("attempted")),
        "ok": auto_index.get("ok"),
        "message": str(auto_index.get("message", "")).strip(),
        "mode": mode,
        "state": str(auto_index.get("state", "")).strip(),
        "started_at": str(auto_index.get("started_at", "")).strip(),
        "completed_at": str(auto_index.get("completed_at", "")).strip(),
        "duration_ms": auto_index.get("duration_ms"),
        "manifest_resolution": {
            "attempted": _as_bool(manifest_resolution.get("attempted")),
            "ok": manifest_resolution.get("ok"),
            "message": str(manifest_resolution.get("message", "")).strip(),
            "resolved_paper_id": str(manifest_resolution.get("resolved_paper_id", "")).strip(),
            "match_rule": str(manifest_resolution.get("match_rule", "")).strip(),
            "requested": {
                "paper_id": str(manifest_requested.get("paper_id", "")).strip(),
                "output_path": str(manifest_requested.get("output_path", "")).strip(),
                "filename": str(manifest_requested.get("filename", "")).strip(),
            },
            "paper": manifest_paper,
        },
    }


def _extract_generation_output_filename(result: Dict, output: Dict) -> str:
    direct = _first_non_empty(output.get("filename"), result.get("filename"))
    if direct:
        return Path(direct).name
    for artifact in (result.get("artifacts") or []):
        if not isinstance(artifact, dict):
            continue
        if str(artifact.get("type", "")).strip().lower() != "html":
            continue
        fn = _first_non_empty(artifact.get("filename"))
        if fn:
            return Path(fn).name
        ap = _first_non_empty(artifact.get("path"))
        if ap:
            return Path(ap).name
    return ""


def _extract_generation_output_paper_id(payload: Dict, result: Dict, output: Dict) -> str:
    metadata = _as_dict(result.get("metadata"))
    auto_index = _as_dict(metadata.get("auto_index"))
    manifest_resolution = _as_dict(auto_index.get("manifest_resolution"))
    manifest_paper = _as_dict(manifest_resolution.get("paper"))
    return _first_non_empty(
        result.get("paper_id"),
        output.get("paper_id"),
        manifest_resolution.get("resolved_paper_id"),
        manifest_paper.get("paper_id"),
        metadata.get("paper_id"),
        payload.get("paper_id"),
    )


def _engine_label_map(scope: str) -> Dict[str, str]:
    options = (
        center_service.get_qa_engine_options()
        if scope == "qa"
        else center_service.get_generation_engine_options()
    )
    return {
        str(item.get("id", "")).strip(): str(item.get("label", "")).strip()
        for item in options
        if str(item.get("id", "")).strip()
    }


def _normalize_engine_order(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    result: List[str] = []
    seen = set()
    for item in raw:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        result.append(value)
        seen.add(value)
    return result


def _build_job_summary(job: Dict) -> Dict:
    payload = _as_dict(job.get("payload"))
    result = _as_dict(job.get("result"))
    output = _as_dict(result.get("output"))
    style = str(payload.get("style", "-")).strip() or "-"
    concise_level = payload.get("concise_level", 6)
    anti_repeat_level = payload.get("anti_repeat_level", 6)
    gemini_preflight_enabled = payload.get("gemini_preflight_enabled", True)
    gemini_preflight_timeout_seconds = payload.get("gemini_preflight_timeout_seconds", 30)
    gemini_rewrite_timeout_seconds = payload.get("gemini_rewrite_timeout_seconds", 140)
    rewrite_fallback_timeout_seconds = payload.get("rewrite_fallback_timeout_seconds", 180)
    style_params = payload.get("style_params") if isinstance(payload.get("style_params"), dict) else {}
    rewrite_engine_order = _normalize_engine_order(payload.get("rewrite_engine_order"))
    generation_engine_labels = _engine_label_map("generation")
    ai_state = _auto_index_state(result=result, payload=payload)
    warnings = _extract_warnings(result)
    errors = _extract_errors(result)
    steps = _extract_steps(result)
    sections_generated = result.get("sections_generated") or _as_dict(result.get("metadata")).get("sections_generated")
    section_rewrite_stats = result.get("section_rewrite_stats")
    if not isinstance(section_rewrite_stats, list):
        meta = _as_dict(result.get("metadata"))
        section_rewrite_stats = meta.get("section_rewrite_stats")
    if not isinstance(section_rewrite_stats, list):
        section_rewrite_stats = []
    rewrite_wall_seconds_total = result.get("rewrite_wall_seconds_total")
    if rewrite_wall_seconds_total is None:
        rewrite_wall_seconds_total = _as_dict(result.get("metadata")).get(
            "rewrite_wall_seconds_total"
        )
    integrate_subchunk_flag = result.get("integrate_subchunk_rewrites")
    if integrate_subchunk_flag is None:
        integrate_subchunk_flag = payload.get("integrate_subchunk_rewrites")
    integrate_subchunk_rewrites_bool = (
        bool(integrate_subchunk_flag) if integrate_subchunk_flag is not None else True
    )
    rewrite_formula_retry_flag = result.get("rewrite_formula_retry")
    if rewrite_formula_retry_flag is None:
        rewrite_formula_retry_flag = _as_dict(result.get("metadata")).get("rewrite_formula_retry")
    if rewrite_formula_retry_flag is None:
        rewrite_formula_retry_flag = payload.get("rewrite_formula_retry")
    rewrite_formula_retry_bool = (
        bool(rewrite_formula_retry_flag) if rewrite_formula_retry_flag is not None else True
    )
    subchunk_integrate_sections_done = result.get("subchunk_integrate_sections")
    if subchunk_integrate_sections_done is None:
        subchunk_integrate_sections_done = _as_dict(result.get("metadata")).get(
            "subchunk_integrate_sections"
        )
    rewrite_model = result.get("model") or _as_dict(result.get("metadata")).get("model") or "-"
    rewrite_model_primary = result.get("rewrite_model_primary") or payload.get("model") or "-"
    pdf_extraction_model = result.get("pdf_extraction_model") or _as_dict(result.get("metadata")).get("pdf_extraction_model") or "-"
    output_path = result.get("output_path") or output.get("output_path")
    output_filename = _extract_generation_output_filename(result=result, output=output)
    output_paper_id = _extract_generation_output_paper_id(payload=payload, result=result, output=output)
    job_id = str(job.get("job_id", "")).strip()
    paper_title = str(payload.get("paper_title") or payload.get("title") or "").strip()
    if not paper_title:
        fn = (output_filename or "").strip()
        if not fn and output_path:
            fn = Path(str(output_path)).name
        if fn and job_id and fn.lower().endswith(".html"):
            suf = f"_{job_id}.html"
            if fn.endswith(suf):
                paper_title = fn[: -len(suf)].strip()
    phase_detail = job.get("phase_detail")
    if not isinstance(phase_detail, dict):
        phase_detail = None

    return {
        "job_id": job_id,
        "status": str(job.get("status", "-")),
        "phase": job.get("phase") or "",
        "phase_detail": phase_detail,
        "pdf_path": str(payload.get("pdf_path", payload.get("source_pdf_path", "-"))),
        "paper_title": paper_title,
        "style": style,
        "style_label": STYLE_LABELS.get(style, style),
        "style_params": style_params,
        "concise_level": concise_level,
        "anti_repeat_level": anti_repeat_level,
        "gemini_preflight_enabled": gemini_preflight_enabled,
        "gemini_preflight_timeout_seconds": gemini_preflight_timeout_seconds,
        "gemini_rewrite_timeout_seconds": gemini_rewrite_timeout_seconds,
        "rewrite_fallback_timeout_seconds": rewrite_fallback_timeout_seconds,
        "output_path": str(output_path or "-"),
        "output_filename": output_filename,
        "output_paper_id": output_paper_id,
        "sections_generated": sections_generated,
        "section_rewrite_stats": section_rewrite_stats,
        "rewrite_wall_seconds_total": rewrite_wall_seconds_total,
        "integrate_subchunk_rewrites": integrate_subchunk_rewrites_bool,
        "subchunk_integrate_sections": subchunk_integrate_sections_done,
        "rewrite_formula_retry": rewrite_formula_retry_bool,
        "rewrite_model": rewrite_model,
        "rewrite_model_primary": rewrite_model_primary,
        "rewrite_engine_order": rewrite_engine_order,
        "rewrite_engine_labels": [
            generation_engine_labels.get(engine_id, engine_id)
            for engine_id in rewrite_engine_order
        ],
        "pdf_extraction_model": pdf_extraction_model,
        "warnings": warnings,
        "warnings_count": len(warnings),
        "errors": errors,
        "errors_count": len(errors),
        "auto_index_state": ai_state,
        "auto_index_mode": ai_state.get("mode", "full_rebuild"),
        "auto_index_state_name": ai_state.get("state") or "-",
        "auto_index_started_at": ai_state.get("started_at") or "-",
        "auto_index_completed_at": ai_state.get("completed_at") or "-",
        "auto_index_duration_ms": ai_state.get("duration_ms"),
        "steps": steps,
        "updated_at": str(job.get("updated_at", "-")),
    }


# ───────────────────── Papers ─────────────────────

@bp.route("/papers", methods=["GET"])
def list_papers():
    try:
        papers = center_service.get_all_papers()
        return _ok(papers)
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/papers/<paper_id>/html", methods=["GET"])
def get_paper_html(paper_id: str):
    try:
        html = center_service.load_html(paper_id)
        return Response(html, mimetype="text/html")
    except Exception as e:
        return _err(str(e), 404)


@bp.route("/papers/<paper_id>/download", methods=["GET"])
def download_paper_html(paper_id: str):
    try:
        normalized_id = str(paper_id or "").strip()
        if "_chunk_" in normalized_id:
            normalized_id = normalized_id.rsplit("_chunk_", 1)[0]
        if not normalized_id:
            return _err("paper_id is required")

        html_path = STORYTELLERS_DIR / f"{normalized_id}.html"
        if not html_path.exists() or not html_path.is_file():
            return _err("HTML not found", 404)

        return send_file(
            html_path,
            mimetype="text/html",
            as_attachment=True,
            download_name=html_path.name,
        )
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/papers/<paper_id>", methods=["DELETE"])
def delete_paper(paper_id: str):
    try:
        result = center_service.delete_paper(paper_id)
        return _ok(result)
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/papers/<paper_id>/rename", methods=["PATCH"])
def rename_paper(paper_id: str):
    data = request.get_json(force=True) or {}
    new_name = str(data.get("new_name", "")).strip()
    if not new_name:
        return _err("new_name is required")
    try:
        result = center_service.rename_paper(paper_id, new_name)
        if not result.get("ok"):
            return _err(result.get("message", "重新命名失敗"), 400)
        return _ok(result)
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/papers/<paper_id>/display-name", methods=["PATCH"])
def update_paper_display_name(paper_id: str):
    data = request.get_json(force=True) or {}
    display_name = str(data.get("display_name", "")).strip()
    if not display_name:
        return _err("display_name is required")
    try:
        result = center_service.update_paper_display_name(paper_id, display_name)
        if not result.get("ok"):
            return _err(result.get("message", "更新顯示名稱失敗"), 400)
        return _ok(result)
    except Exception as e:
        return _err(str(e), 500)


# ───────────────────── Search ─────────────────────

@bp.route("/search", methods=["POST"])
def search():
    data = request.get_json(force=True) or {}
    query = str(data.get("query", "")).strip()
    if not query:
        return _err("query is required")
    top_k = int(data.get("top_k", 10))
    threshold = float(data.get("threshold", 0.0))
    try:
        results = center_service.search(query, top_k=top_k, similarity_threshold=threshold)
        return _ok(_sanitize(results))
    except Exception as e:
        return _err(str(e), 500)


# ───────────────────── Q&A ─────────────────────

@bp.route("/answer", methods=["POST"])
def answer():
    data = request.get_json(force=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return _err("question is required")
    forced_papers = data.get("forced_papers") or None
    engine_order = _normalize_engine_order(data.get("engine_order"))
    try:
        detail = center_service.answer_detailed(
            question=question,
            forced_papers=forced_papers,
            engine_order=engine_order,
        )
        return _ok(detail)
    except Exception as e:
        return _err(str(e), 500)


# ───────────────────── Index ─────────────────────

@bp.route("/index/rebuild", methods=["POST"])
def rebuild_index():
    try:
        ok = center_service.rebuild_index()
        if ok:
            return _ok({"message": "索引重建完成"})
        return _err("索引重建失敗", 500)
    except Exception as e:
        return _err(str(e), 500)


# ───────────────────── Generation Jobs ─────────────────────

@bp.route("/jobs", methods=["GET"])
def list_jobs():
    # limit=0 或 all：回傳全部任務（供前端分頁）；未帶參數預設 20 筆
    raw = request.args.get("limit", "20")
    lim: Any
    if str(raw).lower() in ("all", "none"):
        lim = None
    else:
        try:
            lim = int(raw)
        except (TypeError, ValueError):
            lim = 20
    if lim == 0:
        lim = None
    try:
        jobs = center_service.list_generation_jobs(limit=lim)
        summaries = [_build_job_summary(j) for j in jobs]
        return _ok(summaries)
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    try:
        job = center_service.get_generation_job(job_id)
        if job is None:
            return _err("Job not found", 404)
        summary = _build_job_summary(job)
        # Try to resolve manifest paper
        all_papers = center_service.get_all_papers()
        resolution = center_service.resolve_generation_manifest_paper(
            output_path=summary.get("output_path", ""),
            filename=summary.get("output_filename", ""),
            paper_id=summary.get("output_paper_id", ""),
            manifest_papers=all_papers,
        )
        manifest_paper = None
        if isinstance(resolution, dict):
            raw_paper = resolution.get("paper")
            if isinstance(raw_paper, dict):
                manifest_paper = center_service.normalize_paper(raw_paper)
        summary["manifest_paper"] = manifest_paper
        summary["paper_ready"] = bool(manifest_paper) and center_service.is_paper_ready(manifest_paper)
        return _ok(summary)
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/jobs/<job_id>/retry", methods=["POST"])
def retry_job(job_id: str):
    try:
        result = center_service.retry_generation_job(job_id)
        if result is None:
            return _err("retry failed", 500)
        return _ok({"message": "重試任務已送出"})
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id: str):
    try:
        result = center_service.cancel_generation_job(job_id)
        if result is None:
            return _err("cancel failed", 500)
        return _ok({"message": "任務已取消"})
    except Exception as e:
        return _err(str(e), 500)


@bp.route("/jobs/submit", methods=["POST"])
def submit_job():
    """Submit + launch a generation job. Accepts multipart (with PDF) or JSON."""
    saved_path = None
    cleanup_result = None

    integrate_subchunk_rewrites = True
    rewrite_formula_retry = True
    if request.content_type and "multipart" in request.content_type:
        # File upload path
        pdf_file = request.files.get("pdf")
        if pdf_file:
            size = len(pdf_file.read())
            pdf_file.seek(0)
            max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
            if size > max_bytes:
                return _err(f"檔案過大（{size / (1024*1024):.1f}MB），上限 {MAX_UPLOAD_SIZE_MB}MB")
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = _sanitize_upload_filename(getattr(pdf_file, "filename", "uploaded.pdf"))
            target = UPLOADS_DIR / f"{uuid4().hex[:12]}_{safe_name}"
            pdf_file.save(str(target))
            saved_path = str(target.resolve())
            cleanup_result = _cleanup_old_uploaded_pdfs(keep=[target])
        style = request.form.get("style", "storyteller")
        auto_index = _as_bool(request.form.get("auto_index", "true"))
        concise_level = request.form.get("concise_level", 6)
        anti_repeat_level = request.form.get("anti_repeat_level", 6)
        gemini_preflight_enabled = _as_bool(request.form.get("gemini_preflight_enabled", "true"))
        gemini_preflight_timeout_seconds = request.form.get("gemini_preflight_timeout_seconds", 30)
        gemini_rewrite_timeout_seconds = request.form.get("gemini_rewrite_timeout_seconds", 140)
        rewrite_fallback_timeout_seconds = request.form.get("rewrite_fallback_timeout_seconds", "180")
        integrate_subchunk_rewrites = _as_bool(
            request.form.get("integrate_subchunk_rewrites", "true")
        )
        rewrite_formula_retry = _as_bool(request.form.get("rewrite_formula_retry", "true"))
        pdf_path_field = request.form.get("pdf_path", "").strip()
    else:
        data = request.get_json(force=True) or {}
        style = str(data.get("style", "storyteller")).strip()
        auto_index = _as_bool(data.get("auto_index", True))
        concise_level = data.get("concise_level", 6)
        anti_repeat_level = data.get("anti_repeat_level", 6)
        gemini_preflight_enabled = _as_bool(data.get("gemini_preflight_enabled", True))
        gemini_preflight_timeout_seconds = data.get("gemini_preflight_timeout_seconds", 30)
        gemini_rewrite_timeout_seconds = data.get("gemini_rewrite_timeout_seconds", 140)
        rewrite_fallback_timeout_seconds = data.get("rewrite_fallback_timeout_seconds", 180)
        integrate_subchunk_rewrites = _as_bool(data.get("integrate_subchunk_rewrites", True))
        rewrite_formula_retry = _as_bool(data.get("rewrite_formula_retry", True))
        pdf_path_field = str(data.get("pdf_path", "")).strip()

    # Parse style_params (JSON-encoded string in multipart, or dict in JSON body)
    import json as _json
    style_params: dict = {}
    manual_sections_raw = None
    paper_title_manual = ""
    paper_title_form = ""
    engine_order: List[str] = []
    if request.content_type and "multipart" in request.content_type:
        paper_title_form = str(request.form.get("paper_title", "")).strip()
        raw_sp = request.form.get("style_params", "")
        if raw_sp:
            try:
                style_params = _json.loads(raw_sp)
            except Exception:
                pass
        raw_engine_order = request.form.get("engine_order", "")
        if raw_engine_order:
            try:
                engine_order = _normalize_engine_order(_json.loads(raw_engine_order))
            except Exception:
                engine_order = []
    else:
        data2 = request.get_json(force=True, silent=True) or {}
        sp_raw = data2.get("style_params", {})
        if isinstance(sp_raw, dict):
            style_params = sp_raw
        ms = data2.get("manual_sections")
        if isinstance(ms, list):
            manual_sections_raw = ms
        paper_title_manual = str(data2.get("paper_title", "")).strip()
        engine_order = _normalize_engine_order(data2.get("engine_order"))

    resolved_pdf_path = saved_path or pdf_path_field
    if not resolved_pdf_path and not manual_sections_raw:
        return _err("請提供 PDF 檔案或路徑，或使用手動輸入改寫單元")

    if manual_sections_raw is not None:
        if not paper_title_manual:
            return _err("請填寫論文標題")
    elif resolved_pdf_path:
        if request.content_type and "multipart" in request.content_type:
            if not paper_title_form:
                return _err("請填寫論文標題")
        elif not paper_title_manual:
            return _err("請填寫論文標題")

    try:
        resolved_engines = center_service.resolve_generation_engine_chain(engine_order)
        payload = {
            "pdf_path": resolved_pdf_path or "",
            "style": style,
            "auto_index": auto_index,
            "concise_level": concise_level,
            "anti_repeat_level": anti_repeat_level,
            "gemini_preflight_enabled": gemini_preflight_enabled,
            "gemini_preflight_timeout_seconds": gemini_preflight_timeout_seconds,
            "gemini_rewrite_timeout_seconds": gemini_rewrite_timeout_seconds,
            "rewrite_fallback_timeout_seconds": rewrite_fallback_timeout_seconds,
            "integrate_subchunk_rewrites": integrate_subchunk_rewrites,
            "rewrite_formula_retry": rewrite_formula_retry,
            "style_params": style_params,
            "model": resolved_engines["primary_model"],
            "rewrite_primary_provider": resolved_engines.get("primary_provider") or "",
            "vertex_project": resolved_engines.get("primary_vertex_project") or "",
            "vertex_location": resolved_engines.get("primary_vertex_location") or "",
            "rewrite_fallback_chain": resolved_engines["fallbacks"],
            "rewrite_engine_order": resolved_engines["ordered_ids"],
        }
        if manual_sections_raw is not None:
            payload["manual_sections"] = manual_sections_raw
        if manual_sections_raw is not None:
            payload["paper_title"] = paper_title_manual
        elif resolved_pdf_path:
            if request.content_type and "multipart" in request.content_type:
                payload["paper_title"] = paper_title_form
            else:
                payload["paper_title"] = paper_title_manual
        job = center_service.submit_generation_job(payload=payload)
        job_id = str(job.get("job_id", "")).strip()
        if not job_id:
            return _err("job_id missing in submit response", 500)
        launched = center_service.launch_generation_job(job_id)
        if launched is None:
            return _err("launch failed", 500)
        return _ok({
            "job_id": job_id,
            "message": "任務已提交",
            "saved_path": saved_path,
            "cleanup": cleanup_result,
        })
    except Exception as e:
        return _err(str(e), 500)


# ───────────────────── HTML import (URL → Markdown for manual units) ─────────────────────

@bp.route("/html/import", methods=["POST"])
def import_html_url():
    """Fetch a public HTTP(S) page and convert HTML body to Markdown for batch manual input."""
    data = request.get_json(force=True) or {}
    url = str(data.get("url", "")).strip()
    norm, err = _validate_html_import_url(url)
    if not norm:
        return _err(err, 400)
    try:
        raw = _fetch_url_bytes_capped(norm)
    except requests.HTTPError as e:
        st = e.response.status_code if e.response is not None else "?"
        return _err(f"無法下載（HTTP {st}）", 502)
    except requests.RequestException as e:
        return _err(f"無法下載：{e}", 502)
    except RuntimeError as e:
        return _err(str(e), 400)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    page_title = _extract_html_title(text)
    try:
        markdown = _html_to_markdown(text)
    except Exception as e:
        return _err(f"轉成 Markdown 失敗：{e}", 500)
    return _ok({"markdown": markdown, "page_title": page_title})


# ───────────────────── PDF Scan (preview sections before rewrite) ─────────────────────

@bp.route("/pdf/scan", methods=["POST"])
def pdf_scan():
    """Extract and return sections from an uploaded PDF without starting a rewrite job.

    The frontend uses this to let users review / edit the extracted rewrite units
    before submitting a full generation job.
    """
    from storyteller_pipeline import _extract_pdf_text, _split_into_sections  # local import to avoid circular issues at module load

    pdf_file = request.files.get("pdf")
    if not pdf_file:
        return _err("請提供 PDF 檔案")
    filename = pdf_file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        return _err("僅支援 PDF 檔案")

    safe_name = re.sub(r"[^\w.\-]", "_", filename)[:120]
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOADS_DIR / f"scan_{uuid4().hex[:12]}_{safe_name}"
    pdf_file.save(str(target))

    try:
        extracted_text, warning, model_used = _extract_pdf_text(target)
        sections = _split_into_sections(extracted_text)
        # Derive a human-readable default paper title from the filename
        paper_title = re.sub(r"[_\-]+", " ", safe_name.removesuffix(".pdf")).strip()
        return _ok({
            "paper_title": paper_title,
            "sections": [
                {"title": s["title"], "body": s["source_text"]}
                for s in sections
            ],
            "extraction_model": model_used,
            "warning": warning,
        })
    except Exception as e:
        return _err(str(e), 500)
    finally:
        try:
            target.unlink()
        except Exception:
            pass


# ───────────────────── Meta ─────────────────────

@bp.route("/styles", methods=["GET"])
def list_styles():
    return _ok([
        {"key": k, "label": v, "params": STYLE_PARAMS.get(k, [])}
        for k, v in STYLE_LABELS.items()
    ])


@bp.route("/engines", methods=["GET"])
def list_engines():
    return _ok({
        "qa": center_service.get_qa_engine_options(),
        "generation": center_service.get_generation_engine_options(),
    })


@bp.route("/health", methods=["GET"])
def health():
    return _ok({"status": "ok"})
