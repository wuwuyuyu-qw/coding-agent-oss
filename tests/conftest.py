"""tests/conftest.py —— pytest 全局 fixture 与路径引导。

[Design Rationale] 为什么需要这个文件？
    - 项目代码在仓库根（main.py / core/），测试在 tests/ 子目录；
    - pytest 默认只把 rootdir 加入 sys.path，某些 IDE 直接跑单测时
      会把 tests/ 当成 rootdir，导致 `import core.xxx` 找不到；
    - 这里显式把项目根目录 prepend 到 sys.path，保证任何入口都能跑通。

conftest.py 是 pytest 的"魔法文件"：
    - 同目录及子目录下所有测试自动共享其中的 fixture；
    - 不需要在每个测试文件里 import，收敛了跨文件依赖。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---- 把项目根目录加入 sys.path ----
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """[Design Rationale] 每个测试开始前隔离掉可能污染环境的真实 .env 配置。

    - 防止本地跑测试时读到真的 OPENAI_API_KEY 而去打真实 LLM；
    - 防止 CI 上 .env 缺失触发非预期的 RuntimeError。

    注：个别测试需要真实删除/设置某个变量时，在测试内再用 monkeypatch 覆盖。
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    yield
