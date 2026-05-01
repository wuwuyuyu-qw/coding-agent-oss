"""tests/test_smart_truncate.py —— 验证两端保留法智能截断与 runner 脚本装配。"""

from __future__ import annotations

import pytest

from core.sandbox import PASS_MARKER, _build_runner_script, _smart_truncate

MARKER = b"\n\n...[Middle Logs Truncated]...\n\n"


class TestSmartTruncateNoOpPath:
    def test_short_input_returns_same_object(self):
        data = b"hello world"
        assert _smart_truncate(data, max_bytes=100) is data

    def test_exact_threshold_returns_same_object(self):
        data = b"x" * 100
        assert _smart_truncate(data, max_bytes=100) is data

    def test_one_byte_below_threshold_returns_same_object(self):
        data = b"x" * 99
        assert _smart_truncate(data, max_bytes=100) is data

    def test_empty_input(self):
        assert _smart_truncate(b"", max_bytes=100) == b""


class TestSmartTruncateTruncationPath:
    def test_result_contains_marker(self):
        data = b"A" * 100_000
        result = _smart_truncate(data, max_bytes=1024)
        assert MARKER in result

    def test_head_preserved(self):
        head = b"FIRST_16_BYTES!!"
        data = head + b"M" * 100_000 + b"TAIL"
        result = _smart_truncate(data, max_bytes=1024)
        assert result.startswith(head)

    def test_tail_preserved(self):
        tail = b"ZeroDivisionError: division by zero\n"
        data = b"H" * 100_000 + tail
        result = _smart_truncate(data, max_bytes=1024)
        assert result.endswith(tail)

    def test_total_length_at_most_max_bytes(self):
        # [Design Rationale] 在 str 码点边界上截断后，总长可能略小于 max_bytes；
        # 对纯 ASCII 仍应精确等于 max_bytes。
        data = b"X" * 200_000
        for max_bytes in [512, 1024, 4096, 65536]:
            result = _smart_truncate(data, max_bytes=max_bytes)
            assert len(result) <= max_bytes, (
                f"max_bytes={max_bytes} 时结果长度应不超过 max_bytes，实际 {len(result)}"
            )
            assert len(result) == max_bytes, "纯 ASCII 输入下字节预算应恰好用尽"

    def test_head_tail_ratio_is_10_90(self):
        data = b"A" * 100_000 + b"B" * 100_000
        max_bytes = 1024
        budget = max_bytes - len(MARKER)
        expected_head = budget // 10
        expected_tail = budget - expected_head

        result = _smart_truncate(data, max_bytes=max_bytes)

        assert result[:expected_head] == b"A" * expected_head
        assert result[-expected_tail:] == b"B" * expected_tail
        assert result[expected_head : expected_head + len(MARKER)] == MARKER


class TestSmartTruncateEdgeCases:
    def test_max_bytes_smaller_than_marker_degrades_to_tail_only(self):
        data = b"X" * 100 + b"END"
        result = _smart_truncate(data, max_bytes=10)
        expect = data[-10:].decode("utf-8", errors="replace").encode("utf-8")
        assert result == expect
        assert MARKER not in result

    def test_max_bytes_equal_marker_length_degrades(self):
        data = b"X" * 1000
        result = _smart_truncate(data, max_bytes=len(MARKER))
        expect = data[-len(MARKER):].decode("utf-8", errors="replace").encode("utf-8")
        assert result == expect

    def test_non_utf8_decode_replace_no_crash(self):
        head = b"\xff\xfe\x00\x01" * 10
        tail = b"\x80\x81\x82" * 10
        data = head + b"M" * 100_000 + tail
        result = _smart_truncate(data, max_bytes=1024)
        assert len(result) <= 1024
        assert MARKER in result
        result.decode("utf-8", errors="strict")

    def test_utf8_multibyte_boundary(self):
        unit = "中".encode("utf-8")
        assert len(unit) == 3
        data = unit * 5000 + b"\nTAIL_MARKER\n"
        result = _smart_truncate(data, max_bytes=256)
        result.decode("utf-8")
        assert b"TAIL_MARKER" in result
        assert len(result) <= 256

    def test_realistic_python_traceback(self):
        head = (
            b"Traceback (most recent call last):\n"
            b'  File "/app/main.py", line 1, in <module>\n'
            b"    result = foo()\n"
        )
        middle_noise = b"DEBUG: intermediate log line\n" * 10_000
        tail = (
            b'  File "/app/lib.py", line 42, in deep_call\n'
            b"    1 / 0\n"
            b"ZeroDivisionError: division by zero\n"
        )
        data = head + middle_noise + tail

        result = _smart_truncate(data, max_bytes=4096)

        assert b"Traceback (most recent call last)" in result
        assert b"ZeroDivisionError: division by zero" in result
        assert MARKER in result
        assert len(result) <= 4096


class TestSmartTruncateRegressionGuard:
    def test_must_not_lose_head_on_long_input(self):
        head_marker = b"UNIQUE_HEAD_SIGNATURE_42"
        data = head_marker + b"x" * 100_000
        result = _smart_truncate(data, max_bytes=1024)
        assert head_marker in result


class TestBuildRunnerScript:
    def test_wraps_user_code_and_test_code(self):
        script = _build_runner_script(
            code="def add(a, b): return a + b",
            test_code="assert add(1, 2) == 3",
        )
        assert "def add(a, b): return a + b" in script
        assert "assert add(1, 2) == 3" in script

    def test_contains_pass_marker(self):
        script = _build_runner_script(code="x = 1", test_code="assert x == 1")
        assert PASS_MARKER in script

    def test_catches_base_exception_not_just_exception(self):
        script = _build_runner_script(code="pass", test_code="pass")
        assert "except BaseException" in script

    def test_uses_exec_with_isolated_namespace(self):
        script = _build_runner_script(code="x = 1", test_code="assert x == 1")
        assert "_isolated_ns" in script
        assert "builtins.exec" in script or "_exec(" in script

    def test_preserves_multiline_test_without_reindent_corruption(self):
        script = _build_runner_script(
            code="x = 1",
            test_code="if x:\n    assert x == 1",
        )
        assert "if x:" in script and "assert x == 1" in script

    def test_exit_codes_distinguish_pass_and_fail(self):
        script = _build_runner_script(code="pass", test_code="pass")
        assert "sys.exit(0)" in script
        assert "sys.exit(1)" in script
