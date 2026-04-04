#!/usr/bin/env python3
"""Runtime selection helpers for Python subprocess execution."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_ENV_OVERRIDE_VARS: Tuple[str, ...] = (
    "PAPER_STORYTELLER_PYTHON",
    "STORYTELLER_PYTHON",
)
DEFAULT_FALLBACK_PYTHONS: Tuple[str, ...] = (
    "/home/linuxbrew/.linuxbrew/bin/python3",
)
DEFAULT_PROBE_TIMEOUT_SECONDS = 10


def _normalize_required_modules(required_modules: Iterable[str]) -> Tuple[str, ...]:
    modules: List[str] = []
    seen = set()
    for raw in required_modules:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        modules.append(name)
    return tuple(modules)


def _resolve_executable(raw_value: str) -> Optional[str]:
    value = str(raw_value or "").strip()
    if not value:
        return None

    expanded = Path(value).expanduser()
    if expanded.is_file() and os.access(str(expanded), os.X_OK):
        try:
            return str(expanded.resolve())
        except OSError:
            return str(expanded)

    if "/" not in value:
        resolved = shutil.which(value)
        if resolved:
            try:
                return str(Path(resolved).resolve())
            except OSError:
                return resolved
    return None


def missing_modules_in_current_interpreter(required_modules: Iterable[str]) -> List[str]:
    modules = _normalize_required_modules(required_modules)
    return [name for name in modules if importlib.util.find_spec(name) is None]


def current_interpreter_supports_modules(required_modules: Iterable[str]) -> bool:
    return not missing_modules_in_current_interpreter(required_modules)


def python_can_import_modules(
    python_executable: str,
    required_modules: Iterable[str],
    *,
    probe_timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> bool:
    modules = _normalize_required_modules(required_modules)
    executable = _resolve_executable(python_executable)
    if executable is None:
        return False
    if not modules:
        return True

    probe_code = (
        "import importlib.util\n"
        f"required_modules = {list(modules)!r}\n"
        "missing = [name for name in required_modules if importlib.util.find_spec(name) is None]\n"
        "raise SystemExit(0 if not missing else 1)\n"
    )
    try:
        proc = subprocess.run(
            [executable, "-c", probe_code],
            check=False,
            capture_output=True,
            text=True,
            timeout=probe_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def same_python_executable(left: str, right: str) -> bool:
    left_resolved = _resolve_executable(left)
    right_resolved = _resolve_executable(right)
    if left_resolved is None or right_resolved is None:
        return False
    return left_resolved == right_resolved


def select_preferred_python(
    *,
    required_modules: Iterable[str] = (),
    env_override_vars: Sequence[str] = DEFAULT_ENV_OVERRIDE_VARS,
    fallback_candidates: Sequence[str] = DEFAULT_FALLBACK_PYTHONS,
) -> Dict[str, Any]:
    modules = _normalize_required_modules(required_modules)
    current_python = _resolve_executable(sys.executable) or sys.executable
    current_supports_required = current_interpreter_supports_modules(modules)

    for env_name in env_override_vars:
        raw_override = str(os.environ.get(env_name, "")).strip()
        if not raw_override:
            continue
        resolved_override = _resolve_executable(raw_override)
        if resolved_override and python_can_import_modules(resolved_override, modules):
            return {
                "python_executable": resolved_override,
                "source": "env_override",
                "required_modules": list(modules),
                "supports_required_modules": True,
                "override_env_var": env_name,
                "override_raw_value": raw_override,
                "selection_reason": f"using {env_name} override",
            }

    if current_supports_required:
        return {
            "python_executable": current_python,
            "source": "current",
            "required_modules": list(modules),
            "supports_required_modules": True,
            "override_env_var": "",
            "override_raw_value": "",
            "selection_reason": "current interpreter supports required modules",
        }

    for candidate in fallback_candidates:
        resolved_candidate = _resolve_executable(candidate)
        if not resolved_candidate:
            continue
        if same_python_executable(resolved_candidate, current_python):
            continue
        if python_can_import_modules(resolved_candidate, modules):
            return {
                "python_executable": resolved_candidate,
                "source": "fallback",
                "required_modules": list(modules),
                "supports_required_modules": True,
                "override_env_var": "",
                "override_raw_value": "",
                "selection_reason": "fallback interpreter supports required modules",
            }

    return {
        "python_executable": current_python,
        "source": "current_unverified",
        "required_modules": list(modules),
        "supports_required_modules": current_supports_required,
        "override_env_var": "",
        "override_raw_value": "",
        "selection_reason": "no alternate interpreter with required modules was found",
    }
