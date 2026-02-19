"""基础工具类。"""

from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """工具执行结果。"""

    success: bool
    content: str = ""
    error: str | None = None


class Tool:
    """所有工具的基类。"""

    @property
    def name(self) -> str:
        """工具名称。"""
        raise NotImplementedError

    @property
    def description(self) -> str:
        """工具描述。"""
        raise NotImplementedError

    @property
    def parameters(self) -> dict[str, Any]:
        """工具参数 schema（JSON Schema 格式）。"""
        raise NotImplementedError

    async def execute(self, *args, **kwargs) -> ToolResult:  # type: ignore
        """使用任意参数执行工具。"""
        raise NotImplementedError

    def to_schema(self) -> dict[str, Any]:
        """将工具转换为 Anthropic 工具 schema。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai_schema(self) -> dict[str, Any]:
        """将工具转换为 OpenAI 工具 schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
