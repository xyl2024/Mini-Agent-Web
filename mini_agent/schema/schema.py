from enum import Enum
from typing import Any

from pydantic import BaseModel


class LLMProvider(str, Enum):
    """LLM 提供商类型。"""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class FunctionCall(BaseModel):
    """函数调用详情。"""

    name: str
    arguments: dict[str, Any]  # 函数参数，字典形式


class ToolCall(BaseModel):
    """工具调用结构。"""

    id: str
    type: str  # "function"
    function: FunctionCall


class Message(BaseModel):
    """聊天消息。"""

    role: str  # "system", "user", "assistant", "tool"
    content: str | list[dict[str, Any]]  # 可以是字符串或内容块列表
    thinking: str | None = None  # assistant 消息的扩展思考内容
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # 用于 tool 角色


class TokenUsage(BaseModel):
    """LLM API 响应中的 token 使用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    """LLM 响应。"""

    content: str
    thinking: str | None = None  # 扩展思考块
    tool_calls: list[ToolCall] | None = None
    finish_reason: str
    usage: TokenUsage | None = None  # API 响应中的 token 使用量
