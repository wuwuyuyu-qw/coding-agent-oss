# Coding Agent OSS Resume Bullets

## 项目名称

**Coding Agent OSS：面向真实代码仓库的自动代码修复 Agent 系统**

## 技术栈

**Python、LangGraph、OpenAI API、Repo-level RAG、Tool Calling、MCP、Pytest、Git、Search/Replace Patch、Unified Diff、Benchmark Report**

## 一句话项目概述

设计并实现一个面向真实代码仓库的自动代码修复 Coding Agent，支持 **仓库级上下文检索、工具调用、安全补丁应用、Diff 审计、Benchmark 评测和 MCP Server Adapter**。

## 5 条中文简历描述

1. 设计并实现 **Repo-level RAG** 模块，支持对本地代码仓库进行文件扫描、函数/类级切片和混合检索，根据 **错误日志、失败测试、目标文件和用户需求** 自动召回相关代码上下文，并注入补丁生成流程。

2. 构建内部 **Tool Calling Framework**，抽象 **ToolDefinition、ToolRegistry、ToolExecutor、ToolResult、ToolTrace**，支持 `list_files`、`read_file`、`search_code`、`grep_code`、`git_diff`、`run_tests` 等工具，并实现 **路径隔离、敏感文件拦截、命令 allowlist、timeout 和输出截断**。

3. 设计 **Patch Engine + Diff Audit**，支持 **Search/Replace** 与 **Unified Diff** 两类补丁格式，实现补丁解析、唯一命中校验、dry-run、正式应用、失败回滚和 git diff 审计，降低 LLM 误改代码风险。

4. 构建 **Benchmark + Report** 管线，支持 mock mode 批量运行 toy 修复任务，统计 **成功率、失败类型、Patch 成功率、RAG 召回数、工具调用数、Diff 变更规模** 等指标，并生成 **JSON / Markdown / CSV** 报告。

5. 实现 **MCP Server Adapter**，将内部 ToolRegistry 中的只读/低风险工具通过 **MCP tools** 暴露给外部 Agent 客户端，复用已有安全边界，并默认不暴露高风险 `apply_patch` 能力，`run_tests` 需显式开启。

## 60 秒面试介绍

这个项目是一个面向真实代码仓库的自动代码修复 Coding Agent。它不是只把一段代码丢给 LLM，而是围绕真实工程场景做了完整的上下文、工具、安全和评测闭环。

核心流程是：先用 **Repo-level RAG** 根据错误日志、失败测试、目标文件和用户需求召回相关代码；再用内部 **Tool Calling Framework** 做确定性的文件读取、代码搜索、git diff 和可选测试；LLM 输出补丁后进入 **Patch Engine**，经过解析、唯一命中校验、dry-run、正式应用和失败回滚；最后通过 **Diff Audit** 记录变更范围。项目还加入了 mock mode 的 **Benchmark + Report**，可以生成 JSON、Markdown、CSV 报告，并通过 **MCP Server Adapter** 把安全工具暴露给外部 Agent 客户端。

## 120 秒面试介绍

Coding Agent OSS 是我围绕“真实代码仓库自动修复”设计的一套工程化 Agent 系统。它的目标不是展示一次性的 demo，而是把 LLM 修复代码所需要的上下文检索、工具调用、补丁安全、变更审计和评测报告都拆成可测试的模块。

第一部分是 **Repo-level RAG**。系统会扫描本地仓库，对 Python 和 Java-like 文件做轻量切片，并基于用户需求、错误日志、失败测试和目标文件做混合检索，把相关上下文注入补丁生成流程。

第二部分是内部 **Tool Calling Framework**。我抽象了 ToolDefinition、ToolRegistry、ToolExecutor、ToolResult 和 ToolTrace，内置 list_files、read_file、search_code、grep_code、git_diff、run_tests 等工具。这里重点做了路径隔离、敏感文件拦截、测试命令 allowlist、timeout 和输出截断，避免工具能力变成不受控的 shell。

第三部分是 **Patch Engine + Diff Audit**。LLM 输出不会直接落盘，而是先解析成 Search/Replace 或基础 Unified Diff，再做唯一命中校验、dry-run、应用和失败回滚。应用后用 git diff 做变更审计，统计 changed files、added/deleted lines 和可疑变更信号。

第四部分是 **Benchmark + Report** 和 **MCP Server Adapter**。Benchmark 当前是小型 toy benchmark，不夸大成 SWE-bench，但可以复现地跑 mock 修复任务并生成 JSON、Markdown、CSV 报告。MCP Server Adapter 则把内部只读/低风险工具暴露成 MCP tools，默认不暴露 apply_patch，run_tests 也需要显式开启。

这个项目体现的是我对 Agent 工程化的理解：LLM 只是其中一个环节，真正要可靠，需要上下文、工具、安全边界、补丁策略、审计和评测一起工作。

## 项目亮点

- 模块边界清晰：RAG、tools、patching、benchmark、mcp_adapter 分层独立，便于测试和扩展。
- 工程安全意识强：路径隔离、敏感文件拦截、allowlist、timeout、输出截断、dry-run 和 rollback 都有对应实现。
- 兼容渐进式增强：没有 `repo_root` 时仍保留原单文件修复流程，repo-aware 能力是增量接入。
- 可观测性较完整：ToolTrace、PatchApplyResult、PatchAudit、BenchmarkReport 都能为调试和复盘提供结构化数据。
- 面向生态集成：通过 MCP Server Adapter 将内部安全工具暴露给外部 Agent 客户端。

## 项目不足

- Repo-level RAG 当前主要是轻量关键词 / 混合检索，不是完整向量数据库检索。
- Java 代码切片是轻量规则实现，不是 Tree-sitter 级别的语法解析。
- Benchmark 目前是 toy task 级别，任务数量有限，不能代表大规模真实修复能力。
- Real mode benchmark 还不是完整的批量真实 Agent 评测。
- MCP 当前只做 Server Adapter 和 stdio transport，尚未做 Client 端到端集成测试。
