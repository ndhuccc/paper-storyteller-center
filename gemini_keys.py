"""Shared Gemini API key discovery: load .env, dedupe candidates, random order, first working key."""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import List

from dotenv import load_dotenv

_PROJECT_DIR = Path(__file__).resolve().parent


def reload_gemini_env() -> None:
    """Reload dotenv files so workers can pick up new keys (order matches storyteller_pipeline)."""
    load_dotenv(_PROJECT_DIR / "htmls" / ".env", override=False)
    load_dotenv(_PROJECT_DIR / ".env", override=False)
    load_dotenv(Path.home() / ".env", override=False)


def unique_gemini_api_key_candidates() -> List[str]:
    """Collect GOOGLE_API_KEY, GEMINI_API_KEY, GEMINI_API_KEY_1..6; dedupe by key string."""
    names = ["GOOGLE_API_KEY", "GEMINI_API_KEY"] + [f"GEMINI_API_KEY_{i}" for i in range(1, 7)]
    seen: set[str] = set()
    out: List[str] = []
    for name in names:
        v = str(os.getenv(name) or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def probe_gemini_key(api_key: str) -> bool:
    key = str(api_key or "").strip()
    if not key:
        return False
    try:
        from gemini_client import ping_api_key

        ping_api_key(key, timeout=30)
        return True
    except Exception:
        return False


def pick_working_gemini_api_key() -> str:
    """
    Try configured keys in random order until list_models probe succeeds.
    Returns empty string if none work.
    """
    reload_gemini_env()
    candidates = unique_gemini_api_key_candidates()
    random.shuffle(candidates)
    for api_key in candidates:
        if probe_gemini_key(api_key):
            return api_key
    return ""
