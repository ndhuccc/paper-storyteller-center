#!/usr/bin/env python3
"""Gemini PDF extraction smoke test.

Usage:
    python3 test_gemini_pdf_scan.py --pdf /path/to/file.pdf

If --pdf is omitted, this script tries to create a tiny sample PDF with PyMuPDF
and sends it to Gemini for extraction.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from typing import List, Optional

import google.generativeai as genai
from dotenv import load_dotenv

from storyteller_pipeline import GEMINI_EXTRACTION_PROMPT
from storyteller_pipeline import _split_into_sections


DEFAULT_MODEL = "models/gemini-3.1-flash-lite-preview"
DEFAULT_TIMEOUT = 90
DEFAULT_RETRIES = 2
MAX_RETRY_SLEEP_SECONDS = 30
STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"


def _is_key_working(api_key: str) -> bool:
    key = str(api_key or "").strip()
    if not key:
        return False
    try:
        genai.configure(api_key=key)
        # Quick probe to validate the key against the active API endpoint.
        next(genai.list_models(page_size=1), None)
        return True
    except Exception:
        return False


def load_env() -> str:
    load_dotenv(dotenv_path=STORYTELLERS_DIR / ".env", override=False)
    load_dotenv(dotenv_path=Path.home() / ".env", override=False)
    candidates = [
        str(os.getenv("GOOGLE_API_KEY") or "").strip(),
        str(os.getenv("GEMINI_API_KEY") or "").strip(),
    ]
    seen = set()
    for key in candidates:
        if not key or key in seen:
            continue
        seen.add(key)
        if _is_key_working(key):
            return key
    return ""


def create_sample_pdf(path: Path) -> Path:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF (fitz) is required to auto-create sample PDF") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((72, 96), "Gemini PDF Scan Smoke Test", fontsize=18)
    page.insert_text((72, 130), "Equation: E = mc^2", fontsize=12)
    page.insert_text((72, 154), "Chinese: 這是一份 Gemini 掃描測試文件。", fontsize=12)
    page.insert_text((72, 178), "Goal: verify upload + extraction pipeline.", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def wait_for_file_active(file_name: str, timeout_sec: int) -> None:
    deadline = time.time() + max(timeout_sec, 1)
    while time.time() < deadline:
        uploaded = genai.get_file(file_name)
        state_name = str(getattr(getattr(uploaded, "state", None), "name", "")).upper()
        if not state_name or state_name == "ACTIVE":
            return
        if state_name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed: {file_name}")
        time.sleep(1.5)
    raise TimeoutError(f"Timed out waiting Gemini file active: {file_name}")


def pick_available_model(preferred: str) -> str:
    preferred_name = str(preferred or "").strip()
    available: List[str] = []

    for m in genai.list_models():
        name = str(getattr(m, "name", "") or "").strip()
        methods = list(getattr(m, "supported_generation_methods", []) or [])
        if not name or "generateContent" not in methods:
            continue
        available.append(name)

    if preferred_name and preferred_name in available:
        return preferred_name

    candidates = [
        "models/gemini-2.0-flash",
        "models/gemini-2.5-flash",
        "models/gemini-flash-latest",
    ]
    for candidate in candidates:
        if candidate in available:
            return candidate

    if available:
        return available[0]

    raise RuntimeError("No Gemini models available for generateContent")


def _retry_delay_from_error(exc: Exception) -> Optional[int]:
    message = str(exc)
    marker = "Please retry in "
    idx = message.find(marker)
    if idx == -1:
        return None
    tail = message[idx + len(marker):]
    digits = []
    for ch in tail:
        if ch.isdigit() or ch == ".":
            digits.append(ch)
        else:
            break
    if not digits:
        return None
    try:
        seconds = float("".join(digits))
        return max(1, int(seconds))
    except ValueError:
        return None


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("resourceexhausted" in text) or ("quota exceeded" in text) or ("429" in text)


def _print_quota_diagnostics(exc: Exception) -> None:
    text = str(exc)
    print("[DIAG] Gemini quota/rate limit detected.")
    hints = [
        "quota exceeded",
        "free_tier_requests",
        "free_tier_input_token_count",
        "retry in",
    ]
    lowered = text.lower()
    for hint in hints:
        if hint in lowered:
            print(f"[DIAG] Matched hint: {hint}")


def extract_with_pymupdf(pdf_path: Path) -> str:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF not available for fallback extraction") from exc

    doc = fitz.open(str(pdf_path))
    try:
        lines = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                lines.append(text)
        return "\n".join(lines).strip()
    finally:
        doc.close()


def count_pdf_pages(pdf_path: Path) -> int:
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PyMuPDF not available for page counting") from exc

    doc = fitz.open(str(pdf_path))
    try:
        return int(getattr(doc, "page_count", len(doc)))
    finally:
        doc.close()


def count_sections(extracted_text: str) -> int:
    text = str(extracted_text or "").strip()
    if not text:
        return 0

    try:
        return len(_split_into_sections(text))
    except Exception:
        headings = re.findall(r"(?m)^#{1,6}\s+.+$", text)
        if headings:
            return len(headings)
        blocks = [block for block in re.split(r"\n\s*\n+", text) if block.strip()]
        return len(blocks)


def run_test(pdf_path: Path, model: str, timeout_sec: int, retries: int) -> str:
    sample_file = genai.upload_file(path=str(pdf_path))
    try:
        wait_for_file_active(sample_file.name, timeout_sec=timeout_sec)
        llm = genai.GenerativeModel(model)
        attempts = max(1, retries + 1)
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                try:
                    resp = llm.generate_content([sample_file, GEMINI_EXTRACTION_PROMPT], request_options={"timeout": 180})
                except TypeError:
                    resp = llm.generate_content([sample_file, GEMINI_EXTRACTION_PROMPT])
                text = str(getattr(resp, "text", "") or "").strip()
                if not text:
                    raise RuntimeError("Gemini returned empty extraction text")
                return text
            except Exception as exc:
                last_exc = exc
                if _is_quota_error(exc):
                    _print_quota_diagnostics(exc)
                if attempt >= attempts:
                    break
                sleep_sec = _retry_delay_from_error(exc)
                if sleep_sec is None:
                    sleep_sec = min(12, 3 * attempt)
                sleep_sec = max(1, min(MAX_RETRY_SLEEP_SECONDS, sleep_sec))
                print(f"[WARN] Attempt {attempt}/{attempts} failed, retry after {sleep_sec}s")
                time.sleep(sleep_sec)

        raise RuntimeError(f"Gemini extraction failed after retries: {last_exc}")
    finally:
        try:
            genai.delete_file(sample_file.name)
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini PDF scan smoke test")
    parser.add_argument("--pdf", dest="pdf", default="", help="Path to test PDF")
    parser.add_argument("--model", dest="model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", dest="timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", dest="retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument(
        "--offline",
        dest="offline",
        action="store_true",
        help="Skip Gemini and test only local PyMuPDF extraction/section splitting.",
    )
    parser.add_argument(
        "--no-fallback",
        dest="no_fallback",
        action="store_true",
        help="Disable PyMuPDF fallback verification when Gemini fails",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    pdf_arg = str(args.pdf).strip()
    pdf_path = Path(pdf_arg).expanduser() if pdf_arg else Path()
    if pdf_arg:
        if not pdf_path.exists() or not pdf_path.is_file():
            print(f"[FAIL] PDF not found: {pdf_path}")
            return 3
        target_pdf = pdf_path
    else:
        target_pdf = STORYTELLERS_DIR / "_gemini_smoke_test.pdf"
        try:
            create_sample_pdf(target_pdf)
        except Exception as exc:
            print(f"[FAIL] Could not create sample PDF: {exc}")
            return 3

    try:
        page_count = count_pdf_pages(target_pdf)
    except Exception as exc:
        print(f"[WARN] Could not count PDF pages: {exc}")
        page_count = -1

    print(f"[INFO] Using PDF: {target_pdf}")
    if page_count >= 0:
        print(f"[INFO] PDF pages: {page_count}")

    if args.offline:
        try:
            extracted = extract_with_pymupdf(target_pdf)
        except Exception as exc:
            print(f"[FAIL] Offline PyMuPDF extraction failed: {type(exc).__name__}: {exc}")
            return 1

        section_count = count_sections(extracted)
        print("[PASS] Offline PyMuPDF extraction succeeded.")
        print(f"[INFO] Extracted chars: {len(extracted)}")
        print(f"[INFO] Sections detected: {section_count}")
        print("----- preview begin -----")
        print(extracted[:800])
        print("----- preview end -----")
        return 0

    api_key = load_env()
    if not api_key:
        print("[FAIL] GOOGLE_API_KEY/GEMINI_API_KEY not found in environment/.env")
        return 2

    try:
        selected_model = pick_available_model(args.model)
    except Exception as exc:
        print(f"[FAIL] Could not select Gemini model: {exc}")
        return 4

    print(f"[INFO] Requested model: {args.model}")
    print(f"[INFO] Selected model: {selected_model}")

    try:
        extracted = run_test(
            target_pdf,
            model=selected_model,
            timeout_sec=args.timeout,
            retries=max(0, int(args.retries)),
        )
    except Exception as exc:
        print(f"[FAIL] Gemini extraction failed: {type(exc).__name__}: {exc}")
        if args.no_fallback:
            return 1

        print("[INFO] Running PyMuPDF fallback verification...")
        try:
            local_text = extract_with_pymupdf(target_pdf)
        except Exception as fallback_exc:
            print(f"[FAIL] PyMuPDF fallback also failed: {type(fallback_exc).__name__}: {fallback_exc}")
            return 1

        if not local_text:
            print("[FAIL] PyMuPDF fallback produced empty text")
            return 1

        local_sections = count_sections(local_text)

        print("[PASS] PyMuPDF fallback extraction succeeded.")
        print(f"[INFO] Fallback extracted chars: {len(local_text)}")
        print(f"[INFO] Fallback sections detected: {local_sections}")
        print("----- fallback preview begin -----")
        print(local_text[:800])
        print("----- fallback preview end -----")
        return 1

    preview = extracted[:800]
    section_count = count_sections(extracted)
    print("[PASS] Gemini extraction succeeded.")
    print(f"[INFO] Extracted chars: {len(extracted)}")
    print(f"[INFO] Sections detected: {section_count}")
    print("----- preview begin -----")
    print(preview)
    print("----- preview end -----")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
