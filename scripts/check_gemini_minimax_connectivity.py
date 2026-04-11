#!/usr/bin/env python3
"""Smoke test: Gemini models/gemini-3.1-flash-lite-preview + MiniMax portal (same paths as storyteller_pipeline)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)
load_dotenv(ROOT / "htmls" / ".env", override=False)
load_dotenv(Path.home() / ".env", override=False)

GEMINI_MODEL = "models/gemini-3.1-flash-lite-preview"
MINIMAX_MODEL = "MiniMax-M2.5"


def main() -> int:
    from storyteller_pipeline import (
        DEFAULT_MINIMAX_PORTAL_BASE_URL,
        _call_minimax_portal_llm,
        _configure_gemini,
    )

    print("=== Gemini:", GEMINI_MODEL, "===")
    api_key = _configure_gemini()
    if not api_key:
        print("FAIL: no working GOOGLE_API_KEY / GEMINI_API_KEY / GEMINI_API_KEY_* (see _configure_gemini)")
        return 1
    try:
        from gemini_client import generate_content_text

        text = generate_content_text(
            api_key=api_key,
            model=GEMINI_MODEL,
            contents="Reply with exactly one word: OK",
            timeout=45,
        )[:200]
        print("PASS:", repr(text))
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1

    print("\n=== MiniMax:", MINIMAX_MODEL, "===")
    token = (
        os.getenv("MINIMAX_PORTAL_OAUTH_TOKEN", "").strip()
        or os.getenv("MINIMAX_OAUTH_TOKEN", "").strip()
    )
    base = (
        os.getenv("MINIMAX_PORTAL_BASE_URL", "").strip().rstrip("/")
        or DEFAULT_MINIMAX_PORTAL_BASE_URL.rstrip("/")
    )
    if not token:
        print("FAIL: missing MINIMAX_PORTAL_OAUTH_TOKEN (or MINIMAX_OAUTH_TOKEN)")
        return 1
    try:
        out = _call_minimax_portal_llm(
            prompt="Reply with exactly one word: OK",
            model=MINIMAX_MODEL,
            oauth_token=token,
            base_url=base,
            timeout=45,
        )
        preview = (out or "").strip()[:200]
        print("PASS:", repr(preview))
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
