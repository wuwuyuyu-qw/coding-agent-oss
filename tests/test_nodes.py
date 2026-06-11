"""tests/test_nodes.py —— LangGraph 节点（异步 Actor + Sandbox）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from openai import APIError as OpenAIAPIError
from openai import APITimeoutError

from core import nodes
from core.sandbox import SandboxResult


def _fake_openai_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _FakeOpenAIClient:
    def __init__(self, *, content: str = "", raises: Exception | None = None):
        self._content = content
        self._raises = raises
        self.last_kwargs: dict[str, Any] | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
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
async def patch_llm(monkeypatch):
    async def _inject(
        *, content: str = "", raises: Exception | None = None
    ) -> _FakeOpenAIClient:
        fake = _FakeOpenAIClient(content=content, raises=raises)
        await nodes.reset_llm_client()

        async def _fake_get() -> _FakeOpenAIClient:
            return fake

        monkeypatch.setattr(nodes, "_get_llm_client", _fake_get)
        return fake

    return _inject


class TestGenerateAndFixNodeFirstPass:
    @pytest.mark.asyncio
    async def test_returns_partial_state(self, patch_llm, base_state):
        await patch_llm(
            content=_patch_response("def f(x): return x", "def f(x): return x * 2")
        )
        out = await nodes.generate_and_fix_node(base_state)

        assert out["current_code"].strip() == "def f(x): return x * 2"
        assert out["error_log"] == ""
        assert out["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_uses_first_prompt_when_no_error(self, patch_llm, base_state):
        fake = await patch_llm(content=_patch_response("def f(x): return x", "pass"))
        await nodes.generate_and_fix_node(base_state)
        system_msg = fake.last_kwargs["messages"][0]["content"]
        assert "资深 Python" in system_msg
        assert "上一轮" not in system_msg

    @pytest.mark.asyncio
    async def test_temperature_is_low_for_code_task(self, patch_llm, base_state):
        fake = await patch_llm(
            content=_patch_response("def f(x): return x", "def f(x): return x")
        )
        await nodes.generate_and_fix_node(base_state)
        assert 0.0 < fake.last_kwargs["temperature"] <= 0.3


class TestGenerateAndFixNodeReflexionPass:
    @pytest.mark.asyncio
    async def test_uses_reflect_prompt_when_error_log_present(
        self, patch_llm, base_state
    ):
        fake = await patch_llm(content=_patch_response("def f(x): return x", "pass"))
        state = {**base_state, "error_log": "ZeroDivisionError: division by zero"}
        await nodes.generate_and_fix_node(state)

        system_msg = fake.last_kwargs["messages"][0]["content"]
        assert "Self-Healing" in system_msg
        assert "ZeroDivisionError" in system_msg

    @pytest.mark.asyncio
    async def test_whitespace_only_error_log_is_first_pass(self, patch_llm, base_state):
        fake = await patch_llm(
            content=_patch_response("def f(x): return x", "def f(x): return x")
        )
        state = {**base_state, "error_log": "   \n\t  "}
        await nodes.generate_and_fix_node(state)
        system_msg = fake.last_kwargs["messages"][0]["content"]
        assert "上一轮" not in system_msg


class TestGenerateAndFixNodeNetworkResilience:
    @pytest.mark.asyncio
    async def test_catches_api_timeout_error(self, patch_llm, base_state):
        import httpx

        exc = APITimeoutError(request=httpx.Request("POST", "https://x.invalid"))
        await patch_llm(raises=exc)
        out = await nodes.generate_and_fix_node(base_state)
        assert out["retry_count"] == 1
        assert "LLM Call Failed" in out["error_log"]
        assert "APITimeoutError" in out["error_log"]
        assert out["current_code"] == base_state["current_code"]

    @pytest.mark.asyncio
    async def test_catches_base_openai_api_error(self, patch_llm, base_state):
        class _FakeAPIError(OpenAIAPIError):
            def __init__(self, message: str):
                self.message = message

            def __str__(self):
                return self.message

        await patch_llm(raises=_FakeAPIError("API down for maintenance"))
        out = await nodes.generate_and_fix_node(base_state)
        assert out["retry_count"] == 1
        assert "LLM Call Failed" in out["error_log"]
        assert "API down" in out["error_log"]

    @pytest.mark.asyncio
    async def test_retry_count_increments_on_llm_failure(self, patch_llm, base_state):
        import httpx

        await patch_llm(
            raises=APITimeoutError(request=httpx.Request("POST", "https://x.invalid"))
        )
        state = {**base_state, "retry_count": 2}
        out = await nodes.generate_and_fix_node(state)
        assert out["retry_count"] == 3


class TestGenerateAndFixNodeDefensive:
    @pytest.mark.asyncio
    async def test_empty_content_falls_back_to_current_code(self, patch_llm, base_state):
        await patch_llm(content="")
        out = await nodes.generate_and_fix_node(base_state)
        assert out["current_code"] == base_state["current_code"]
        assert out["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_patch_returns_error_log(self, patch_llm, base_state):
        await patch_llm(content="I refuse to write fences.")
        out = await nodes.generate_and_fix_node(base_state)
        assert out["retry_count"] == 1
        assert "Patch Merge Failed" in out["error_log"]

    @pytest.mark.asyncio
    async def test_missing_repo_root_falls_back_to_original_flow(
        self, patch_llm, tmp_path, base_state
    ):
        fake = await patch_llm(
            content=_patch_response("def f(x): return x", "def f(x): return x + 1")
        )
        state = {**base_state, "repo_root": str(tmp_path / "missing")}

        out = await nodes.generate_and_fix_node(state)

        assert out["current_code"].strip() == "def f(x): return x + 1"
        assert out["rag_context"] == ""
        assert out["rag_metadata"]["enabled"] is False
        assert out["rag_metadata"]["reason"] == "repo_root_not_found"
        assert "[Repo-level Evidence Context]" not in fake.last_kwargs["messages"][1]["content"]


@pytest.fixture
def patch_sandbox(monkeypatch):
    def _inject(result: SandboxResult):
        async def _async_run(**kwargs):
            return result

        monkeypatch.setattr(nodes, "run_in_sandbox_async", _async_run)
        return result

    return _inject


class TestRunSandboxNode:
    @pytest.mark.asyncio
    async def test_passed_clears_error_log(self, patch_sandbox, base_state):
        patch_sandbox(
            SandboxResult(
                passed=True,
                stdout="__PASS__",
                stderr="",
                exit_code=0,
                timed_out=False,
            )
        )
        out = await nodes.run_sandbox_node(base_state)
        assert out == {"is_passed": True, "error_log": ""}

    @pytest.mark.asyncio
    async def test_failed_includes_stderr(self, patch_sandbox, base_state):
        patch_sandbox(
            SandboxResult(
                passed=False,
                stdout="",
                stderr="ZeroDivisionError: division by zero",
                exit_code=1,
                timed_out=False,
            )
        )
        out = await nodes.run_sandbox_node(base_state)
        assert out["is_passed"] is False
        assert "stderr" in out["error_log"]
        assert "ZeroDivisionError" in out["error_log"]

    @pytest.mark.asyncio
    async def test_failed_includes_stdout_when_stderr_empty(
        self, patch_sandbox, base_state
    ):
        patch_sandbox(
            SandboxResult(
                passed=False,
                stdout="debug: x was None",
                stderr="",
                exit_code=1,
                timed_out=False,
            )
        )
        out = await nodes.run_sandbox_node(base_state)
        assert "stdout" in out["error_log"]
        assert "debug: x was None" in out["error_log"]

    @pytest.mark.asyncio
    async def test_failed_merges_stdout_and_stderr(self, patch_sandbox, base_state):
        patch_sandbox(
            SandboxResult(
                passed=False,
                stdout="out",
                stderr="err",
                exit_code=1,
                timed_out=False,
            )
        )
        out = await nodes.run_sandbox_node(base_state)
        assert "stderr" in out["error_log"] and "err" in out["error_log"]
        assert "stdout" in out["error_log"] and "out" in out["error_log"]

    @pytest.mark.asyncio
    async def test_failed_with_empty_logs_shows_placeholder(
        self, patch_sandbox, base_state
    ):
        patch_sandbox(
            SandboxResult(
                passed=False, stdout="", stderr="", exit_code=-1, timed_out=True
            )
        )
        out = await nodes.run_sandbox_node(base_state)
        assert out["is_passed"] is False
        assert out["error_log"] == "(空错误日志)"

    @pytest.mark.asyncio
    async def test_exit_137_oom_prefix(self, patch_sandbox, base_state):
        patch_sandbox(
            SandboxResult(
                passed=False,
                stdout="x",
                stderr="y",
                exit_code=137,
                timed_out=False,
            )
        )
        out = await nodes.run_sandbox_node(base_state)
        assert "OOM" in out["error_log"] or "137" in out["error_log"]

    @pytest.mark.asyncio
    async def test_falls_back_to_original_code_when_current_missing(
        self, patch_sandbox, monkeypatch, base_state
    ):
        captured: dict[str, Any] = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)
            return SandboxResult(
                passed=True, stdout="__PASS__", stderr="", exit_code=0, timed_out=False
            )

        monkeypatch.setattr(nodes, "run_in_sandbox_async", fake_run)

        state = {**base_state}
        state.pop("current_code")
        await nodes.run_sandbox_node(state)
        assert captured["code"] == base_state["original_code"]


@pytest.fixture(autouse=True)
async def _reset_llm_between_tests():
    await nodes.reset_llm_client()
    yield
    await nodes.reset_llm_client()
