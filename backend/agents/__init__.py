"""多维 Agent 分析层。

统一 JSON 契约：每个 Agent 输出 AgentOutput（score/direction/confidence/summary），
编排层只消费契约字段，不解析自由文本。LLM Agent 失败时回退量化基线并标记 fallback。
"""
