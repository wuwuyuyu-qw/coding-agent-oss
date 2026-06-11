"""core/nodes.py —— LangGraph 的两个核心节点。

节点 1: generate_and_fix_node  (Actor + Reflexion + Search/Replace 合并)
节点 2: run_sandbox_node       (沙盒执行 + 状态写回)

[Design Rationale] 在 LangGraph 范式里，"节点" = 一个无副作用（对外）的纯函数，
签名固定为 `state -> partial_state`。异步版本为 `async def`，由 LangGraph
与 `ainvoke` / `astream` 协同调度。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from openai import APIError as OpenAIAPIError
from openai import APITimeoutError, AsyncOpenAI, RateLimitError

from rag.context_builder import ContextBuilder
from rag.hybrid_retriever import HybridRetriever

from .patch_apply import merge_patches_into_code
from .sandbox import SandboxResult, run_in_sandbox_async
from .state import AgentState

logger = logging.getLogger(__name__)

# ============================================================
# LLM 异步客户端（进程级单例 + AsyncClient 连接池）
# ============================================================

# ====【全局 LLM 客户端管理——底层异步连接的 “壳” 单例/锁/退役任务集合】====================

_llm_async_client: AsyncOpenAI | None = None  # [全局单例] 当前进程全局唯一的 LLM 异步客户端。类型标注用Union（3.10+语法, | None 等价于 Optional[AsyncOpenAI]）。
_llm_client_lock: asyncio.Lock | None = None  # [进程级异步锁] 防止并发初始化 LLM 客户端带来的脏数据/多实例/死锁。注意锁的实例和事件循环 binding 的微妙语义。
_llm_retire_tasks: set[asyncio.Task[None]] = set()  # [退役中的异步任务集合] 这里用 set 去重，防止重复追踪 zombie 退役任务，协助资源闭环。

def _task_done_callback(task: asyncio.Task[None]) -> None:
    """
    后台退役任务的后置收口回调：
      - 保证所有异步异常被主动消耗（避免僵尸 Task 搞崩日志系统 or 内存持续泄漏）。
      - 主动弹出集合中已完成任务，释放引用防止 OOM。
      - 不同异常分支分级（Cancel vs 普通异常），日志按层级归类。
    """
    _llm_retire_tasks.discard(task)  # [业务层] 一旦 task 完成立即移除出集中引用，防止泄漏；set.discard 是幂等操作，task 不在里面也不会抛错。
    try:
        task.result()  # [语法基础] 异步任务的异常收口点（未 .result() 会在 loop GC 时抛 RuntimeWarning）——必须主动消费, 防止“未处理异常”导致的调试盲区。
    except asyncio.CancelledError:
        logger.warning("[LLM] 后台退役任务被取消")  # [分支细致] 明确打出 Cancel，和代码 bug 区分开，减少误报警。
    except Exception:
        logger.exception("[LLM] 后台退役任务执行失败")  # [兜底分支] 全面追踪异常，打全堆栈（大厂事故速查关键）。

def _get_or_init_llm_lock() -> asyncio.Lock:
    """
    [惰性初始化] 以“当前事件循环”为归属的异步锁生成器。
      - 避免多线程/多事件循环（如单测/热更新/new loop）因锁复用而死锁/多实例泄漏。
      - [防坑] Python3.7+ 的 asyncio.Lock 必须挂在单一 event loop 下，旧版本写法/同步锁会出灾难。
    """
    global _llm_client_lock
    if _llm_client_lock is None:  # [判断] 首次访问时才实例化，避免全局提前执行导致锁/loop 捆绑关系错乱。
        _llm_client_lock = asyncio.Lock()  # [高阶] 只在本事件循环生成锁，保证不会跨loop污染。
    return _llm_client_lock

async def _close_client_after_grace_period(
    client: AsyncOpenAI,
    grace_seconds: float = 5.0,
) -> None:
    """
    延迟关闭 LLM 客户端，确保原有飞行中的 API 请求能自洽结束：
      - [业务层] 必须“温和”退役连接池——直接 close 会造成未完成请求断链、下游连接暴毙。
      - [架构] grace_seconds 是“韧性窗口”，5s trade-off 大多数中小型请求能短期收敛，极端滞后任务完全可以让 orchestrator 兜底踢掉。
    """
    await asyncio.sleep(grace_seconds)  # [基础] 挂起本协程（不阻塞其它事件循环），等待业务收尾。
    await client.close()  # [核心调用] 主动释放底层 httpx 资源，彻底回收连接，防 fd 泄漏。

async def reset_llm_client() -> None:
    """
    全局 LLM 客户端的“温和重置”接口（典型应用于密钥轮换、UT 隔离、异常自愈等场景）：
      - [业务层] 置空全局指针，让下次访问强制初始化新客户实例。
      - [语法层] 配合 async with lock 保证在并发高压下只有一个协程可修改状态（防竞态灾难）。
      - [架构] 实际闭包老 client 使用后台异步延迟收敛，降级风险，保证 in-flight 不被猝死，大厂线上事故标准姿势。
    """
    global _llm_async_client
    lock = _get_or_init_llm_lock()
    # === [异步锁语法讲解]: async with 确保仅当前 coroutine 获得独占修改权 ===
    async with lock:
        old = _llm_async_client  # [副本策略] 旧实例移交给下步清理，新实例置空优先保证全局无脏读。
        _llm_async_client = None
    # === [异步状态/协程分流]: 外部协程安全启动后台收敛流程 ===
    if old is not None:  # [边界处理] 没有旧实例时无需清理。
        retire_task = asyncio.create_task(
            _close_client_after_grace_period(old, grace_seconds=5.0)
        )  # [高级语法] create_task 将清理逻辑“无阻塞”提交到事件循环后台，大厂生产避免阻塞主业务流。
        _llm_retire_tasks.add(retire_task)  # [资源管控] 实时跟踪所有退役中的清理任务。
        retire_task.add_done_callback(_task_done_callback)  # [异常闭环] 必须设置 callback 统一消灭异常，死胡同清理。

async def _get_llm_client() -> AsyncOpenAI:
    """
    获取（或延迟初始化）OpenAI 的异步客户端（进程级单例，内置连接池）：
      - [性能设计] 双重校验锁（Double-Checked Locking）：高并发下先无锁快返回，慢路径再 lock, 极致利用单例资源。
      - [架构防坑] 并发初始化防止资源抢占、死锁，一人成功 new 其余立刻走缓存。
      - 线程/协程安全 + 惰性初始化的正确姿势。
    """
    global _llm_async_client
    if _llm_async_client is not None:  # [无锁快通道] 90%+场景下全局已初始化，极低延迟。
        return _llm_async_client
    lock = _get_or_init_llm_lock()
    # === [慢通道核查] 若刚好正初始化/第一次调用，必须串行进入锁 ===
    async with lock:
        if _llm_async_client is None:  # [二次确认] 确保即使多协程 await 后只有一个人初始化（并发魔鬼）。
            _llm_async_client = _build_async_openai_client()
        return _llm_async_client

def _build_async_openai_client() -> AsyncOpenAI:
    """
    构造 AsyncOpenAI 客户端（含自定义连接池和高级网络参数）:
      - [业务层] 严格读取环境变量，便于 CI/云环境安全动态注入。
      - [架构层] 连接池/超时/重试全可控，兼容大流量、非标准代理。
      - [防御编程] 缺少密钥直接 explit raise，快速 fail-fast。
      - 连接池相关参数直接决定系统承压极限、TCP 泄漏、连接风暴等大厂实战硬伤。
    """
    api_key = os.environ.get("OPENAI_API_KEY")  # [源头收敛] 只从环境变量读取，不要代码硬编码/泄漏。
    if not api_key:
        raise RuntimeError(
            "[配置错误] 未检测到 OPENAI_API_KEY 环境变量。\n"
            "请在项目根目录创建 .env 文件并填入:\n"
            "  OPENAI_API_KEY=sk-xxx\n"
            "  OPENAI_BASE_URL=https://api.deepseek.com  (可选)\n"
            "  OPENAI_MODEL=deepseek-chat                (可选)"
        )  # [失误爆破] CI/开发环境经常掉 key，必须第一时间 fail-fast 指南位

    # [底层 httpx 异步连接池配置] —— 不设限会被【易被 DDOS/端口耗尽】
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=100,           # 最大连接数保护，防止池膨胀失控
            max_keepalive_connections=20,  # keepalive 连接池保护，利于微服务健康
            keepalive_expiry=30.0,         # 静默过期主动回收，长连接泄漏保险阀
        ),
        # [架构选型] timeout 拆分细粒度控制：
        # read=30.0: 专指建立连接后，接收 LLM 连续两个数据包(Token)的最大间隔。防止大模型服务内部卡死引发死等。
        # 注：若未来开启 stream=True (流式输出)，模型“长考”不吐 Token 的时间若超过 read 阈值会触发误杀，需针对性放宽此参数。
        timeout=httpx.Timeout(
            timeout=30.0,  # 请求生命周期最大阈值，避免死等
            connect=5.0,   # SYN/ACK 阶段最大等候，预防 TCP 短链雪崩
            read=30.0,     # 读响应缓慢保护
            write=10.0,    # 长 prompt 极端慢写时及时爆破
            pool=5.0,      # 池拥堵阈值（极端高并发下的阻塞锁定窗口）
        ),
    )

    return AsyncOpenAI(
        api_key=api_key,     # [核心灵魂] LLM 的服务访问密钥，不允许代码写死。
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        # [兼容扩展] 支持私有化/运营商网关/第三方 vendor
        timeout=30.0,        # 整体 API 调用总超时
        max_retries=2,       # SDK 级别自动重试，补偿随机闪断/网络毛刺
        http_client=http_client,  # [关键] 强制自定义底层池，拒绝 SDK 默认池黑盒
    )

MODEL_NAME: str = os.environ.get("OPENAI_MODEL", "deepseek-chat")  # 当前 LLM 默认模型名。支持通过环境变量热切模型，不硬编码写死，利于迁移和灰度。


# ============================================================
# Prompt 模板（Search / Replace 补丁模式）
# ============================================================

_SYSTEM_PROMPT_FIRST = """你是一位资深 Python 工程师，专注于编写正确、简洁、可测试的代码。

