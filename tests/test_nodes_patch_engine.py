from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core import nodes


class _FakeOpenAIClient:
    def __init__(self, content: str):
        self._content = content
        self.last_kwargs: dict[str, Any] | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


def _patch(search: str, replace: str) -> str:
    return f"```search\n{search}\n```\n```replace\n{replace}\n```\n"


@pytest.fixture
def base_state() -> dict[str, Any]:
    return {
        "original_code": "def f(x): return x",
        "current_code": "def f(x): return x",
        "test_code": "assert f(1) == 2",
        "error_log": "",
        "retry_count": 0,
        "max_retries": 3,
        "is_passed": False,
        "final_status": "",
    }


async def _install_fake_llm(monkeypatch, content: str) -> _FakeOpenAIClient:
    fake = _FakeOpenAIClient(content)
    await nodes.reset_llm_client()

    async def _fake_get() -> _FakeOpenAIClient:
        return fake

    monkeypatch.setattr(nodes, "_get_llm_client", _fake_get)
    return fake


@pytest.mark.asyncio
async def test_no_repo_root_uses_original_single_file_flow(monkeypatch, base_state):
    await _install_fake_llm(
        monkeypatch,
        _patch("def f(x): return x", "def f(x): return x + 1"),
    )

    out = await nodes.generate_and_fix_node(base_state)

    assert out["current_code"] == "def f(x): return x + 1\n"
    assert out.get("patch_plan") in (None, {})


@pytest.mark.asyncio
async def test_repo_root_uses_patch_engine_and_writes_state(
    monkeypatch,
    tmp_path: Path,
    base_state,
):
    (tmp_path / "app.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    await _install_fake_llm(monkeypatch, _patch("    return x", "    return x + 1"))
    state = {
        **base_state,
        "repo_root": str(tmp_path),
        "target_file": "app.py",
        "current_code": "def f(x):\n    return x\n",
        "original_code": "def f(x):\n    return x\n",
    }

    out = await nodes.generate_and_fix_node(state)

    assert out["current_code"] == "def f(x):\n    return x + 1\n"
    assert out["patch_plan"]["patch_type"] == "search_replace"
    assert out["patch_validation"]["valid"] is True
    assert out["patch_apply_result"]["success"] is True
    assert out["patch_audit"]["patch_id"] == out["patch_plan"]["patch_id"]


@pytest.mark.asyncio
async def test_patch_engine_failure_does_not_crash_flow(
    monkeypatch,
    tmp_path: Path,
    base_state,
):
    (tmp_path / "app.py").write_text("same\nsame\n", encoding="utf-8")
    await _install_fake_llm(monkeypatch, _patch("same", "new"))
    state = {
        **base_state,
        "repo_root": str(tmp_path),
        "target_file": "app.py",
        "current_code": "same\nsame\n",
        "original_code": "same\nsame\n",
    }

    out = await nodes.generate_and_fix_node(state)

    assert out["current_code"] == "same\nsame\n"
    assert "Patch Merge Failed" in out["error_log"]
    assert out["patch_validation"]["valid"] is False
    assert out["patch_apply_result"]["success"] is False
