"""Vertex AI (GCP) Gemini text generation — optional backend alongside API-key Gemini."""

from __future__ import annotations

import os
from typing import Any


def normalize_vertex_model_id(model: str) -> str:
    """Turn UI / legacy IDs into Vertex GenerativeModel model id."""
    m = str(model or "").strip()
    if not m:
        raise ValueError("empty Vertex model id")
    if m.startswith("models/"):
        return m[len("models/") :].strip() or m
    if m.startswith("vertex/"):
        return m.split("/", 1)[1].strip() or m
    return m


def resolve_vertex_project(explicit: str) -> str:
    pid = str(explicit or "").strip()
    if pid:
        return pid
    for key in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "VERTEX_PROJECT"):
        v = str(os.getenv(key) or "").strip()
        if v:
            return v
    return ""


def resolve_vertex_location(explicit: str) -> str:
    """Gemini 3.x preview on Vertex 需使用 ``global``；各引擎 spec 的 vertex_location 會優先於環境變數。"""
    loc = str(explicit or "").strip()
    if loc:
        return loc
    for key in ("VERTEX_LOCATION", "VERTEX_AI_LOCATION", "GOOGLE_CLOUD_REGION"):
        v = str(os.getenv(key) or "").strip()
        if v:
            return v
    return "us-central1"


def vertex_generate_content_text(
    *,
    project_id: str,
    location: str,
    model_id: str,
    contents: str,
    timeout: int,
) -> str:
    """Call Vertex Gemini and return plain text (raises on empty or import/config errors)."""
    try:
        import vertexai  # type: ignore[import-untyped]
        from vertexai.generative_models import GenerativeModel  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - env specific
        raise RuntimeError(
            "Vertex AI 需要安裝 google-cloud-aiplatform（例如：pip install google-cloud-aiplatform）"
        ) from exc

    pid = resolve_vertex_project(project_id)
    if not pid:
        raise RuntimeError(
            "Vertex AI 缺少 GCP 專案 ID：請在引擎設定填 vertex_project，或設定環境變數 "
            "GOOGLE_CLOUD_PROJECT / GCP_PROJECT / VERTEX_PROJECT，並完成 Application Default Credentials。"
        )
    loc = resolve_vertex_location(location)
    mid = normalize_vertex_model_id(model_id)

    vertexai.init(project=pid, location=loc)
    model = GenerativeModel(mid)
    req_opts: dict[str, Any] = {"timeout": int(max(timeout, 1))}
    try:
        response = model.generate_content(str(contents or ""), request_options=req_opts)
    except TypeError:
        # Older SDKs may not accept request_options the same way
        response = model.generate_content(str(contents or ""))

    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text

    # Fallback: walk candidates for text parts
    cand = getattr(response, "candidates", None) or []
    parts: list[str] = []
    for c in cand:
        content = getattr(c, "content", None)
        prts = getattr(content, "parts", None) if content is not None else None
        if not prts:
            continue
        for p in prts:
            t = str(getattr(p, "text", "") or "").strip()
            if t:
                parts.append(t)
    joined = "\n".join(parts).strip()
    if joined:
        return joined

    raise RuntimeError("Vertex AI returned empty content")


def vertex_preflight_ping(*, project_id: str, location: str, model_id: str, timeout: int) -> tuple[bool, str]:
    """Tiny generation to verify Vertex credentials and model availability."""
    try:
        vertex_generate_content_text(
            project_id=project_id,
            location=location,
            model_id=model_id,
            contents="Reply with exactly: OK",
            timeout=min(int(timeout), 60),
        )
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
