"""tests/test_llm_client.py —— 异步 LLM 客户端单例与连接池。"""

from __future__ import annotations

import asyncio

import pytest
from openai import AsyncOpenAI

from core import nodes


@pytest.fixture(autouse=True)
async def _clean_singleton_between_tests():
    await nodes.reset_llm_client()
    yield
    await nodes.reset_llm_client()


@pytest.fixture
def fake_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key-0123456789")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.fake-endpoint.invalid/v1")


class TestLLMClientFailFast:
    @pytest.mark.asyncio
    async def test_missing_api_key_raises_runtime_error(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            await nodes._get_llm_client()

    @pytest.mark.asyncio
    async def test_error_message_mentions_env_file(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError) as exc_info:
            await nodes._get_llm_client()
        msg = str(exc_info.value)
        assert ".env" in msg
        assert "OPENAI_API_KEY" in msg


class TestLLMClientSingletonIdentity:
    @pytest.mark.asyncio
    async def test_returns_async_openai_instance(self, fake_api_key):
        client = await nodes._get_llm_client()
        assert isinstance(client, AsyncOpenAI)

    @pytest.mark.asyncio
    async def test_two_calls_return_same_instance(self, fake_api_key):
        c1 = await nodes._get_llm_client()
        c2 = await nodes._get_llm_client()
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_many_calls_all_return_same_instance(self, fake_api_key):
        clients = await asyncio.gather(*[nodes._get_llm_client() for _ in range(50)])
        assert len({id(c) for c in clients}) == 1


class TestLLMClientReset:
    @pytest.mark.asyncio
    async def test_reset_creates_new_instance(self, fake_api_key):
        c1 = await nodes._get_llm_client()
        await nodes.reset_llm_client()
        c2 = await nodes._get_llm_client()
        assert c1 is not c2


class TestLLMClientConfiguration:
    @pytest.mark.asyncio
    async def test_respects_base_url_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
        await nodes.reset_llm_client()
        client = await nodes._get_llm_client()
        assert "deepseek.com" in str(client.base_url)

    @pytest.mark.asyncio
    async def test_default_base_url_when_env_missing(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        await nodes.reset_llm_client()
        client = await nodes._get_llm_client()
        assert "openai.com" in str(client.base_url)

    @pytest.mark.asyncio
    async def test_client_constructs_without_hitting_network(self, fake_api_key):
        client = await nodes._get_llm_client()
        assert client is not None
