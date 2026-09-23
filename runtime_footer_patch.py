#!/usr/bin/env python3
"""Apply the Codex quota runtime-footer patch to a Hermes image tree."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


class PatchError(RuntimeError):
    pass


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def _patch_runtime_footer(text: str) -> tuple[str, str]:
    if "async def build_footer_line_async" in text and '"codex_quota": lambda: codex_quota or ""' in text:
        return text, "already_patched"

    text = _replace_once(
        text,
        "import os\nfrom typing import Any, Iterable, Optional",
        "import asyncio\nimport logging\nimport os\nimport time\nfrom datetime import datetime, timezone\nfrom typing import Any, Iterable, Optional",
        "runtime_footer imports",
    )
    text = _replace_once(
        text,
        '_SEP = " · "\n',
        '_SEP = " · "\n_CODEX_QUOTA_CACHE: tuple[float, str] = (0.0, "")\nlogger = logging.getLogger(__name__)\n',
        "runtime_footer constants",
    )

    quota_block = '''def format_codex_quota(snapshot: Any, *, now: Optional[datetime] = None) -> str:
    """Render the active Codex allowance in the compact Korean footer form."""
    if snapshot is None:
        return ""
    windows = tuple(getattr(snapshot, "windows", ()) or ())
    preferred = ("session", "weekly")
    window = next((w for label in preferred for w in windows
                   if str(getattr(w, "label", "")).strip().lower() == label), None)
    if window is None and windows:
        window = windows[0]
    used = getattr(window, "used_percent", None) if window is not None else None
    if not isinstance(used, (int, float)):
        return ""
    remaining = max(0, min(100, round(100 - float(used))))
    text = f"잔여량 {remaining}%"
    reset_at = getattr(window, "reset_at", None)
    if not isinstance(reset_at, datetime):
        return text
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    seconds = max(0, int((reset_at - current).total_seconds()))
    if seconds >= 86400:
        interval = f"{seconds // 86400}일 남음"
    elif seconds >= 3600:
        interval = f"{seconds // 3600}시간 남음"
    elif seconds >= 60:
        interval = f"{seconds // 60}분 남음"
    else:
        interval = "곧 초기화"
    return f"{text} (초기화 {interval})"


def clear_codex_quota_cache() -> None:
    global _CODEX_QUOTA_CACHE
    _CODEX_QUOTA_CACHE = (0.0, "")


async def get_codex_quota_footer(*, timeout: float = 2.0, cache_ttl: float = 300.0) -> str:
    """Fetch Codex quota off-thread with a wall-clock bound; fail open."""
    global _CODEX_QUOTA_CACHE
    cached_at, cached_value = _CODEX_QUOTA_CACHE
    if cached_value and time.monotonic() - cached_at < max(0.0, cache_ttl):
        return cached_value
    try:
        from agent.account_usage import _fetch_codex_account_usage
        snapshot = await asyncio.wait_for(
            asyncio.to_thread(_fetch_codex_account_usage), timeout=max(0.01, timeout),
        )
        value = format_codex_quota(snapshot)
        if value:
            _CODEX_QUOTA_CACHE = (time.monotonic(), value)
        return value
    except Exception:
        logger.debug("Codex runtime-footer quota fetch failed (fail-open)", exc_info=True)
        return ""


'''
    text = _replace_once(
        text,
        "def format_runtime_footer(*, model: Optional[str]",
        quota_block + "def format_runtime_footer(*, model: Optional[str]",
        "quota formatter insertion",
    )
    text = _replace_once(
        text,
        "                          turn_seconds: Optional[float] = None,\n                          requested_model: Optional[str] = None, served_model: Optional[str] = None,",
        "                          turn_seconds: Optional[float] = None,\n                          codex_quota: Optional[str] = None,\n                          requested_model: Optional[str] = None, served_model: Optional[str] = None,",
        "format_runtime_footer signature",
    )
    text = _replace_once(
        text,
        "    renderers = {\n        \"model\": lambda: _model_short(model),",
        "    renderers = {\n        \"codex_quota\": lambda: codex_quota or \"\",\n        \"model\": lambda: _model_short(model),",
        "quota renderer",
    )
    text = _replace_once(
        text,
        "                      cwd: Optional[str] = None, turn_seconds: Optional[float] = None,\n                      requested_model: Optional[str] = None, served_model: Optional[str] = None) -> str:",
        "                      cwd: Optional[str] = None, turn_seconds: Optional[float] = None,\n                      codex_quota: Optional[str] = None,\n                      requested_model: Optional[str] = None, served_model: Optional[str] = None) -> str:",
        "build_footer_line signature",
    )
    text = _replace_once(
        text,
        "                                 context_length=context_length, cwd=cwd, turn_seconds=turn_seconds,\n                                 requested_model=requested_model, served_model=served_model,",
        "                                 context_length=context_length, cwd=cwd, turn_seconds=turn_seconds,\n                                 codex_quota=codex_quota,\n                                 requested_model=requested_model, served_model=served_model,",
        "build_footer_line quota forwarding",
    )

    async_builder = '''

async def build_footer_line_async(*, user_config: dict[str, Any] | None, platform_key: str | None,
                                  provider: Optional[str], model: Optional[str], context_tokens: int,
                                  context_length: Optional[int], cwd: Optional[str] = None,
                                  turn_seconds: Optional[float] = None,
                                  requested_model: Optional[str] = None, served_model: Optional[str] = None,
                                  quota_timeout: float = 2.0) -> str:
    """Async gateway entry point; quota I/O never blocks the event loop."""
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    fields = cfg.get("fields") or _DEFAULT_FIELDS
    quota = ""
    if "codex_quota" in fields:
        # The field itself is the operator's explicit opt-in.  Do not depend on the
        # result dict carrying a provider value: some gateway delivery paths omit it.
        # The account-usage reader fails open when no Codex login is available.
        quota = await get_codex_quota_footer(timeout=quota_timeout)
    return format_runtime_footer(
        model=model, context_tokens=context_tokens, context_length=context_length,
        cwd=cwd, turn_seconds=turn_seconds, codex_quota=quota,
        requested_model=requested_model, served_model=served_model, fields=fields,
    )
'''
    text = text.rstrip() + async_builder + "\n"
    return text, "patched"


def _patch_run_turn(text: str) -> tuple[str, str]:
    if "async def _hmwa_runtime_footer_line" in text and "build_footer_line_async" in text:
        return text, "already_patched"

    text = _replace_once(
        text,
        "    def _hmwa_runtime_footer_line(self, agent_result, source, _turn_seconds):",
        "    async def _hmwa_runtime_footer_line(self, agent_result, source, _turn_seconds):",
        "run_turn async method",
    )
    text = _replace_once(
        text,
        "            from gateway.runtime_footer import build_footer_line as _bfl\n            return _bfl(",
        "            from gateway.runtime_footer import build_footer_line_async as _bfl\n            return await _bfl(",
        "run_turn async builder",
    )
    text = _replace_once(
        text,
        "                platform_key=_platform_config_key(source.platform), model=agent_result.get(\"model\"),",
        "                platform_key=_platform_config_key(source.platform), provider=agent_result.get(\"provider\"),\n                model=agent_result.get(\"model\"),",
        "run_turn provider forwarding",
    )
    text = _replace_once(
        text,
        "_footer_line = self._hmwa_runtime_footer_line(agent_result, source, _turn_seconds)",
        "_footer_line = await self._hmwa_runtime_footer_line(agent_result, source, _turn_seconds)",
        "run_turn await caller",
    )
    return text, "patched"


def _atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode & 0o777
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def apply_patch(target: str | Path) -> dict[str, str]:
    root = Path(target)
    footer_path = root / "gateway/runtime_footer.py"
    turn_path = root / "gateway/run_turn.py"
    if not footer_path.is_file() or not turn_path.is_file():
        raise PatchError(f"target is not a Hermes install tree: {root}")

    original_footer = footer_path.read_text(encoding="utf-8")
    original_turn = turn_path.read_text(encoding="utf-8")
    patched_footer, footer_state = _patch_runtime_footer(original_footer)
    patched_turn, turn_state = _patch_run_turn(original_turn)

    compile(patched_footer, str(footer_path), "exec")
    compile(patched_turn, str(turn_path), "exec")

    if footer_state == "patched":
        _atomic_write(footer_path, patched_footer)
    if turn_state == "patched":
        _atomic_write(turn_path, patched_turn)
    return {"runtime_footer": footer_state, "run_turn": turn_state}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="/opt/hermes")
    args = parser.parse_args()
    try:
        result = apply_patch(args.target)
    except PatchError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "target": args.target, "files": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
