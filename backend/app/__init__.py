"""AI Panel Studio 后端核心逻辑。

这里只放不依赖 IO 的纯逻辑：调度、人设、共识合并。
它们全部是纯函数或不可变数据结构 —— 这样才有测的必要，
也才能在一条 SSE 事件流之外单独验证。
"""

__all__ = ["models", "scheduler", "personas", "consensus"]
