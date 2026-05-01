"""core/state.py —— Agent 状态机的「中枢神经契约」。

[Design Rationale] LangGraph 的核心抽象是 (State, Node, Edge)：
    - State：贯穿整张图的全局上下文。
    - Node：纯函数 `state -> partial_state`，框架自动 merge。
    - Edge：根据 state 决定下一步跳转。
所以 State 的设计决定了整个 Agent 的可调试性与可扩展性。
"""

from __future__ import annotations

from typing import TypedDict


# ============================================================
# 为什么用 TypedDict 而不是 Pydantic BaseModel？
# ------------------------------------------------------------
# 1. LangGraph 内部用 dict.update() 做状态合并（reducer），
#    TypedDict 本质是 dict 的类型注解，零运行时开销。
# 2. Pydantic BaseModel 每次更新都会触发 __init__ 校验，
#    在高频跳转的 Agent 循环里是性能黑洞。
# 3. Pydantic 更适合"边界校验"——比如校验 LLM 返回的 JSON，
#    我们会在 nodes.py 里看到这种典型用法。
# ------------------------------------------------------------
# 工业级实践：state 用 TypedDict（轻），output 用 Pydantic（严）。
# ============================================================


class AgentState(TypedDict, total=False):
    """自修复 Agent 的全局状态。

    [Design Rationale] total=False 让所有字段都是 NotRequired，原因：
        - LangGraph 节点返回 partial state，框架做浅合并；
        - 如果 total=True，节点必须返回所有字段，工程上极其难维护。
        - 唯一约束是「初始 invoke 时把必填字段补全」（在 main.py 入口处）。
    """

    # ---------- 输入侧（由调用方一次性注入） ----------
    """原始代码"""
    original_code: str
    """[字段说明] 用户提交的、可能含 bug 的原始代码。
    全程只读、不修改，作为 LLM 上下文的 anchor，避免反思过程中"漂移失忆"。"""

    """测试代码"""
    test_code: str
    """[字段说明] 必须通过的测试代码（assert 风格或 unittest 风格皆可）。
    这是 Agent 的「ground truth」——只有沙盒里跑通它，才算修复成功。
    这其实就是 Test-Driven Repair 的思想：
    把"代码是否正确"这个模糊问题，转化为"测试是否通过"这个二值问题。"""

    # ---------- 循环过程态（每轮都会被节点改写） ----------
    """当前代码"""
    current_code: str
    """[字段说明] 当前正在尝试的最新版本代码。
    第 0 轮 = original_code；之后每轮被 generate_and_fix_node 覆盖。"""

    """错误日志"""
    error_log: str
    """[字段说明] 沙盒上一次执行的 stderr / Traceback。
    这是整个 Reflexion 模式的「燃料」：
        - 空字符串  -> 第一次生成，走"普通修复"prompt；
        - 非空      -> 注入到 system prompt，触发"自我反思"。
    务必只保留最近一次报错（而非全部历史），原因：
        1) Token 成本——历史报错越堆越多会指数级烧钱；
        2) LLM 的 attention 对最新错误最敏感，旧错误会形成噪声。"""

    """重试次数"""
    retry_count: int
    """[字段说明] 已经反思修复的次数，从 0 开始。
    这是「熔断器」(Circuit Breaker) 的状态变量。
    任何带 LLM 的循环都必须有显式计数器，否则一次 prompt 设计失误
    就可能让你的 API Key 在凌晨四点烧光余额（真实事故）。"""

    """最大重试次数"""
    max_retries: int
    """[字段说明] 最大重试次数，由调用方传入（默认 3）。
    [Design Rationale] 把"上限"放进 state 而不是 hardcode 在 router 里，
    是为了让同一个 graph 实例可以被不同任务复用不同阈值
    （比如 hard problem 给 5 次，simple bug 给 1 次）。"""

    # ---------- 输出侧（由 sandbox / 终止节点写入） ----------

    """是否通过"""
    is_passed: bool
    """[字段说明] 上一次沙盒执行是否通过。路由器的核心判据。"""

    """终态标签"""
    final_status: str
    """[字段说明] 终态标签，取值：
        - "success"        : 修复成功（测试通过）
        - "circuit_break"  : 触发熔断（达到 max_retries 仍失败）
    [Design Rationale] 之所以不用 bool，是为了未来扩展更多终态
    （比如 "sandbox_unavailable" / "llm_quota_exceeded"），
    枚举字符串比 bool 更面向未来。"""
