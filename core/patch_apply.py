"""core/patch_apply.py —— LLM 输出的 Search/Replace 补丁解析与精准合并。

[Design Rationale] 全量重写 3000 行会触发 Token 雪崩与回归；工业界更稳的做法是
「小步 search/replace」—— 与 Cursor / Aider 等产品同构。
"""

from __future__ import annotations

import logging
import re
from typing import Final

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ==========================
# [全局正则常量] 用于识别并剔除 <think> ... </think> 标签（LLM 很容易输出 process reasoning 步骤，脏数据）。
# -----
# 1. 语法层/基础：re.compile 会返回一个已经编译好的正则表达式对象，便于下次反复调用 sub/search，无需重复解释 pattern，加速频繁文本处理场景。
# 2. 业务层/意图：.DOTALL 让 . 可以跨越多行换行符，匹配 tag block；IGNORECASE 确保 model 大小写写错时也能剔除。
# 3. 架构层/防坑：直接用 str.find/replace 处理这些 tag 会且慢且漏。如果正则忘加 DOTALL，换行嵌套就抓不到，会导致污染 patch。
# ----------
_REDACTED_THINKING: Final[re.Pattern[str]] = re.compile(
    r"<think>.*?</think>",
    re.DOTALL | re.IGNORECASE,
)


class PatchPair(BaseModel):
    """单条 search/replace 对（Pydantic 在边界处校验，防脏数据进合并逻辑）。

    设计理念：
      - 用 Pydantic（类型安全 + 自动校验）保证 search 结构合法。
      - 防止脏补丁（空 search、全空格等）渗透 core 逻辑引发难以排查的 bug。
    """

    search: str = Field(..., min_length=1, description="必须在当前代码中唯一匹配的原文片段")  # 【语法层】... 表示该字段为必填参数，min_length=1 防空。
    replace: str = Field(default="", description="替换为的内容，允许为空（删除片段）")           # 【业务层】replace 允许完全为空串，代表“删除”而不是“什么都不改”。

    @field_validator("search")
    @classmethod
    def search_not_only_whitespace(cls, v: str) -> str:
        # 【业务防御】：仅包含空白字符的 search 会导致全文件任意位置都可能被高危覆盖，务必严防。
        if not v.strip():  # strip 会移除字符串前后所有空白，经典 "全空白判定" 写法。
            raise ValueError("search 不能只含空白")  # 结构性硬防：拒绝仅空白片段。
        return v  # 校验通过放行，不做 strip 保持原始形态供精准匹配。


def strip_reasoning_artifacts(raw: str) -> str:
    """清洗推理模型可能泄漏的 `<think>` 等标签内容。

    - 语法层：正则 .sub(pattern, repl, string) 会用 "" 替换所有 <think>...</think> 块。
    - 业务层：极端防御，如果 LLM 把上下文 process reasoning 混进 patch，后续 merge 可能疯狂污染真实代码！
    """
    return _REDACTED_THINKING.sub("", raw)  # 一行流，语义强、性能高，regex 预编译对象效率优于每次 re.sub(pattern, ...)


def extract_tagged_fence_contents(raw: str, tag: str) -> list[str]:
    """
    按 ```{tag} 起始的围栏提取正文，直到下一个 ```（不用跨块 `.*?`）。

    大致流程：
      1. 查找 "```{tag}" 作为围栏开始的位置
      2. 跳过空白和换行，直接定位到代码块正文首字符
      3. 找到下一个独立的 "```"，即块结束（严格遵循 Markdown 围栏语法）
      4. 截取两者之间内容并返回

    防坑说明：
      - “补丁正文里若出现独立一行```，本块会提前终止” —— 与 Markdown 规范完全对齐，防 LLM 补丁将多块糊在一起导致解析错乱。
      - 小白常犯错误：用正则贪婪 .* 或用 split 分隔，这样会把所有 patch block 跨块抓成一坨，引发难查异常。
    """
    blocks: list[str] = []  # 结果存所有匹配到的补丁块
    lowered = raw.lower()   # 为兼容所有大小写，做一次小写副本，搜索时不用反复 lower()
    needle = f"```{tag.lower()}"  # 比如 "```search"，定位每个 block 起点
    idx = 0  # 游标，start searching from 0
    while True:
        pos = lowered.find(needle, idx)  # 在剩余区间查找下一个 "```tag"
        if pos == -1:
            break  # 没有更多块，终止主循环
        start = pos + len(needle)  # 定位到 tag 之后，即 block 内容的起始点（还不一定是正文！）
        # --- 跳过 tag 后的所有 空格/制表符（读取到换行符为止）---
        while start < len(raw) and raw[start] in " \t":  # 一个字符一个字符跳过，防 LLM 输出多余缩进
            start += 1
        # --- 跳过换行符（兼容 Linux/Mac/Windows 回车混用）---
        if start < len(raw) and raw[start] == "\n":      # 标准 LF
            start += 1
        elif start < len(raw) and raw.startswith("\r\n", start):  # Windows CRLF
            start += 2
        # --- 查找当前 block 结束位置 ---
        close = raw.find("```", start)  # 找下一处反引号，严格意义上的 Markdown block 结束
        if close == -1:
            break  # 缺 block 结束符视为补丁异常，直接跳出，目前设计选择为忽略不合规 block
        blocks.append(raw[start:close])  # slice 左闭右开，确保内容精准截取
        idx = close + 3  # 移动游标，严格避免死循环。+3 是因为三个反引号
    return blocks


