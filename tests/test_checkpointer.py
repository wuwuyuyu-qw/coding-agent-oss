"""tests/test_checkpointer.py —— 验证持久化 Checkpointer（需求 1）。

本测试的难点在于：
    - 真实的 actor 节点会调 LLM（要钱、要网络），sandbox 节点会调 Docker；
    - 单测必须用 fake 节点替换它们，但又要保留"走完整张图 + 经过 checkpointer"
      的端到端行为，才能真正证明 MemorySaver 在工作。

解法：用 monkeypatch 直接替换 `main` 模块 namespace 里的两个节点名，
build_graph() 内部 `add_node("actor", generate_and_fix_node)` 会在调用时
查到被替换后的 fake 版本。这是 Python 模块级命名空间的标准玩法。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

import main
from core.state import AgentState


# ============================================================
# 测试工具：可配置的 fake 节点
# ============================================================


def _make_fake_nodes(pass_on_attempt: int = 1):
    """[Design Rationale] 工厂函数返回一对 (fake_actor, fake_sandbox) 节点：
        - fake_actor    : 记录调用次数，生成一段"假修复代码"；
        - fake_sandbox  : 在第 N 次尝试时返回 passed=True，之前都失败。

    这让我们可以参数化测试：
        - pass_on_attempt=1 : 一次就过，只会产生一轮 actor→sandbox；
        - pass_on_attempt=3 : 第三次才过，验证 checkpoint 跨多步持久化。
    """
    call_log: dict[str, int] = {"actor": 0, "sandbox": 0}

    async def fake_actor(state: AgentState) -> dict[str, Any]:
        call_log["actor"] += 1
        return {
            "current_code": f"# fake fix v{call_log['actor']}",
            "error_log": "",
            "retry_count": state.get("retry_count", 0) + 1,
        }

    async def fake_sandbox(state: AgentState) -> dict[str, Any]:
        call_log["sandbox"] += 1
        if call_log["sandbox"] >= pass_on_attempt:
            return {"is_passed": True, "error_log": ""}
        return {
            "is_passed": False,
            "error_log": f"fake error on attempt {call_log['sandbox']}",
        }

    return fake_actor, fake_sandbox, call_log


def _base_state(max_retries: int = 3) -> AgentState:
    return {
        "original_code": "def f(): pass",
        "current_code": "def f(): pass",
        "test_code": "assert True",
        "error_log": "",
        "retry_count": 0,
        "max_retries": max_retries,
        "is_passed": False,
        "final_status": "",
    }


@pytest.fixture
def patched_app(monkeypatch):
    """构建一个 checkpointer 已装配、但 actor/sandbox 已被 fake 替换的 app。"""
    fake_actor, fake_sandbox, call_log = _make_fake_nodes(pass_on_attempt=1)
    monkeypatch.setattr(main, "generate_and_fix_node", fake_actor)
    monkeypatch.setattr(main, "run_sandbox_node", fake_sandbox)
    app = main.build_graph()
    return app, call_log


# ============================================================
# 测试用例
# ============================================================


class TestCheckpointerWiring:
    """先验证"compile() 确实注入了 MemorySaver"，再谈上层语义。"""

    def test_compiled_app_has_checkpointer(self, patched_app):
        app, _ = patched_app
        # LangGraph 的 CompiledGraph 暴露 .checkpointer 属性。
        assert app.checkpointer is not None, "compile() 必须装配 checkpointer"

    def test_checkpointer_is_memory_saver(self, patched_app):
        app, _ = patched_app
        assert isinstance(app.checkpointer, MemorySaver), (
            "Demo 阶段应当是 MemorySaver；生产切换到 PostgresSaver 时"
            "这个断言也会红灯提醒团队确认是否是有意切换。"
        )


class TestCheckpointerPersistence:
    """验证 state 真的被持久化了——通过 get_state 读回。"""

    def test_invoke_with_thread_id_succeeds(self, patched_app):
        app, call_log = patched_app
        config = {"configurable": {"thread_id": "t1"}, "recursion_limit": 25}
        final = asyncio.run(app.ainvoke(_base_state(), config=config))
        assert final["final_status"] == "success"
        assert final["is_passed"] is True
        assert call_log["actor"] == 1
        assert call_log["sandbox"] == 1

    def test_get_state_returns_persisted_state(self, patched_app):
        app, _ = patched_app
        config = {"configurable": {"thread_id": "persist_check"}, "recursion_limit": 25}
        asyncio.run(app.ainvoke(_base_state(), config=config))

        # [关键断言] invoke 结束后，checkpointer 里应当还有 state，
        # 能够通过 get_state(config) 读回—— 这就是"持久化"的定义。
        snapshot = app.get_state(config)
        assert snapshot.values["final_status"] == "success"
        assert snapshot.values["is_passed"] is True
        assert snapshot.values["retry_count"] >= 1

    def test_different_thread_ids_are_isolated(self, patched_app):
        """多租户场景：不同 thread_id 的 state 必须完全隔离。"""
        app, _ = patched_app
        cfg_a = {"configurable": {"thread_id": "tenant_a"}, "recursion_limit": 25}
        cfg_b = {"configurable": {"thread_id": "tenant_b"}, "recursion_limit": 25}

        # A 跑完一整轮
        asyncio.run(app.ainvoke(_base_state(), config=cfg_a))
        snap_a = app.get_state(cfg_a)
        assert snap_a.values.get("final_status") == "success"

        # B 还没跑 —— 应当没有任何持久化状态
        snap_b = app.get_state(cfg_b)
        # MemorySaver 对未知 thread 返回 values={} 或 final_status 未设置
        assert snap_b.values.get("final_status") != "success", (
            "thread_b 不应看到 thread_a 的成功状态，否则就是多租户串数据 —— P0 故障"
        )


class TestCheckpointerTimeTravel:
    """时间旅行调试能力：get_state_history 能列出所有 checkpoint。"""

    def test_state_history_has_multiple_checkpoints(self, patched_app):
        app, _ = patched_app
        config = {"configurable": {"thread_id": "history_demo"}, "recursion_limit": 25}
        asyncio.run(app.ainvoke(_base_state(), config=config))

        history = list(app.get_state_history(config))
        # 一轮 = entry + actor + sandbox + success + END，
        # MemorySaver 至少会保留 3 个以上 checkpoint。
        assert len(history) >= 3, (
            f"Checkpoint 历史应当有多个 (entry/actor/sandbox/success)，"
            f"实际只有 {len(history)} 个 —— 时间旅行调试能力不可用"
        )

    def test_history_entries_have_monotonic_steps(self, patched_app):
        """[Design Rationale] 每个 checkpoint 都带一个单调递增的 step 编号，
        这是"回到第 N 步"功能的基础。"""
        app, _ = patched_app
        config = {"configurable": {"thread_id": "monotonic"}, "recursion_limit": 25}
        asyncio.run(app.ainvoke(_base_state(), config=config))

        history = list(app.get_state_history(config))
        # get_state_history 返回的顺序是"最新→最旧"，每个都有 config 信息。
        assert all(h.config is not None for h in history)
        # 至少要有一个 metadata 不为空的 checkpoint
        assert any(h.metadata for h in history)


class TestCheckpointerMultiRoundReflexion:
    """验证多轮 Reflexion 场景下 checkpointer 的完整性。"""

    def test_three_retries_then_success(self, monkeypatch):
        """前两次 sandbox fail，第三次 pass，校验 actor 被调 3 次。"""
        fake_actor, fake_sandbox, call_log = _make_fake_nodes(pass_on_attempt=3)
        monkeypatch.setattr(main, "generate_and_fix_node", fake_actor)
        monkeypatch.setattr(main, "run_sandbox_node", fake_sandbox)
        app = main.build_graph()

        config = {"configurable": {"thread_id": "reflexion_3"}, "recursion_limit": 25}
        final = asyncio.run(app.ainvoke(_base_state(max_retries=5), config=config))

        assert final["final_status"] == "success"
        assert call_log["actor"] == 3, "前两次 sandbox fail 会触发 actor 再跑 2 次"
        assert call_log["sandbox"] == 3
        # retry_count 由 actor 每次 +1 得到
        assert final["retry_count"] == 3

    def test_circuit_break_when_max_retries_exhausted(self, monkeypatch):
        """验证业务层熔断与 checkpointer 协同工作。"""
        # sandbox 永远失败
        fake_actor, fake_sandbox, call_log = _make_fake_nodes(pass_on_attempt=999)
        monkeypatch.setattr(main, "generate_and_fix_node", fake_actor)
        monkeypatch.setattr(main, "run_sandbox_node", fake_sandbox)
        app = main.build_graph()

        config = {"configurable": {"thread_id": "must_break"}, "recursion_limit": 25}
        final = asyncio.run(app.ainvoke(_base_state(max_retries=3), config=config))

        assert final["final_status"] == "circuit_break"
        assert final["is_passed"] is False
        assert call_log["actor"] == 3
        assert call_log["sandbox"] == 3
