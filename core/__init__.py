"""core 包 —— Self-Healing PR Agent 的核心组件。

[Design Rationale]
    把 state / sandbox / nodes / router 拆成独立模块，是为了贯彻
    "单一职责原则"（SRP）：每个文件只负责一个轴向的关注点，
    便于单元测试与未来横向扩展（比如再加一个 static_analysis_node）。
"""