def parse_search_replace_pairs(raw: str) -> list[tuple[str, str]]:
    """
    从 LLM 输出解析 search/replace 块对；失败则抛 ValueError（由 Actor 节点捕获）。

    设计细节：
      - 步骤 1. 剥离杂质，干净输入
      - 步骤 2. 分别 extract search、replace blocks
      - 步骤 3. 数量检查：必须一一配对——任何地方数量不对都必须 fail-fast
      - 步骤 4. 校验和模型防御：每一对都要 Pydantic 校验，search 不能全空格，否则恶意覆盖
    """
    cleaned = strip_reasoning_artifacts(raw)  # [业务层] 清光 <think> 标签，提高后续解析成功率
    searches = extract_tagged_fence_contents(cleaned, "search")  # 抓所有 search 块内容
    replaces = extract_tagged_fence_contents(cleaned, "replace")  # 抓所有 replace 块内容

    # ---[业务型硬防]---
    if not searches:
        raise ValueError("未找到任何 ```search 围栏；拒绝把裸文本当代码合并")  # 没有 search block 属于致命错，绝不允许 merge

    # ---[数量配对校验]---
    if len(searches) != len(replaces):  # 一对一配对，标准 Patch 设计
        raise ValueError(
            f"```search 与 ```replace 数量不一致：{len(searches)} vs {len(replaces)}"
        )

    # ---[模型输出/结构防御]---
    pairs: list[tuple[str, str]] = []
    for s, r in zip(searches, replaces, strict=True):  # strict=True 防止自动截断 zip，数量不等直接抛 TypeError
        validated = PatchPair(search=s, replace=r)     # 应用上方的 Pydantic 防御
        pairs.append((validated.search, validated.replace))  # 保证 pair 总是符合业务约束
    return pairs


def apply_search_replace_chain(base: str, pairs: list[tuple[str, str]]) -> str:
    """
    顺序应用补丁；search 必须至少出现一次，多处命中时只替换第一处并打日志。

    1. 业务流程：依次从 pairs 里取 search/replace，逐步 apply 到本地变量 out
    2. 防御分支：如果 search 带冗余换行，但没有命中，则允许一次“去换行降级”——极端防御 LLM 末尾习惯性乱加换行导致补丁 apply 失败
    3. 超多命中：search 如果在代码里出现多次，则只替换第一处，并在日志里高亮警告；这可极大降低误伤风险（全自动 merge 风险极高）
    4. 彻底 miss：search 根本没机会 match，可能此补丁与当前代码版本极度不兼容，严格 fail-fast
    """
    out = base  # 每次 apply 都在此变量上递进式变更
    for i, (search, replace) in enumerate(pairs, start=1):  # i 从 1 开始，更利于日志定位 patch 执行序
        needle = search  # 保留原始 search，后续可“降级处理”

        # ---------------------------------------
        # 【业务防御分支】
        # 1. LLM 习惯性把 search 写成「内容\n」（末尾捎带换行），但当前代码行如果没有换行符会直接 apply 失败。
        # 2. 于是允许一次降级，把末尾 \r\n 或 \n 削掉，重新尝试一次匹配。
        # 高可用补丁系统的关键容错姿势！
        # ---------------------------------------
        if needle not in out and needle.endswith("\n"):
            alt = needle.rstrip("\r\n")  # rstrip 同时去掉 \r、\n，兼容 Linux/Windows 换行
            if alt and alt in out:      # 只有一个字符都没有了才不允许降级（全是换行）
                logger.warning("第 %d 条补丁 search 末尾换行未命中，降级为无换行匹配", i)
                needle = alt

        # --------------------------------------
        # 【致命防御】search 匹配不到，补丁与当前环境语义强不兼容，直接 fail-fast，不允许暴力清空代码
        # --------------------------------------
        if needle not in out:
            raise ValueError(f"第 {i} 条补丁的 search 在当前代码中未找到")

        # --------------------------------------
        # 【高危提醒】如果 search 匹配到不止一次（多处出现），只做第一次替换，日志高亮警示
        # --------------------------------------
        count = out.count(needle)
        if count > 1:
            logger.warning(
                "第 %d 条补丁 search 命中 %d 次，仅替换第一处以避免误伤",
                i,
                count,
            )

        # --------------------------------------
        # 【核心替换】标准 str.replace(s, r, 1) 语法，只替换第一个匹配，防止大面积误删除
        # --------------------------------------
        out = out.replace(needle, replace, 1)  # Python 字符串的 replace 第三个参数 n 控制最多替换 n 次

    return out  # 最终所有补丁 apply 完毕，交还给上游逻辑


def merge_patches_into_code(base_code: str, llm_raw: str) -> str:
    """
    解析 + 合并一步完成（供 Actor 节点调用）。

    架构层意义：
      - 单个高阶接口，内部聚合所有边界、日志与校验，暴露给外部的调用语义极简。
      - 上层 Actor 只需专注业务流程，不感知任何底层防陷阱细节。
      - 精细化异常处理，全链路日志贯穿，方便链路调试/事故定位。
    """
    pairs = parse_search_replace_pairs(llm_raw)  # 先 parse，强约束粒度型补丁对
    return apply_search_replace_chain(base_code, pairs)  # 再 apply，严控一致性
