#!/usr/bin/env python3
"""比對專案內設定的模型 ID 是否為各 provider 目前可辨識／可用（不印出金鑰）。

- Gemini：list_models（支援 generateContent 者）＋可選極短 generate 試探
- Ollama：GET /api/tags
- MiniMax：以既有 _call_minimax_portal_llm 極短試探（需 .env token）

用法：
  .venv/bin/python3 scripts/verify_project_model_names.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "htmls" / ".env", override=False)
load_dotenv(ROOT / ".env", override=False)
load_dotenv(Path.home() / ".env", override=False)


def _gemini_model_suffix(name: str) -> str:
    n = str(name or "").strip()
    if n.startswith("models/"):
        return n[7:]
    return n


def _collect_gemini_targets() -> List[str]:
    from center_service import QA_FALLBACK_CHAIN, QA_PRIMARY_ENGINE
    from storyteller_pipeline import (
        DEFAULT_PDF_EXTRACTION_MODEL,
        DEFAULT_REWRITE_FALLBACK_CHAIN,
        DEFAULT_REWRITE_MODEL,
        PDF_EXTRACTION_FALLBACK_MODEL,
    )

    seen: Set[str] = set()
    out: List[str] = []
    for raw in (
        DEFAULT_REWRITE_MODEL,
        DEFAULT_PDF_EXTRACTION_MODEL,
        PDF_EXTRACTION_FALLBACK_MODEL,
        QA_PRIMARY_ENGINE.get("model", ""),
        *[str(s.get("model", "")).strip() for s in QA_FALLBACK_CHAIN],
        *[str(s.get("model", "")).strip() for s in DEFAULT_REWRITE_FALLBACK_CHAIN],
    ):
        m = str(raw or "").strip()
        if not m or not m.startswith("models/"):
            continue
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def _gemini_list_names(api_key: str) -> Tuple[Optional[Set[str]], str]:
    try:
        from gemini_client import list_models_supporting_generate_content

        names = list_models_supporting_generate_content(api_key, timeout=90, page_size=256)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return set(names), ""


def _gemini_resolve(canonical: Set[str], target: str) -> Tuple[str, str]:
    """Return (status, detail). status one of EXACT, ALIAS, MISSING."""
    if target in canonical:
        return "EXACT", ""
    suffix = _gemini_model_suffix(target)
    for c in canonical:
        if _gemini_model_suffix(c) == suffix:
            return "ALIAS", f"catalog has {c!r} (same suffix as {target!r})"
    # fuzzy: any catalog id contains suffix as substring
    hits = [c for c in canonical if suffix and suffix in _gemini_model_suffix(c)]
    if len(hits) == 1:
        return "FUZZY", f"only similar catalog id: {hits[0]!r}"
    if hits:
        return "FUZZY", f"similar catalog ids (show up to 5): {hits[:5]!r}"
    return "MISSING", "no catalog name with same suffix"


def _ollama_tags(base: str) -> Tuple[Optional[Set[str]], str]:
    url = f"{base.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    names: Set[str] = set()
    for row in data.get("models", []) or []:
        n = str(row.get("name", "") or "").strip()
        if n:
            names.add(n)
    return names, ""


def _collect_ollama_models() -> List[Tuple[str, str]]:
    from storyteller_pipeline import DEFAULT_OLLAMA_BASE_URL, DEFAULT_REWRITE_FALLBACK_CHAIN

    out: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for spec in DEFAULT_REWRITE_FALLBACK_CHAIN:
        if (spec.get("provider") or "").strip().lower() != "ollama":
            continue
        base = str(spec.get("ollama_base_url") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        model = str(spec.get("model") or "").strip()
        key = (base, model)
        if model and key not in seen:
            seen.add(key)
            out.append((base, model))
    return out


def main() -> int:
    from storyteller_pipeline import (
        DEFAULT_MINIMAX_PORTAL_BASE_URL,
        PDF_EXTRACTION_FALLBACK_MODEL,
        _call_minimax_portal_llm,
    )

    exit_code = 0
    print("=== Gemini（list_models + 可選 generate 試探）===\n")
    try:
        from gemini_keys import pick_working_gemini_api_key, reload_gemini_env
        from gemini_client import generate_content_text

        reload_gemini_env()
        key = pick_working_gemini_api_key()
        if not key:
            print("FAIL: 無可用 Gemini 金鑰（pick_working_gemini_api_key 為空）\n")
            exit_code = 1
        else:
            canonical, err = _gemini_list_names(key)
            if canonical is None:
                print(f"FAIL: 無法列出模型 — {err}\n")
                exit_code = 1
            else:
                targets = _collect_gemini_targets()
                # PDF fallback 可能是短 id（無 models/ 前綴），一併檢查
                extra = str(PDF_EXTRACTION_FALLBACK_MODEL or "").strip()
                if extra and not extra.startswith("models/"):
                    targets.append(f"models/{extra}")
                print(f"catalog 內含 generateContent 的模型數: {len(canonical)}\n")
                for t in targets:
                    status, detail = _gemini_resolve(canonical, t)
                    gen_note = ""
                    if status in ("EXACT", "ALIAS") and key:
                        try:
                            generate_content_text(
                                api_key=key,
                                model=t,
                                contents="Reply exactly: OK",
                                timeout=35,
                            )
                            gen_note = " | generate: OK"
                        except Exception as exc:
                            gen_note = f" | generate: FAIL ({type(exc).__name__}: {exc})"
                            exit_code = 1
                    line = f"  {t!r} → {status}{gen_note}"
                    if detail:
                        line += f" ({detail})"
                    print(line)
                # PDF 萃取 fallback 常為短 id（與 list 的 models/ 全名不同），單獨試 generate
                from storyteller_pipeline import PDF_EXTRACTION_FALLBACK_MODEL as pdf_fb

                raw_fb = str(pdf_fb or "").strip()
                if raw_fb:
                    print(f"\n  （PDF 萃取 fallback 原始字串）{raw_fb!r}")
                    fb_cands: List[str] = [raw_fb]
                    if not raw_fb.startswith("models/"):
                        fb_cands.append(f"models/{raw_fb}")
                    ok_fb = False
                    for cand in fb_cands:
                        try:
                            generate_content_text(
                                api_key=key,
                                model=cand,
                                contents="Reply exactly: OK",
                                timeout=35,
                            )
                            print(f"    generate({cand!r}) → OK")
                            ok_fb = True
                            break
                        except Exception as exc:
                            print(f"    generate({cand!r}) → FAIL {type(exc).__name__}: {exc}")
                    if not ok_fb:
                        exit_code = 1
                print()
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}\n")
        exit_code = 1

    print("=== Ollama（/api/tags）===\n")
    for base, model in _collect_ollama_models():
        tags, err = _ollama_tags(base)
        if tags is None:
            print(f"  base={base} → FAIL list: {err}")
            exit_code = 1
            continue
        ok = model in tags
        print(f"  base={base} model={model!r} → {'OK' if ok else 'MISSING'}")
        if not ok:
            sample = ", ".join(sorted(tags)[:15])
            print(f"    （tags 節錄）{sample}{'…' if len(tags) > 15 else ''}")
            exit_code = 1
    print()

    print("=== MiniMax（chatcompletion 極短試探）===\n")
    token = (
        os.getenv("MINIMAX_PORTAL_OAUTH_TOKEN", "").strip()
        or os.getenv("MINIMAX_OAUTH_TOKEN", "").strip()
    )
    base = (
        os.getenv("MINIMAX_PORTAL_BASE_URL", "").strip().rstrip("/")
        or DEFAULT_MINIMAX_PORTAL_BASE_URL.rstrip("/")
    )
    if not token:
        print("  SKIP: 未設定 MINIMAX_PORTAL_OAUTH_TOKEN / MINIMAX_OAUTH_TOKEN\n")
    else:
        from storyteller_pipeline import DEFAULT_REWRITE_FALLBACK_CHAIN

        mm_models = [
            str(s.get("model", "")).strip()
            for s in DEFAULT_REWRITE_FALLBACK_CHAIN
            if (s.get("provider") or "").strip().lower() in {"minimax.io", "minimax", "minimax-portal"}
        ]
        mm_models = list(dict.fromkeys(m for m in mm_models if m))
        for m in mm_models:
            try:
                out = _call_minimax_portal_llm(
                    prompt="Reply exactly: OK",
                    model=m,
                    oauth_token=token,
                    base_url=base,
                    timeout=45,
                )
                preview = (out or "").strip()[:40]
                print(f"  model={m!r} base={base!r} → OK preview={preview!r}")
            except Exception as exc:
                print(f"  model={m!r} base={base!r} → FAIL {type(exc).__name__}: {exc}")
                exit_code = 1
        print()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
