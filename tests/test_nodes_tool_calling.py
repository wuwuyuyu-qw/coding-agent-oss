from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core import nodes


def _fake_openai_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _FakeOpenAIClient:
    def __init__(self, content: str):
        self._content = content
        self.last_kwargs: dict[str, Any] | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return _fake_openai_response(self._content)


def _patch_response(search: str, replace: str) -> str:
    return f"```search\n{search}\n```\n```replace\n{replace}\n```\n"


@pytest.fixture
def base_state() -> dict[str, Any]:
    return {
        "original_code": "def f(x): return x",
        "current_code": "def f(x): return x",
        "test_code": "assert f(1) == 1",
        "error_log": "",
        "retry_count": 0,
        "max_retries": 3,
        "is_passed": False,
        "final_status": "",
    }


@pytest.fixture
async def fake_llm(monkeypatch):
    fake = _FakeOpenAIClient(
        content=_patch_response("def f(x): return x", "def f(x): return x + 1")
    )
    await nodes.reset_llm_client()

    async def _fake_get() -> _FakeOpenAIClient:
        return fake

    monkeypatch.setattr(nodes, "_get_llm_client", _fake_get)
    return fake


@pytest.mark.asyncio
async def test_repo_root_missing_does_not_inject_tool_context(fake_llm, base_state):
    out = await nodes.generate_and_fix_node(base_state)

    prompt = fake_llm.last_kwargs["messages"][1]["content"]
    assert "[Tool Calling Context]" not in prompt
    assert out["tool_context"] == ""
    assert out["tool_metadata"]["enabled"] is False
    assert out["tool_metadata"]["reason"] == "repo_root_missing"


@pytest.mark.asyncio
async def test_repo_root_present_injects_tool_calling_context(
    fake_llm,
    tmp_path: Path,
    base_state,
):
    (tmp_path / "app.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    state = {
        **base_state,
        "repo_root": str(tmp_path),
        "target_file": "app.py",
        "user_request": "fix f",
    }

    out = await nodes.generate_and_fix_node(state)

    prompt = fake_llm.last_kwargs["messages"][1]["content"]
    assert "[Tool Calling Context]" in prompt
    assert "tool: list_files" in prompt
    assert "tool: search_code" in prompt
    assert "tool: read_file" in prompt
    assert out["tool_metadata"]["enabled"] is True
    assert out["tool_metadata"]["executed_count"] == 3
    assert out["current_code"].strip() == "def f(x): return x + 1"


@pytest.mark.asyncio
async def test_tool_failure_still_allows_patch_generation(
    fake_llm,
    tmp_path: Path,
    base_state,
):
    (tmp_path / "app.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    state = {
        **base_state,
        "repo_root": str(tmp_path),
        "target_file": "../blocked.py",
        "user_request": "fix f",
    }

    out = await nodes.generate_and_fix_node(state)

    assert out["current_code"].strip() == "def f(x): return x + 1"
    assert out["tool_metadata"]["enabled"] is True
    assert out["tool_metadata"]["failed_count"] >= 1
    assert any(not result["success"] for result in out["tool_results"])

