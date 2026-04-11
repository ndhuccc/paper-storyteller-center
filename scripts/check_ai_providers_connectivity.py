#!/usr/bin/env python3
"""逐一檢測管線會用到的 AI provider：Gemini、Ollama、MiniMax。

載入 .env 順序與 storyteller_pipeline / gemini_keys 一致。
不會把 API Key 印到螢幕；僅報告「是否已設定」與錯誤型別／訊息。

用法：
  python3 scripts/check_ai_providers_connectivity.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "htmls" / ".env", override=False)
load_dotenv(ROOT / ".env", override=False)
load_dotenv(Path.home() / ".env", override=False)

GEMINI_MODEL = "models/gemini-3.1-flash-lite-preview"
MINIMAX_MODEL = "MiniMax-M2.5"


def _missing_gemini_sdk_help() -> Optional[str]:
    """If legacy Gemini SDK is not importable, return a help string; else None."""
    try:
        from gemini_client import available_backends

        backends = available_backends()
    except Exception as exc:
        backends = []
        backend_note = f"\n匯入 gemini_client 失敗：{type(exc).__name__}: {exc}"
    else:
        backend_note = ""

    if backends:
        return None

    venv_hint = ROOT / ".venv" / "bin" / "python3"
    venv_line = (
        f"  建議：{venv_hint} scripts/check_ai_providers_connectivity.py\n"
        if venv_hint.is_file()
        else "  建議：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
    )
    return (
        "目前這個 `python3` **未安裝舊版 Gemini SDK（google.generativeai）**，因此還沒打到 Google API，"
        "與金鑰好壞無關。\n"
        + venv_line
        + "  請安裝：pip install google-generativeai\n"
        + "若遇 PEP 668，請勿對系統 Python 硬裝，改用專案 .venv。\n"
        + "安裝完成後再跑本腳本，才會進入各金鑰 list_models 連線測試。"
        + backend_note
    )


def _env_key_status() -> Dict[str, bool]:
    names = ["GOOGLE_API_KEY", "GEMINI_API_KEY"] + [f"GEMINI_API_KEY_{i}" for i in range(1, 7)]
    return {n: bool(str(os.getenv(n) or "").strip()) for n in names}


def _check_gemini() -> Tuple[bool, str]:
    sdk_help = _missing_gemini_sdk_help()
    if sdk_help:
        return False, sdk_help

    from gemini_keys import named_gemini_api_candidates, probe_gemini_key_detail, reload_gemini_env
    from storyteller_pipeline import _gemini_rewrite_preflight

    reload_gemini_env()
    flags = _env_key_status()
    set_names = [k for k, v in flags.items() if v]
    if not set_names:
        return (
            False,
            "未偵測到任何 Gemini 金鑰環境變數（GOOGLE_API_KEY、GEMINI_API_KEY、GEMINI_API_KEY_1…6 皆空）。\n"
            "請在專案根目錄或 htmls/.env、~/.env 設定至少一組有效金鑰後再測。",
        )

    named = named_gemini_api_candidates()
    lines: List[str] = ["--- 各環境變數對應之「相異金鑰」list_models 探測（不重複測同一字串）---"]
    key: str = ""
    for env_name, candidate_key in named:
        ok_probe, err_detail = probe_gemini_key_detail(candidate_key)
        if ok_probe:
            lines.append(f"  {env_name}: PASS（list_models 可連線）")
            key = candidate_key
            break
        lines.append(f"  {env_name}: FAIL — {err_detail}")

    if not key:
        lines.append(
            "\n（以上皆失敗時，pick_working_gemini_api_key 也會回傳空字串；請依各 FAIL 訊息修正金鑰、網路或代理。）"
        )
        body = "\n".join(lines)
        if all(
            "google-generativeai" in line or "ImportError" in line
            for line in lines
            if line.strip().startswith("  ")
        ):
            body += (
                "\n\n提示：若每一行都是 ImportError / google-generativeai，代表執行當下仍缺套件；"
                "請先完成安裝後再測金鑰（見本節開頭說明）。"
            )
        return False, body

    ok, reason = _gemini_rewrite_preflight(model=GEMINI_MODEL, timeout=20)
    if not ok:
        return (
            False,
            "已取得可用金鑰，但 preflight（極短 generate）失敗：\n"
            f"  {reason}\n"
            "常見原因：金鑰權限不足、模型 ID 對該專案不可用、逾時、或地區限制。",
        )

    try:
        from gemini_client import generate_content_text

        text = generate_content_text(
            api_key=key,
            model=GEMINI_MODEL,
            contents="Reply with exactly one word: OK",
            timeout=45,
        )
        if not text.strip():
            return False, "Gemini 回傳空字串（異常）。"
        return True, f"preflight + generate_content 成功；回覆預覽: {text.strip()[:80]!r}"
    except Exception as exc:
        return False, f"preflight 通過但正式 generate 失敗: {type(exc).__name__}: {exc}"


def _ollama_list_models(base: str) -> Tuple[Optional[List[str]], Optional[str]]:
    url = f"{base.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        models = []
        for row in data.get("models", []) or []:
            name = str(row.get("name", "") or "").strip()
            if name:
                models.append(name)
        return models, None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return None, f"URLError: {exc.reason}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _check_ollama(base_url: str, model: str) -> Tuple[bool, str]:
    from storyteller_pipeline import _call_local_llm

    models, err = _ollama_list_models(base_url)
    if err:
        return (
            False,
            f"無法連到 Ollama `{base_url}`（先打 GET /api/tags）。\n"
            f"  原因: {err}\n"
            "請確認本機已執行 `ollama serve`、埠號正確（預設 11434），且無防火牆／代理擋住。",
        )

    if model not in models:
        hint = ", ".join(models[:12]) + ("…" if len(models) > 12 else "")
        return (
            False,
            f"Ollama 可連線，但 **未安裝** 所需模型 `{model}`。\n"
            f"  /api/tags 目前有的名稱（節錄）: {hint or '（無）'}\n"
            f"  請執行: ollama pull {model}",
        )

    try:
        out = _call_local_llm(
            prompt="Reply with exactly one word: OK",
            model=model,
            ollama_base_url=base_url.rstrip("/"),
            timeout=25,
        )
        if not (out or "").strip():
            return False, "/api/generate 成功回應但 response 欄位為空（模型或參數異常）。"
        return True, f"/api/tags 與 /api/generate 皆成功；預覽: {(out or '').strip()[:80]!r}"
    except Exception as exc:
        return (
            False,
            f"已列出模型且含 `{model}`，但 /api/generate 失敗: {type(exc).__name__}: {exc}\n"
            "若為 TimeoutError：代表推論過慢或 GPU 忙碌，可拉長 timeout 或換較小模型。",
        )


def _check_minimax() -> Tuple[bool, str]:
    from storyteller_pipeline import DEFAULT_MINIMAX_PORTAL_BASE_URL, _call_minimax_portal_llm

    token = (
        os.getenv("MINIMAX_PORTAL_OAUTH_TOKEN", "").strip()
        or os.getenv("MINIMAX_OAUTH_TOKEN", "").strip()
    )
    base = (
        os.getenv("MINIMAX_PORTAL_BASE_URL", "").strip().rstrip("/")
        or DEFAULT_MINIMAX_PORTAL_BASE_URL.rstrip("/")
    )
    if not token:
        return (
            False,
            "未設定 MINIMAX_PORTAL_OAUTH_TOKEN（或 MINIMAX_OAUTH_TOKEN）。\n"
            "管線的 MiniMax 呼叫需要 Bearer token；未設定則 fallback 會直接失敗。",
        )

    try:
        out = _call_minimax_portal_llm(
            prompt="Reply with exactly one word: OK",
            model=MINIMAX_MODEL,
            oauth_token=token,
            base_url=base,
            timeout=45,
        )
        preview = (out or "").strip()[:120]
        return True, f"API 成功；base={base}；回覆預覽: {preview!r}"
    except Exception as exc:
        msg = str(exc)
        detail = (
            "\n說明：MiniMax `chatcompletion_pro`（及部分 v2 情境）常要求 JSON 內含 **bot_setting**（機器人名稱與人設／系統描述陣列）。\n"
            "目前 storyteller_pipeline._call_minimax_portal_llm 僅送 model、messages、temperature，"
            "若官方改為必填 bot_setting，就會出現 `code=2013` / `missing required parameter`。\n"
            "解法：在程式 payload 補上符合文件的 bot_setting，或改打只要求 messages 的端點／版本。"
        )
        if "2013" in msg or "bot_setting" in msg:
            return False, f"{type(exc).__name__}: {msg}{detail}"
        return False, f"{type(exc).__name__}: {msg}"


def main() -> int:
    from storyteller_pipeline import DEFAULT_OLLAMA_BASE_URL, DEFAULT_REWRITE_FALLBACK_CHAIN

    print("=== 1) Google Gemini（Developer API）===\n")
    ok, note = _check_gemini()
    print("狀態:", "PASS" if ok else "FAIL")
    print(note)
    print()

    print("=== 2) Ollama（本機 /api/generate）===\n")
    seen: set[Tuple[str, str]] = set()
    ollama_specs: List[Dict[str, Any]] = []
    for spec in DEFAULT_REWRITE_FALLBACK_CHAIN:
        if (spec.get("provider") or "").strip().lower() != "ollama":
            continue
        base = str(spec.get("ollama_base_url") or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        model = str(spec.get("model") or "")
        key = (base, model)
        if key in seen or not model:
            continue
        seen.add(key)
        ollama_specs.append({"base": base, "model": model})

    ollama_ok_all = True
    if not ollama_specs:
        print("（fallback 鏈中無 ollama 項目）\n")
    else:
        for i, spec in enumerate(ollama_specs, start=1):
            base, model = spec["base"], spec["model"]
            print(f"--- 2.{i}) base={base} model={model} ---")
            ok_o, note_o = _check_ollama(base, model)
            if not ok_o:
                ollama_ok_all = False
            print("狀態:", "PASS" if ok_o else "FAIL")
            print(note_o)
            print()

    print("=== 3) MiniMax Portal（api.minimax.io）===\n")
    ok_m, note_m = _check_minimax()
    print("狀態:", "PASS" if ok_m else "FAIL")
    print(note_m)

    return 0 if ok and ok_m and ollama_ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