你的任务：根据用户给出的「原始代码」「当前代码」和「测试用例」，输出**最小化补丁**——
用若干对 ```search 与 ```replace 围栏，把当前代码改成能通过测试的版本。

严格遵循的输出契约：
- 只输出一对或多对 ```search ... ``` 与 ```replace ... ```（数量必须一致，按顺序应用）；
- ```search``` 中的文本必须是「当前代码」里**逐字逐符**可找到的连续片段（含缩进与换行）；
- 不要输出 ```python 全量文件**；不要输出任何围栏外的解释文字；
- 若需多处修改，请输出多对 search/replace，按从上到下的顺序列出。
"""


_SYSTEM_PROMPT_REFLECT = """你是一位精通 Python 调试的工程师，正在进行【迭代式自修复】(Self-Healing)。

【上一轮的失败信息】
你上一次提交的代码在隔离沙盒中执行失败。请按以下三步进行**内心反思**（不要写出来，只在心里走一遍）：
  1. 仔细阅读下方 stderr / Traceback，定位真正的错误根因（不要只看表面行号）；
  2. 思考你上一次的补丁错在了什么"心智模型"上（边界条件？类型转换？算法逻辑？）；
  3. 在本次修复中，**针对性地**改正该处，并避免引入新的回归。

[沙盒返回的报错栈 BEGIN]
{error_log}
[沙盒返回的报错栈 END]

严格遵循的输出契约：
- 只输出 ```search / ```replace 补丁对（可多对），顺序应用；
- search 必须与「当前代码」完全匹配；不要输出全量 ```python 文件；
- 不要输出围栏外的自然语言。
"""

_USER_PROMPT_TEMPLATE = """[原始代码（锚点，只读）]
```python
{original_code}
```

