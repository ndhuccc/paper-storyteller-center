"""Gemini Developer API via ``google-genai`` SDK (replaces deprecated ``google.generativeai``)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List, Optional

try:
    from google import genai as google_genai
    from google.genai import types as genai_types
except ImportError as exc:  # pragma: no cover - import guard for clearer errors
    raise ImportError(
        "The google-genai package is required. Install with: pip install google-genai"
    ) from exc


def make_client(api_key: str, *, timeout: Optional[int] = None) -> google_genai.Client:
    """Build a sync Client. ``timeout`` is HTTP-layer seconds when set."""
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("Gemini api_key is empty")
    if timeout is not None:
        return google_genai.Client(
            api_key=key,
            http_options=genai_types.HttpOptions(timeout=int(timeout)),
        )
    return google_genai.Client(api_key=key)


def ping_api_key(api_key: str, *, timeout: int = 30) -> None:
    """Lightweight key check (list one model). Raises on auth/network failure."""
    client = make_client(api_key, timeout=int(timeout))
    next(
        iter(
            client.models.list(
                config=genai_types.ListModelsConfig(page_size=1),
            )
        ),
        None,
    )


def list_models_supporting_generate_content(
    api_key: str, *, timeout: int = 60, page_size: int = 100
) -> List[str]:
    """Return model resource names that advertise ``generateContent`` (or Gemini ids as fallback)."""
    client = make_client(api_key, timeout=timeout)
    names: List[str] = []
    pager = client.models.list(
        config=genai_types.ListModelsConfig(page_size=int(page_size)),
    )
    for m in pager:
        name = str(getattr(m, "name", "") or "").strip()
        if not name:
            continue
        actions = list(getattr(m, "supported_actions", None) or [])
        if actions:
            if "generateContent" not in actions:
                continue
        elif "gemini" not in name.lower():
            continue
        names.append(name)
    return names


def wait_for_uploaded_file_active(
    client: google_genai.Client, file_name: str, *, timeout_seconds: int = 90
) -> None:
    deadline = time.time() + max(int(timeout_seconds), 1)
    while time.time() < deadline:
        uploaded = client.files.get(name=file_name)
        state = getattr(uploaded, "state", None)
        state_name = str(getattr(state, "name", state) or "").upper()
        if not state_name or state_name == "ACTIVE":
            return
        if state_name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {file_name}")
        time.sleep(1.5)
    raise TimeoutError(f"Timed out waiting for Gemini file processing: {file_name}")


def upload_file(client: google_genai.Client, path: Path) -> Any:
    return client.files.upload(file=str(path))


def delete_file_quiet(client: google_genai.Client, name: str) -> None:
    try:
        client.files.delete(name=name)
    except Exception:
        pass


def generate_content_text_from_client(
    client: google_genai.Client, *, model: str, contents: Any
) -> str:
    """Run ``generate_content`` on an existing client (e.g. same session as file upload)."""
    response = client.models.generate_content(model=model, contents=contents)
    text = str(getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty content")
    return text


def generate_content_text(
    *,
    api_key: str,
    model: str,
    contents: Any,
    timeout: int,
) -> str:
    """Run ``models.generate_content`` and return stripped text or raise if empty."""
    client = make_client(api_key, timeout=int(timeout))
    return generate_content_text_from_client(client, model=model, contents=contents)
