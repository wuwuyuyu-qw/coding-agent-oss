"""main.py —— Self-Healing PR Agent 装配入口与 Demo。

[Design Rationale] 整张图的拓扑：

         ┌──────────────────┐
   ───>  │      Actor       │  generate_and_fix_node
         │  (LLM 生成/反思) │
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────┐
         │     Sandbox      │  run_sandbox_node
         │  (Docker 隔离)   │
         └────────┬─────────┘
                  │ 条件路由 (circuit_breaker_router)
        ┌─────────┼──────────────┐
        │         │              │
        ▼         ▼              ▼
    SUCCESS    RETRY        CIRCUIT_BREAK
       │         │              │
       │         └─> 回到 Actor │
       │                        │
       └────────> END <─────────┘

为什么要建独立的 success / circuit_break 终态节点，
而不是直接 add_conditional_edges 到 END？
    - 终态节点可以打 final_status 标签，便于下游统计/告警；
    - 未来想接 webhook 通知（"修复成功了发个钉钉"）只需扩展终态节点；
    - LangSmith Trace 里两条终止链路视觉上完全分开，调试体验飞跃。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

# [Design Rationale] MemorySaver 是 LangGraph 官方提供的「进程内 Checkpointer」。
# Checkpointer 会在每个节点执行完后，把当前 state 快照序列化并以
# (thread_id, step_id) 为主键存起来。它是 LangGraph "Durable Execution" 的基石。
#
# 为什么演示先用 MemorySaver？ 
#     - 零依赖、无 IO、单进程就能跑，适合 demo 和单测；
#     - 但它是易失的：进程一挂 state 全没。
# 生产环境必须替换为持久化后端（按下列优先级推荐）：
#     1) PostgresSaver   —— 主流首选：ACID、横向扩展、天然支持多 worker；
#     2) SqliteSaver     —— 单机轻量：嵌入式、零运维，适合工具类 Agent；
#     3) RedisSaver      —— 低延迟优先：checkpoint 频繁时 ~10x 于 Postgres。
# 切换方式只改这一行 import，业务代码零改动——这就是面向接口的威力。
from core.nodes import generate_and_fix_node, run_sandbox_node
from core.router import (
    ROUTE_CIRCUIT_BREAK,
    ROUTE_RETRY,
    ROUTE_SUCCESS,
    circuit_breaker_router,
)
from core.state import AgentState

# ---- 在 import 阶段就加载 .env，确保 nodes.py 能拿到 OPENAI_API_KEY ----
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


# ============================================================
# 终态节点（轻量"标签节点"，只为打 final_status）
# ============================================================


async def _success_node(state: AgentState) -> dict[str, Any]:
    """[Design Rationale] 终态节点不做任何 IO，纯打标签。
    保持职责极简，是为了让"成功路径"的 trace 看起来一目了然。"""
    logger.info("✅ Agent 成功完成自修复，retry_count=%d", state.get("retry_count", 0))
    return {"final_status": "success"}


async def _circuit_break_node(state: AgentState) -> dict[str, Any]:
    """[Design Rationale] 熔断终态。生产环境应在这里：
        - 上报 Prometheus 指标 `agent_circuit_break_total{reason="..."}`
        - 持久化最后一次 error_log 到 PostgreSQL 供人工 review
        - 触发 PagerDuty 告警（如果是 P0 任务）
    Demo 阶段先打个日志即可。"""
    logger.error(
        "🔴 触发熔断：达到最大重试次数 %d 仍未修复成功。\n   最后一次报错: %s",
        state.get("retry_count", 0),
        (state.get("error_log") or "")[:500],
    )
    return {"final_status": "circuit_break"}


# ============================================================
# 图装配
# ============================================================


def build_graph():
    """组装 LangGraph 状态机并返回 compiled app。

    为什么把建图独立成函数而不是模块顶层执行？
        - 测试时可以 build 多个图实例做独立断言；
        - 未来想加运行时配置（比如不同模型走不同图），可以传参进来；
        - 避免 import main.py 就触发一坨副作用。

    [Design Rationale·持久化（新增）] 本版本为 compile() 注入了 Checkpointer：
        - 每个节点执行完之后，LangGraph 会用 (thread_id, checkpoint_id) 为主键
          把完整 state 序列化下来；
        - 这让整张图从「无状态一次性执行」升级为「可恢复、可回放、可人工干预」
          的工作流引擎，是 Agent 走向生产的分水岭。

            1) 故障恢复（Crash Recovery）：
           进程 OOM 崩溃后，用相同 thread_id 再次 invoke，会从最后一个 checkpoint
           续跑，避免重复烧 token、重复调 Docker；
        2) 人机协同（Human-in-the-Loop）：
           可以在任何节点前用 `interrupt_before=["sandbox"]` 暂停，让人工审核
           LLM 生成的代码后再继续，非常适合金融/医疗等高合规场景；
        3) 分布式追踪（Distributed Tracing）：
           thread_id 就是一整条因果链的 trace_id，接入 LangSmith / OpenTelemetry
           后可以跨进程、跨微服务看到同一任务的完整 span 树；
        4) 时间旅行调试（Time-Travel Debugging）：
           `app.get_state_history(config)` 能列出所有 checkpoint，可以回到任意
           历史步骤，改 state 后重新分叉（fork）执行，是复现偶发 bug 的神器。

            thread_id 是逻辑租户边界：不同请求必须用不同 thread_id，否则它们的
        state 会互相覆盖。生产环境通常用 `f"{user_id}:{task_id}"` 或 UUID。
    """
    graph = StateGraph(AgentState)

    # ---- 注册节点 ----
    graph.add_node("actor", generate_and_fix_node)
    graph.add_node("sandbox", run_sandbox_node)
    graph.add_node("success", _success_node)
    graph.add_node("circuit_break", _circuit_break_node)

    # ---- 入口：第一步永远先调 LLM 生成 ----
    graph.set_entry_point("actor")

    # ---- Actor -> Sandbox 是无条件直边 ----
    # [Design Rationale] 这一步不需要任何路由判断，因为 LLM 一旦输出代码，
    # 我们都要去沙盒验证。把"无条件流"和"条件流"显式区分开，是好的图结构习惯。
    graph.add_edge("actor", "sandbox")

    # ---- Sandbox -> {success | actor | circuit_break} 条件分支 ----
    # add_conditional_edges 的 mapping 一定要"穷举"，
    # 路由函数返回了一个不在 mapping 里的字符串，LangGraph 会直接抛 ValueError，
    # 这是它的"快速失败"机制，比 LangChain 0.x 的"静默挂死"友好得多。
    graph.add_conditional_edges(
        "sandbox",
        circuit_breaker_router,
        {
            ROUTE_SUCCESS: "success",
            ROUTE_RETRY: "actor",
            ROUTE_CIRCUIT_BREAK: "circuit_break",
        },
    )

    # ---- 终态节点 -> END ----
    graph.add_edge("success", END)
    graph.add_edge("circuit_break", END)

    # compile() 这一步会做静态检查：
    #   - 检查所有 add_node 的 name 在 edge 里都有对应；
    #   - 检查从 entry 出发能否可达 END；
    #   - 这是 LangGraph 比裸 LangChain 强的核心原因之一。
    #
    # 注入 MemorySaver checkpointer：
    #     - compile() 后 app 就获得 get_state / update_state /
    #       get_state_history / stream(..., subgraphs=True) 等高级 API；
    #     - 每次跑完一个节点，LangGraph 会自动调用 checkpointer.put() 落盘；
    #     - 下次用同一个 thread_id 再 invoke，会触发 checkpointer.get_tuple()
    #       把 state 读回，从最后成功的 checkpoint 续跑。
    # 这就是把「函数调用」变成「持久化工作流」的一行代码魔法。
    return graph.compile(checkpointer=MemorySaver())


# ============================================================
# Demo：一段经典的 off-by-one bug
# ============================================================


_DEMO_BUGGY_CODE = '''\
def fibonacci(n: int) -> int:
    """返回斐波那契数列第 n 项 (0-indexed): 0, 1, 1, 2, 3, 5, 8, ..."""
    if n <= 0:
        return 0
    if n == 1:
        return 1
    a, b = 0, 1
    # 故意写错：循环次数少了 1，会导致 fibonacci(5) 返回 3 而非 5
    for _ in range(n - 1):
        a, b = b, a + b
    return a
'''

_DEMO_TEST_CODE = '''\
assert fibonacci(0) == 0, f"f(0) expected 0, got {fibonacci(0)}"
assert fibonacci(1) == 1, f"f(1) expected 1, got {fibonacci(1)}"
assert fibonacci(2) == 1, f"f(2) expected 1, got {fibonacci(2)}"
assert fibonacci(5) == 5, f"f(5) expected 5, got {fibonacci(5)}"
assert fibonacci(10) == 55, f"f(10) expected 55, got {fibonacci(10)}"
'''


# [Design Rationale·测试策略] main() 是纯 Demo 编排器，依赖真实 LLM + Docker，
# 单测用 mock 穿过它没意义；真正的 invoke 流程已经在 test_checkpointer.py
# 里用 fake 节点覆盖了。所以这里直接 pragma: no cover 排除它。
def main() -> None:  # pragma: no cover
    """运行一次端到端 Demo（异步调度整张图）。"""
    asyncio.run(_main_async())


async def _main_async() -> None:  # pragma: no cover
    app = build_graph()

    # LangGraph 的 invoke 入参 = 初始 state。
    # 这里把所有"过程态"字段也显式初始化，是 12-Factor 的好习惯：
    # 永远不要依赖框架的 None / 缺失行为，显式 > 隐式。
    initial_state: AgentState = {
        "original_code": _DEMO_BUGGY_CODE,
        "current_code": _DEMO_BUGGY_CODE,
        "test_code": _DEMO_TEST_CODE,
        "error_log": "",
        "retry_count": 0,
        "max_retries": 3,
        "is_passed": False,
        "final_status": "",
    }

    logger.info("============= Self-Healing Agent 启动 =============")
    final_state = await app.ainvoke(
        initial_state,
        # config 的两个关键字段各司其职：
        #   ├─ configurable.thread_id : 业务级「会话 ID」
        #   │     - Checkpointer 用它做主键对 state 分桶；
        #   │     - 同一个 thread_id 重复 invoke 会从最后一个 checkpoint 续跑；
        #   │     - 不同请求必须传不同 thread_id，否则会互相覆盖 state；
        #   │     - 生产环境推荐：`f"{tenant_id}:{task_uuid}"`。
        #   └─ recursion_limit       : 框架级「图步数熔断」
        #         - 是我们业务层 max_retries 熔断之外的第二道保险；
        #         - max_retries=3 时一轮 = actor + sandbox = 2 步，
        #           加上终态节点最多 ~10 步，25 留足 buffer。
        config={
            "configurable": {"thread_id": "demo_task_1"},
            "recursion_limit": 25,
        },
    )
    logger.info("============= Self-Healing Agent 结束 =============")

    print("\n" + "=" * 60)
    print(f"  最终状态        : {final_state.get('final_status')}")
    print(f"  累计反思次数    : {final_state.get('retry_count')}")
    print(f"  测试是否通过    : {final_state.get('is_passed')}")
    print("=" * 60)
    print("\n--- 最终代码 ---\n")
    print(final_state.get("current_code", "<empty>"))
    if not final_state.get("is_passed"):
        print("\n--- 最后一次报错 ---\n")
        print(final_state.get("error_log", "<empty>"))


if __name__ == "__main__":  # pragma: no cover
    main()
