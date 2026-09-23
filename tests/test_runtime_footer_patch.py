from __future__ import annotations

import asyncio
import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

try:
    from runtime_footer_patch import PatchError, apply_patch
except ModuleNotFoundError:
    PatchError = RuntimeError
    apply_patch = None


BASE_RUNTIME_FOOTER = '''"""fixture"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_SEP = " · "


def _home_relative_cwd(cwd: str) -> str:
    return cwd


def _model_short(model: Optional[str]) -> str:
    return model.rsplit("/", 1)[-1] if model else ""


def _env_cwd() -> str:
    return ""


def resolve_footer_config(user_config: dict[str, Any] | None, platform_key: str | None = None) -> dict[str, Any]:
    return {"enabled": True, "fields": list((user_config or {}).get("fields", _DEFAULT_FIELDS))}


def _format_latency(seconds: float) -> str:
    if seconds < 1:
        return "<1s"
    return f"{int(round(seconds))}s"


def format_runtime_footer(*, model: Optional[str], context_tokens: int,
                          context_length: Optional[int], cwd: Optional[str] = None,
                          turn_seconds: Optional[float] = None,
                          requested_model: Optional[str] = None, served_model: Optional[str] = None,
                          fields: Iterable[str] = _DEFAULT_FIELDS) -> str:
    def context_pct() -> str:
        return ""

    def served() -> str:
        return ""

    renderers = {
        "model": lambda: _model_short(model),
        "served_model": served,
        "context_pct": context_pct,
        "latency": lambda: _format_latency(turn_seconds) if turn_seconds is not None and turn_seconds >= 0 else "",
        "cwd": lambda: _home_relative_cwd(cwd or _env_cwd()),
    }
    return _SEP.join(v for field in fields if (render := renderers.get(field)) and (v := render()))


def build_footer_line(*, user_config: dict[str, Any] | None, platform_key: str | None,
                      model: Optional[str], context_tokens: int, context_length: Optional[int],
                      cwd: Optional[str] = None, turn_seconds: Optional[float] = None,
                      requested_model: Optional[str] = None, served_model: Optional[str] = None) -> str:
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    return format_runtime_footer(model=model, context_tokens=context_tokens,
                                 context_length=context_length, cwd=cwd, turn_seconds=turn_seconds,
                                 requested_model=requested_model, served_model=served_model,
                                 fields=cfg.get("fields") or _DEFAULT_FIELDS)
'''

BASE_RUN_TURN = '''class Runner:
    def _hmwa_runtime_footer_line(self, agent_result, source, _turn_seconds):
        from gateway.run import _load_gateway_config, _platform_config_key, _terminal_scope_cwd
        try:
            from gateway.runtime_footer import build_footer_line as _bfl
            return _bfl(
                user_config=_load_gateway_config(),
                platform_key=_platform_config_key(source.platform), model=agent_result.get("model"),
                context_tokens=agent_result.get("last_prompt_tokens", 0) or 0,
                context_length=agent_result.get("context_length") or None,
                cwd=_terminal_scope_cwd(""), turn_seconds=_turn_seconds,
                requested_model=agent_result.get("requested_model"),
                served_model=agent_result.get("served_model"),
            )
        except Exception as _footer_err:
            return ""

    async def finish(self, agent_result, source, _turn_seconds):
        _footer_line = self._hmwa_runtime_footer_line(agent_result, source, _turn_seconds)
        return _footer_line
'''


def make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "hermes"
    (root / "gateway").mkdir(parents=True)
    (root / "gateway/runtime_footer.py").write_text(BASE_RUNTIME_FOOTER, encoding="utf-8")
    (root / "gateway/run_turn.py").write_text(BASE_RUN_TURN, encoding="utf-8")
    return root


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("patched_footer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeFooterPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = make_tree(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def require_implementation(self):
        self.assertIsNotNone(apply_patch, "runtime_footer_patch implementation is missing")

    def test_apply_patches_both_gateway_files_and_is_idempotent(self):
        self.require_implementation()
        first = apply_patch(self.root)
        second = apply_patch(self.root)
        self.assertEqual(first, {"runtime_footer": "patched", "run_turn": "patched"})
        self.assertEqual(second, {"runtime_footer": "already_patched", "run_turn": "already_patched"})

        footer = (self.root / "gateway/runtime_footer.py").read_text(encoding="utf-8")
        run_turn = (self.root / "gateway/run_turn.py").read_text(encoding="utf-8")
        self.assertIn("async def build_footer_line_async", footer)
        self.assertIn('"codex_quota": lambda: codex_quota or ""', footer)
        self.assertIn("async def _hmwa_runtime_footer_line", run_turn)
        self.assertIn('provider=agent_result.get("provider")', run_turn)
        self.assertIn("await self._hmwa_runtime_footer_line", run_turn)
        self.assertIn('requested_model=agent_result.get("requested_model")', run_turn)
        self.assertIn('served_model=agent_result.get("served_model")', run_turn)

    def test_apply_refuses_unknown_source_without_partial_write(self):
        self.require_implementation()
        before_footer = (self.root / "gateway/runtime_footer.py").read_bytes()
        (self.root / "gateway/run_turn.py").write_text("incompatible", encoding="utf-8")
        incompatible = (self.root / "gateway/run_turn.py").read_bytes()

        with self.assertRaises(PatchError):
            apply_patch(self.root)

        self.assertEqual((self.root / "gateway/runtime_footer.py").read_bytes(), before_footer)
        self.assertEqual((self.root / "gateway/run_turn.py").read_bytes(), incompatible)

    def test_patched_formatter_renders_weekly_quota_in_requested_order(self):
        self.require_implementation()
        apply_patch(self.root)
        footer = load_module(self.root / "gateway/runtime_footer.py")

        class Window:
            label = "Weekly"
            used_percent = 60.0
            reset_at = datetime(2026, 9, 25, 2, 34, 19, tzinfo=timezone.utc)

        class Snapshot:
            windows = (Window(),)

        quota = footer.format_codex_quota(
            Snapshot(), now=datetime(2026, 9, 23, 0, 36, 58, tzinfo=timezone.utc)
        )
        line = footer.format_runtime_footer(
            model="openai-codex/gpt-5.6-sol",
            context_tokens=0,
            context_length=None,
            turn_seconds=2.2,
            codex_quota=quota,
            fields=("latency", "model", "codex_quota"),
        )
        self.assertEqual(line, "2s · gpt-5.6-sol · 잔여량 40% (초기화 2일 남음)")

    def test_async_builder_fetches_quota_when_field_is_requested_even_if_provider_is_missing(self):
        self.require_implementation()
        apply_patch(self.root)
        footer = load_module(self.root / "gateway/runtime_footer.py")
        calls = []

        async def fake_quota(**kwargs):
            calls.append(kwargs)
            return "잔여량 40% (초기화 2일 남음)"

        config = {"fields": ["latency", "model", "codex_quota"]}
        with mock.patch.object(footer, "get_codex_quota_footer", fake_quota):
            line = asyncio.run(footer.build_footer_line_async(
                user_config=config,
                platform_key="slack",
                provider=None,
                model="gpt-5.6-sol",
                context_tokens=0,
                context_length=None,
                turn_seconds=1.0,
                requested_model="gpt-5.6-sol",
                served_model="gpt-5.6-sol",
            ))
        self.assertEqual(line, "1s · gpt-5.6-sol · 잔여량 40% (초기화 2일 남음)")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
