"""tests/test_patch_apply.py —— Search/Replace 解析与合并。"""

from __future__ import annotations

import pytest

from core.patch_apply import (
    apply_search_replace_chain,
    merge_patches_into_code,
    parse_search_replace_pairs,
    strip_reasoning_artifacts,
)


def test_strip_redacted_thinking():
    raw = "pre<think>secret</think>```search\na\n```"
    cleaned = strip_reasoning_artifacts(raw)
    assert "secret" not in cleaned
    assert "```search" in cleaned


def test_parse_pairs_success():
    raw = """```search
old
```
```replace
new
```"""
    pairs = parse_search_replace_pairs(raw)
    assert pairs == [("old\n", "new\n")]


def test_parse_multiple_pairs():
    raw = """```search
a
```
```replace
b
```
```search
c
```
```replace
d
```"""
    pairs = parse_search_replace_pairs(raw)
    assert pairs == [("a\n", "b\n"), ("c\n", "d\n")]


def test_parse_mismatch_raises():
    raw = """```search
only
```"""
    with pytest.raises(ValueError, match="replace"):
        parse_search_replace_pairs(raw)


def test_parse_none_raises():
    with pytest.raises(ValueError, match="search"):
        parse_search_replace_pairs("no fences here")


def test_apply_chain():
    base = "hello OLD world OLD end"
    out = apply_search_replace_chain(base, [("OLD", "new")])
    assert out == "hello new world OLD end"


def test_merge_integration():
    base = "def f():\n    return 1\n"
    llm = """```search
    return 1
```
```replace
    return 2
```"""
    assert merge_patches_into_code(base, llm) == "def f():\n    return 2\n"


def test_code_fence_inside_search_does_not_use_dot_star_cross_fence():
    raw = (
        "```search\n"
        'x = """doc"""\n'
        "```\n"
        "```replace\n"
        "x = 1\n"
        "```\n"
    )
    # 若用 `.*?``` 跨围栏贪婪，会把 replace 吃掉；按围栏扫描应仍能解析。
    pairs = parse_search_replace_pairs(raw)
    assert len(pairs) == 1
    assert "doc" in pairs[0][0]
