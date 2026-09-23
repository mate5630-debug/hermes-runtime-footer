#!/usr/bin/env python3
"""Verify the installed derived-image footer without contacting live APIs."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


def load(path: Path):
    spec = importlib.util.spec_from_file_location("derived_runtime_footer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="/opt/hermes")
    args = parser.parse_args()
    root = Path(args.target)
    footer_path = root / "gateway/runtime_footer.py"
    run_turn_path = root / "gateway/run_turn.py"
    footer = load(footer_path)

    class Window:
        label = "Weekly"
        used_percent = 60.0
        reset_at = datetime(2026, 9, 25, 2, 34, 19, tzinfo=timezone.utc)

    class Snapshot:
        windows = (Window(),)

    quota = footer.format_codex_quota(
        Snapshot(), now=datetime(2026, 9, 23, 0, 36, 58, tzinfo=timezone.utc)
    )
    expected = "2s · gpt-5.6-sol · 잔여량 40% (초기화 2일 남음)"
    actual = footer.format_runtime_footer(
        model="openai-codex/gpt-5.6-sol", context_tokens=0, context_length=None,
        turn_seconds=2.2, codex_quota=quota,
        fields=("latency", "model", "codex_quota"),
    )
    if actual != expected:
        raise SystemExit(f"formatter mismatch: {actual!r}")

    async def fake_quota(**_kwargs):
        return "잔여량 40%"

    with mock.patch.object(footer, "get_codex_quota_footer", fake_quota):
        built = asyncio.run(footer.build_footer_line_async(
            user_config={"display": {"runtime_footer": {
                "enabled": True,
                "fields": ["latency", "model", "codex_quota"],
            }}},
            platform_key="slack", provider=None, model="gpt-5.6-sol",
            context_tokens=0, context_length=None, turn_seconds=1.0,
        ))
    if built != "1s · gpt-5.6-sol · 잔여량 40%":
        raise SystemExit(f"async builder mismatch: {built!r}")

    run_turn = run_turn_path.read_text(encoding="utf-8")
    required = (
        "async def _hmwa_runtime_footer_line",
        "build_footer_line_async",
        'provider=agent_result.get("provider")',
        "await self._hmwa_runtime_footer_line",
    )
    missing = [marker for marker in required if marker not in run_turn]
    if missing:
        raise SystemExit(f"run_turn markers missing: {missing}")
    print("runtime footer derived-image verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
