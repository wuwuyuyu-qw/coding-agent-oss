"""eval_runner.py —— 批量异步评测入口（HumanEval / SWE-bench Lite 风格）。

用法示例：
    python eval_runner.py --dataset sample_humaneval.jsonl --concurrency 4

[Design Rationale] 本脚本与「在线服务」解耦：只依赖 `build_graph()` 与 `ainvoke`，
演示如何把 Agent 当作纯异步任务丢进 Semaphore 限流的协程池。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from core.state import AgentState
from main import build_graph

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eval_runner")


def _default_sample_rows() -> list[dict[str, Any]]:
    """内嵌极简样本，便于无数据集文件时演示流水线。"""
    return [
        {
            "task_id": "sample_fib_off_by_one",
            "canonical_solution": "",
            "prompt": "fix fib",
            "entry_point": "fibonacci",
            "code": '''def fibonacci(n: int) -> int:
    if n <= 0:
        return 0
    if n == 1:
        return 1
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return a
''',
            "test": '''assert fibonacci(0) == 0
assert fibonacci(1) == 1
assert fibonacci(5) == 5
''',
        }
    ]


async def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """异步线程中读取 JSONL，避免在协程里阻塞磁盘 IO（大文件时更明显）。"""
    return await asyncio.to_thread(_load_jsonl_sync, path)


def _load_jsonl_sync(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _build_initial_state(row: dict[str, Any]) -> AgentState:
    """将 HumanEval 风格字段映射到 AgentState（可按实际数据集调整）。"""
    code = row.get("code") or row.get("prompt") or ""
    test = row.get("test") or row.get("tests") or ""
    if not code or not test:
        raise ValueError(f"任务 {row.get('task_id', '?')} 缺少 code/test 字段")
    return {
        "original_code": code,
        "current_code": code,
        "test_code": test,
        "error_log": "",
        "retry_count": 0,
        "max_retries": int(row.get("max_retries", 3)),
        "is_passed": False,
        "final_status": "",
    }


async def evaluate_one(
    app: Any,
    row: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    task_id = str(row.get("task_id", uuid.uuid4()))
    async with semaphore:
        state = _build_initial_state(row)
        config = {
            "configurable": {"thread_id": f"eval:{task_id}"},
            "recursion_limit": 25,
        }
        try:
            final = await app.ainvoke(state, config=config)
        except Exception as e:
            logger.exception("任务 %s 执行异常", task_id)
            return {"task_id": task_id, "ok": False, "error": repr(e)}
        ok = final.get("final_status") == "success" and final.get("is_passed") is True
        return {
            "task_id": task_id,
            "ok": ok,
            "final_status": final.get("final_status"),
            "retry_count": final.get("retry_count"),
        }


async def run_evaluation(
    dataset_path: Path | None,
    concurrency: int,
) -> None:
    if dataset_path and dataset_path.is_file():
        rows = await load_jsonl(dataset_path)
        logger.info("已加载数据集 %s，共 %d 条", dataset_path, len(rows))
    else:
        rows = _default_sample_rows()
        logger.warning("未找到数据集文件，使用内嵌 sample（%d 条）", len(rows))

    app = build_graph()
    sem = asyncio.Semaphore(max(1, concurrency))
    tasks = [evaluate_one(app, row, sem) for row in rows]
    results = await asyncio.gather(*tasks)

    passed = sum(1 for r in results if r.get("ok"))
    logger.info("评测结束：%d / %d 通过", passed, len(results))
    for r in results:
        logger.info("%s", r)


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Self-Healing Agent 批量异步评测")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="HumanEval / SWE-bench Lite 风格 JSONL 路径",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="最大并发 ainvoke 数（建议与 Docker / API 配额匹配）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    asyncio.run(run_evaluation(args.dataset, args.concurrency))


if __name__ == "__main__":
    main()
