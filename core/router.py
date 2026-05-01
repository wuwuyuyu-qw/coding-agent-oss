"""core/router.py —— 条件路由与死锁熔断。

[Design Rationale] 路由器是 LangGraph 状态机的"调度大脑"。
LangGraph 的范式要求路由函数：
    - 是纯函数：相同 state 永远返回相同分支字符串；
    - 返回值类型是 str（或 list[str] 走多分支）；
    - 这个 str 会去 `add_conditional_edges` 的 mapping 里查下一个节点。

把路由独立成一个文件而不是塞进 main.py，是因为：
    1) 路由逻辑会随着业务复杂度膨胀（未来可能加"sandbox 不可用 -> 走 fallback"）；
    2) 路由可以独立单测，无需启动整张图。
"""

from __future__ import annotations

import logging

from .state import AgentState

logger = logging.getLogger(__name__)


# ============================================================
# 路由分支字符串常量
# ------------------------------------------------------------
# 为什么不用 magic string 直接 return "retry"？
#   - 字符串拼写错误是路由 bug 的头号杀手（例：return "retri"）；
#   - 用常量，IDE 能补全，重构时一处修改全局生效；
#   - 测试时可以 `from .router import ROUTE_RETRY` 做精确断言。
# ============================================================

ROUTE_SUCCESS: str = "success"
ROUTE_RETRY: str = "retry"
ROUTE_CIRCUIT_BREAK: str = "circuit_break"


# ============================================================
# 默认熔断阈值
# ============================================================
"""默认最大重试次数"""
DEFAULT_MAX_RETRIES: int = 3
"""为什么默认 3 次？
    - 学术经验：Reflexion 论文显示前 3 轮收益最大，第 4 轮起边际递减；
    - 工程经验：3 次能覆盖 ~80% 的 bug 类型，再多就是烧 token；
    - Token 成本：每轮 ~2K tokens，3 次 ≈ 6K，可控。
    可以通过 state["max_retries"] 由外部按任务难度覆盖。"""


# ============================================================
# 核心路由函数
# ============================================================


def circuit_breaker_router(state: AgentState) -> str:
    """根据沙盒结果与重试次数，决定下一跳。

    返回值（必须是 ROUTE_* 常量之一）：
        - ROUTE_SUCCESS        : 测试通过，正常终止
        - ROUTE_RETRY          : 失败但仍有重试额度，回到 Actor 反思修复
        - ROUTE_CIRCUIT_BREAK  : 失败且超出重试上限，触发熔断，强制终止

    [Design Rationale] 路由顺序非常关键，必须是：
        ① 先判 success（最快路径，避免任何不必要的计算）
        ② 再判 circuit_break（兜底，防止任何分支漏出去成无限循环）
        ③ 最后才是 retry（默认动作）
    """

    # ---- ① 成功路径：最高优先级，立刻终止 ----
    # 用 .get() 而不是 state["is_passed"]，
    # 是为了在 sandbox 节点意外没写回时，路由仍然能 fallback 到 retry，
    # 而不是因为 KeyError 让整张图崩溃。这就是"防御性编程"。
    if state.get("is_passed", False):
        logger.info("[Router] ✅ 测试通过 -> SUCCESS")
        return ROUTE_SUCCESS

    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", DEFAULT_MAX_RETRIES)

    # ---- ② 熔断路径：超出上限，强制终止 ----
    # Circuit Breaker 模式的本质：
    #   当一个有失败可能的操作连续失败 N 次后，认为它"暂时不可恢复"，
    #   主动断开后续调用，把控制权交还给上层。
    # 在 Agent 场景下，它防的是：
    #   1) Token 烧穿账单（每轮调用 ¥0.01-1，乘 ∞ 就是无限亏损）；
    #   2) 用户体验黑洞（用户等了 5 分钟，结果系统在反复试错）；
    #   3) 模型陷在错误模式（同样的错改 100 次还是错的现象很常见）。
    # 触发条件用 >= 而不是 ==，是为了应对"max_retries 被运行时改小"的极端情况。
    if retry_count >= max_retries:
        logger.warning(
            "[Router] 🔴 熔断触发：retry_count=%d >= max_retries=%d -> CIRCUIT_BREAK",
            retry_count, max_retries,
        )
        return ROUTE_CIRCUIT_BREAK

    # ---- ③ 默认：继续反思重试 ----
    logger.info(
        "[Router] 🔄 失败但仍有额度：retry_count=%d / max_retries=%d -> RETRY",
        retry_count, max_retries,
    )
    return ROUTE_RETRY
