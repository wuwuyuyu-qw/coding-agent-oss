from __future__ import annotations

import pytest

from patching.errors import PatchParseError
from patching.models import PatchType
from patching.parser import PatchParser


def test_parse_single_search_replace_block() -> None:
    raw = """```search
old
```
```replace
new
```"""

    plan = PatchParser().parse(raw, default_target_file="app.py")

    assert plan.patch_type == PatchType.SEARCH_REPLACE
    assert len(plan.edits) == 1
    assert plan.edits[0].file_path == "app.py"
    assert plan.edits[0].search == "old\n"


def test_parse_multiple_search_replace_blocks() -> None:
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

    plan = PatchParser().parse(raw, default_target_file="app.py")

    assert len(plan.edits) == 2
    assert plan.target_files == ["app.py"]


def test_parse_empty_search_replace_fails() -> None:
    raw = """```search

```
```replace
new
```"""

    with pytest.raises(PatchParseError):
        PatchParser().parse(raw, default_target_file="app.py")


def test_parse_unified_diff_changed_files() -> None:
    raw = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
-old
+new
"""

    plan = PatchParser().parse(raw)

    assert plan.patch_type == PatchType.UNIFIED_DIFF
    assert plan.target_files == ["app.py"]
    assert plan.unified_diff is not None
    assert len(plan.unified_diff.hunks) == 1


def test_malformed_diff_returns_clear_error() -> None:
    raw = """--- a/app.py
+++ b/app.py
old
"""

    with pytest.raises(PatchParseError, match="no hunks"):
        PatchParser().parse(raw)

