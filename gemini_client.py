"""Gemini legacy client wrapper.

This project currently pins Gemini access to the legacy
``google.generativeai`` SDK because it matches the previously working behavior
in this environment. The modern ``google.genai`` path is intentionally excluded.
"""

from __future__ import annotations

import importlib
import time
import warnings
from pathlib import Path
from typing import Any, Iterable, List, Optional

_LEGACY_IMPORT_DONE = False
_LEGACY_SDK = None
_LEGACY_IMPORT_ERROR: Optional[Exception] = None


def _load_legacy_sdk():
    global _LEGACY_IMPORT_DONE, _LEGACY_SDK, _LEGACY_IMPORT_ERROR
    if _LEGACY_IMPORT_DONE:
        return _LEGACY_SDK
    _LEGACY_IMPORT_DONE = True
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            _LEGACY_SDK = importlib.import_module("google.generativeai")
    except Exception as exc:  # pragma: no cover - import environment specific
        _LEGACY_IMPORT_ERROR = exc
        _LEGACY_SDK = None
    return _LEGACY_SDK


def available_backends() -> List[str]:
    return ["legacy"] if _load_legacy_sdk() is not None else []


def _legacy_help_message() -> str:
    hints = [
        "Gemini SDK 不可用。此專案目前已明確固定為舊版 google.generativeai。",
        "請安裝：pip install google-generativeai",
        "若系統 Python 遇到 PEP 668 externally-managed-environment，請改用專案 .venv：",
        "  .venv/bin/pip install -r requirements.txt",
        "  .venv/bin/python3 <script.py>",
    ]
    if _LEGACY_IMPORT_ERROR is not None:
        hints.append(
            f"legacy import error: {type(_LEGACY_IMPORT_ERROR).__name__}: {_LEGACY_IMPORT_ERROR}"
        )
    return "\n".join(hints)


class _LegacyModelsAdapter:
    def __init__(self, client: "GeminiLegacyClient") -> None:
        self._client = client

    def list(self, *, page_size: int = 100) -> Iterable[Any]:
        sdk = self._client._legacy_sdk
        self._client._configure_legacy()
        kwargs = {"page_size": int(page_size)} if page_size else {}
        return sdk.list_models(**kwargs)

    def generate_content(self, *, model: str, contents: Any) -> Any:
        sdk = self._client._legacy_sdk
        self._client._configure_legacy()
        gm = sdk.GenerativeModel(model)
        try:
            return gm.generate_content(
                contents,
                request_options={"timeout": int(self._client.timeout)},
            )
        except TypeError:
            return gm.generate_content(contents)


class _LegacyFilesAdapter:
    def __init__(self, client: "GeminiLegacyClient") -> None:
        self._client = client

    def upload(self, *, file: str) -> Any:
        sdk = self._client._legacy_sdk
        self._client._configure_legacy()
        return sdk.upload_file(path=str(file))

    def get(self, *, name: str) -> Any:
        sdk = self._client._legacy_sdk
        self._client._configure_legacy()
        return sdk.get_file(name)

    def delete(self, *, name: str) -> None:
        sdk = self._client._legacy_sdk
        self._client._configure_legacy()
        sdk.delete_file(name)


class GeminiLegacyClient:
    def __init__(self, *, api_key: str, timeout: Optional[int] = None) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise ValueError("Gemini api_key is empty")
        self.timeout = int(timeout) if timeout is not None else 240
        self.backend = "legacy"
        self._legacy_sdk = _load_legacy_sdk()
        if self._legacy_sdk is None:
            raise ImportError(_legacy_help_message())
        self.models = _LegacyModelsAdapter(self)
        self.files = _LegacyFilesAdapter(self)

    def _configure_legacy(self) -> None:
        self._legacy_sdk.configure(api_key=self.api_key)


def make_client(api_key: str, *, timeout: Optional[int] = None) -> GeminiLegacyClient:
    """Build a Gemini client using only legacy google.generativeai."""
    return GeminiLegacyClient(api_key=api_key, timeout=timeout)


def ping_api_key(api_key: str, *, timeout: int = 30) -> None:
    """Lightweight key check (list one model). Raises on auth/network failure."""
    client = make_client(api_key, timeout=int(timeout))
    next(iter(client.models.list(page_size=1)), None)


def list_models_supporting_generate_content(
    api_key: str, *, timeout: int = 60, page_size: int = 100
) -> List[str]:
    """Return legacy Gemini model names that support generateContent."""
    client = make_client(api_key, timeout=timeout)
    names: List[str] = []
    for m in client.models.list(page_size=int(page_size)):
        name = str(getattr(m, "name", "") or "").strip()
        methods = list(getattr(m, "supported_generation_methods", []) or [])
        if not name or "generateContent" not in methods:
            continue
        names.append(name)
    return names


def wait_for_uploaded_file_active(
    client: GeminiLegacyClient, file_name: str, *, timeout_seconds: int = 90
) -> None:
    deadline = time.time() + max(int(timeout_seconds), 1)
    while time.time() < deadline:
        uploaded = client.files.get(name=file_name)
        state_name = str(getattr(getattr(uploaded, "state", None), "name", "")).upper()
        if not state_name or state_name == "ACTIVE":
            return
        if state_name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {file_name}")
        time.sleep(1.5)
    raise TimeoutError(f"Timed out waiting for Gemini file processing: {file_name}")


def upload_file(client: GeminiLegacyClient, path: Path) -> Any:
    return client.files.upload(file=str(path))


def delete_file_quiet(client: GeminiLegacyClient, name: str) -> None:
    try:
        client.files.delete(name=name)
    except Exception:
        pass


def generate_content_text_from_client(
    client: GeminiLegacyClient, *, model: str, contents: Any
) -> str:
    """Run generate_content on an existing legacy client."""
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
    """Run Gemini content generation via legacy SDK and return stripped text."""
    client = make_client(api_key, timeout=int(timeout))
    return generate_content_text_from_client(client, model=model, contents=contents)
