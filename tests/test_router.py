"""tests/test_router.py —— 熔断路由器的纯函数单测。

为什么要为 circuit_breaker_router 单独写单测？
    - 它是路由大脑，业务规则最密集；
    - 它是纯函数，单测极其快且覆盖率高；
    - 把"路由规则"当独立单元测试，一旦未来改熔断策略（比如加指数退避、
      加 SLO 降级），测试红灯会立刻提醒——文档化测试胜过文档。
"""

from __future__ import annotations

import pytest

from core.router import (
    DEFAULT_MAX_RETRIES,
    ROUTE_CIRCUIT_BREAK,
    ROUTE_RETRY,
    ROUTE_SUCCESS,
    circuit_breaker_router,
)


class TestSuccessPath:
    """成功路径是最高优先级的快速返回。"""

    def test_is_passed_true_returns_success(self):
        state = {"is_passed": True, "retry_count": 0, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_SUCCESS

    def test_success_wins_over_retry_count(self):
        """即使 retry_count 已超限，只要 is_passed=True 仍应走成功路径。"""
        state = {"is_passed": True, "retry_count": 99, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_SUCCESS


class TestCircuitBreakPath:
    def test_exceeds_max_retries_triggers_break(self):
        state = {"is_passed": False, "retry_count": 3, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_CIRCUIT_BREAK

    def test_greater_than_max_also_breaks(self):
        """用 >= 而不是 == 是防御性设计，
        应对 max_retries 被运行时改小的极端情况。"""
        state = {"is_passed": False, "retry_count": 10, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_CIRCUIT_BREAK


class TestRetryPath:
    def test_below_max_retries_returns_retry(self):
        state = {"is_passed": False, "retry_count": 1, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_RETRY

    def test_zero_retries_returns_retry(self):
        state = {"is_passed": False, "retry_count": 0, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_RETRY


class TestDefensiveDefaults:
    """防御性编程：state 字段缺失时不能 KeyError。"""

    def test_missing_is_passed_defaults_to_retry(self):
        state = {"retry_count": 0, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_RETRY

    def test_missing_max_retries_uses_default(self):
        # retry_count=DEFAULT_MAX_RETRIES 应当刚好触发熔断
        state = {"is_passed": False, "retry_count": DEFAULT_MAX_RETRIES}
        assert circuit_breaker_router(state) == ROUTE_CIRCUIT_BREAK

    def test_missing_retry_count_defaults_zero(self):
        state = {"is_passed": False, "max_retries": 3}
        assert circuit_breaker_router(state) == ROUTE_RETRY

    def test_completely_empty_state_does_not_crash(self):
        """即使是 {} 也应返回合法分支，不能抛 KeyError。"""
        result = circuit_breaker_router({})
        assert result in {ROUTE_SUCCESS, ROUTE_RETRY, ROUTE_CIRCUIT_BREAK}


@pytest.mark.parametrize(
    "retry_count, max_retries, expected",
    [
        (0, 3, ROUTE_RETRY),
        (1, 3, ROUTE_RETRY),
        (2, 3, ROUTE_RETRY),
        (3, 3, ROUTE_CIRCUIT_BREAK),
        (4, 3, ROUTE_CIRCUIT_BREAK),
        (0, 1, ROUTE_RETRY),
        (1, 1, ROUTE_CIRCUIT_BREAK),
    ],
)
def test_retry_threshold_matrix(retry_count, max_retries, expected):
    """参数化矩阵，一次性覆盖熔断阈值的所有关键点。"""
    state = {"is_passed": False, "retry_count": retry_count, "max_retries": max_retries}
    assert circuit_breaker_router(state) == expected