[当前代码（补丁的匹配基准 —— search 必须来自这里）]
```python
{current_code}
```

[必须通过的测试用例]
```python
{test_code}
```

请输出 search/replace 补丁对（仅围栏，无其他文字）。
"""


def _build_repo_rag_context(state: AgentState, *, error_log: str) -> tuple[str, dict[str, Any]]:
    """Build optional repo-level evidence context with fail-open behavior."""

    repo_root = (state.get("repo_root") or "").strip()
    metadata: dict[str, Any] = {
        "enabled": False,
        "indexed_file_count": 0,
        "retrieved_chunk_count": 0,
        "top_evidence_files": [],
        "elapsed_ms": 0.0,
        "reason": "repo_root_missing",
    }
    if not repo_root:
        return "", metadata

    started = time.perf_counter()
    try:
        root_path = Path(repo_root)
        if not root_path.exists() or not root_path.is_dir():
            metadata["reason"] = "repo_root_not_found"
            return "", metadata

        retriever = HybridRetriever()
        results = retriever.retrieve(
            user_request=state.get("user_request", ""),
            error_log=error_log,
            failing_tests=state.get("failing_tests", []),
            target_file=state.get("target_file", ""),
            repo_root=str(root_path),
            top_k=5,
        )
        context = ContextBuilder(max_context_chars=8000).build_context(
            user_request=state.get("user_request", ""),
            error_log=error_log,
            results=results,
            failing_tests=state.get("failing_tests", []),
            target_file=state.get("target_file", ""),
        )
        metadata.update(
            {
                "enabled": True,
                "indexed_file_count": retriever.indexed_file_count,
                "retrieved_chunk_count": len(results),
                "top_evidence_files": [item.chunk.file_path for item in results[:3]],
                "reason": "ok",
            }
        )
        return context, metadata
    except Exception as exc:  # pragma: no cover - exact failures are environment-specific.
        metadata["reason"] = f"rag_failed:{type(exc).__name__}"
        logger.warning("[RAG] Repo-level retrieval failed; falling back without RAG: %s", exc)
        return "", metadata
    finally:
        metadata["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)


# ============================================================
# 节点 1：Actor (生成 / 反思修复)
# ============================================================

# [架构层] Actor 节点负责与 LLM 交互，完成代码的自动修复与补丁合成（最核心链路）。
# [业务层] 负责“基于测试的 Repair”——要么首轮生成，要么基于上轮 error 走 Reflexion 修复。

async def generate_and_fix_node(state: AgentState) -> dict[str, Any]:
    """LangGraph 节点：调用 LLM 生成或修复代码（异步）。"""
    # [语法层] .get(k,v) 防 KeyError，传递默认值，or "" 保证 error_log 一定为字符串类型，防止 None 污染后续分支
    error_log = state.get("error_log", "") or ""  # 取 error_log 字段，无则空串，彻底闭环格式不变量
    retry_count = state.get("retry_count", 0)     # 当前是第几轮尝试，Agent 状态追踪 retry 序列

    # -------- 判断分支：是否走 Reflexion（反思修复）或首次生成 --------
    # [架构层] Agent 必须支持 fail-fast Reflexion：捕捉前一轮报错，定向让 LLM 只“少量地”自修复出错点，防 token 雪崩或语义漂移
    if error_log.strip():  # [业务层] 只要 error_log 不是全空白——说明是“带错误回溯的自修复”
        # [语法层] 字符串 format 注入动态报错堆栈
        system_prompt = _SYSTEM_PROMPT_REFLECT.format(error_log=error_log)
        logger.info(
            "[Actor] 第 %d 轮 Reflexion：注入 %d 字节报错栈",
            retry_count + 1,              # 注意 retry_count+1 表示本次是第几轮（包含当前），利于可观察性
            len(error_log),               # 报错堆栈长度，判断异常增长情况
        )
    else:
        # [业务层] 首次生成 patch，无历史报错信息，直接用初始系统提示，无 error 定向
        system_prompt = _SYSTEM_PROMPT_FIRST
        logger.info("[Actor] 第 1 轮初次生成（无历史报错）")  # 严格首轮日志

    # -------- 选择“补丁应用基线” --------
    # [业务层] 若有 current_code（即上轮已生成补丁的新版），就基于此二次改动；否则回退到 original_code（初次执行/状态脏损时的安全兜底）
    base_code = state.get("current_code") or state["original_code"]  # [架构层] 保证任何状态下都有锚点，绝无 KeyError
    # [语法层] 字符串模板注入，拼出标准 user prompt，单责任单入口，上抛所有三大锚点
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        original_code=state["original_code"],  # 原始全量代码，LLM 能看到上下文
        current_code=base_code,                # 当前补丁基线，search 匹配从这里起步
        test_code=state["test_code"],          # 必须通过的测试用例，这个设计很像 Codex/Reflexion 论文实践
    )
    rag_context, rag_metadata = _build_repo_rag_context(state, error_log=error_log)
    rag_state_update = {"rag_context": rag_context, "rag_metadata": rag_metadata}
    if rag_context:
        user_prompt = (
            f"{user_prompt}\n\n"
            "[Repo-level Evidence Context]\n"
            f"{rag_context}\n"
        )
        logger.info(
            "[RAG] enabled=%s indexed_files=%d retrieved_chunks=%d top_files=%s elapsed_ms=%.2f",
            rag_metadata.get("enabled"),
            rag_metadata.get("indexed_file_count", 0),
            rag_metadata.get("retrieved_chunk_count", 0),
            rag_metadata.get("top_evidence_files", []),
            rag_metadata.get("elapsed_ms", 0.0),
        )
    elif state.get("repo_root"):
        logger.info("[RAG] disabled reason=%s", rag_metadata.get("reason"))

    # --------- 获取“异步 LLM 客户端”全局实例 ----------
    client = await _get_llm_client()  # [语法层] 异步懒加载，用单例锁防止多协程脏实例；内部搞定连接池

    try:
        # --------- 调用 OpenAI Chat Completions API（核心链路） ----------
        # [业务层] 必须传 system prompt + user prompt，两层约束，确保 LLM 的输出结构一以贯之
        # [架构层] temperature 控 0.2 —— 追求收敛、稳定的小步补丁，max_tokens 控死 2k 防单轮雪崩
        # [语法层] await 调用异步 API，释放事件循环，不阻塞上游 Reactor
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},  # 系统（无上下文）指令
                {"role": "user", "content": user_prompt},      # 单轮用户问题
            ],
            temperature=0.2,        # 激进度低，上收敛速度
            max_tokens=2048,        # 限制输出规模，直接防 prompt injection OOM
        )
    # -------- API 层异常防御（网络/风控/硬性故障）--------
    except (APITimeoutError, RateLimitError, OpenAIAPIError) as e:
        # [架构层] 捕获所有已知标准异常，不抛给 State Machine，由节点自身兜底（防止拉垮整个 Agent）
        logger.exception("[Actor] LLM 调用失败：%s", e)  # 打全量 trace，事故溯源
        return {
            "current_code": base_code,    # 回退原代码，防失联
            "error_log": f"[LLM Call Failed] {type(e).__name__}: {e}",  # 明确告知异常
            "retry_count": retry_count + 1,
            **rag_state_update,
        }

    # -------- 业务核心：补丁输出后处理 ----------
    # [业务层] 只要有产出，strip 保守防护（大模型偶尔首末有空行）
    raw_text = (response.choices[0].message.content or "").strip()  # [语法层] 防止 .content None
    if not raw_text:
        # [业务/架构层] LLM 返回空内容直接复用上一版代码，并打日志警示（关键指标！常见事故点如 quota/风控影响）
        logger.warning("[Actor] LLM 返回空内容，复用上一版代码")
        return {
            "current_code": base_code,
            "error_log": "",  # 不记故障但递进 retry
            "retry_count": retry_count + 1,
            **rag_state_update,
        }

    try:
        # [核心] 合并 LLM 输出的 patch 到 base_code，内部包含 search/replace 边界强校验
        merged = merge_patches_into_code(base_code, raw_text)
    except ValueError as e:
        # [架构层] 补丁不合规或全局替换，直接 fail fast，不让 LLM 弄乱底层代码
        logger.warning("[Actor] 补丁解析/合并失败：%s", e)
        return {
            "current_code": base_code,   # 回退上一个可用代码
            "error_log": f"[Patch Merge Failed] {e}",
            "retry_count": retry_count + 1,
            **rag_state_update,
        }

    # [终极返回] 补丁合并通过，记录 retry 供 LangGraph 状态追踪
    return {
        "current_code": merged,  # [语法/业务] 合并后的最新代码
        "error_log": "",         # 本轮无报错，留空明示
        "retry_count": retry_count + 1,  # [架构层] 必须自增，为 Router 熔断提供计数支撑
        **rag_state_update,
    }


# ============================================================
# 节点 2：Sandbox Runner
# ============================================================

# [业务层] 该工具函数负责“智能格式化”代码执行失败后的沙盒返回内容。
# [架构层] 需综合多个信号源，防止漏掉 stderr/stdout 的关键信息。

def _format_sandbox_failure_log(result: SandboxResult) -> str:
    """合并 stdout / stderr，避免 `stderr or stdout` 丢掉另一路信号。"""

    chunks: list[str] = []    # [语法层] 用列表堆叠所有异常输出，末尾统一 join，扩展性强
    if result.exit_code == 137:
        # [业务层] 若 exit_code=137，判定为“沙盒 OOM Killer 或 SIGKILL 外部中止”，需提前输出容易遗漏的大坑线索
        # [经验层] Python 执行 docker 容器超内存后被杀死就是 137，线上遇到超频
        chunks.append(
            "[Sandbox OOM] 进程退出码 137：通常为容器 OOM killer 或 SIGKILL，"
            "请缩小内存占用 / 避免极大中间数据结构。"
        )
    # [架构层] .strip() 防止日志换行影响关键定位
    err_part = (result.stderr or "").strip()   # [业务层] 标准错误；or "" 防 NoneType 闪崩
    out_part = (result.stdout or "").strip()   # [业务层] 标准输出
    if err_part:
        chunks.append(f"--- stderr ---\n{err_part}")  # [业务] stderr 优先，关键 Traceback 信号
    if out_part:
        chunks.append(f"--- stdout ---\n{out_part}")  # 标准输出日志
    if not chunks:
        # [架构层] 沙盒彻底无输出时，明示特殊信号，辅助下游定位意外 silent failure
        return "(空错误日志)"
    return "\n\n".join(chunks)  # [语法层] 列表转多段文本，顺序保存，利于后续搜索


# [业务层] 沙盒 Runner，核心职责：把最新代码和测试丢进容器做安全执行，把执行结果写回状态。
# [架构层] 用异步包装，彻底杜绝 docker SDK 阻塞主事件循环（高并发极其重要）。

async def run_sandbox_node(state: AgentState) -> dict[str, Any]:
    """LangGraph 节点：把当前代码丢进 Docker 沙盒执行（异步包装，不阻塞事件循环）。"""
    # [业务层] 每一轮都判断用“最新已应用的 current_code”，初次就 fallback 到 original_code
    code = state.get("current_code") or state["original_code"]   # [架构层] KeyError 兜底，防用户输入脏值
    test_code = state["test_code"]                               # [语法层] 假定 test_code 必有（agent 架构提前兜底了）

    logger.info("[Sandbox] 开始执行代码，长度=%d", len(code))    # [经验层] 长度监控容易捕捉无意的超大补丁
    # [架构层] run_in_sandbox_async 必须用异步调度，防止子线程死锁拖死主事件循环
    result = await run_in_sandbox_async(code=code, test_code=test_code)
    logger.info(
        "[Sandbox] 执行完成 passed=%s exit_code=%s timed_out=%s",
        result.passed,             # [业务层] 是否执行通过
        result.exit_code,          # 进程退出码，对 debug 沙盒异常极其关键
        result.timed_out,          # 是否超时（死循环/阻塞等高危）
    )

    # -------- 判定执行结果，构造状态回写包 --------
    if result.passed:
        # [架构层] 代码通过所有测试，直接写入 is_passed=True、清空 error_log
        return {
            "is_passed": True,
            "error_log": "",
        }

    # [业务层] 失败时，把综合的错误日志传递到下游 Reflexion，最大化保真度
    return {
        "is_passed": False,
        "error_log": _format_sandbox_failure_log(result),
    }
