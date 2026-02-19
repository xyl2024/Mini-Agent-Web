"""Anthropic LLM 客户端实现。"""

import logging
from typing import Any

import anthropic

from ..retry import RetryConfig, async_retry
from ..schema import FunctionCall, LLMResponse, Message, TokenUsage, ToolCall
from .base import LLMClientBase

logger = logging.getLogger(__name__)


class AnthropicClient(LLMClientBase):
    """使用 Anthropic 协议的 LLM 客户端。

    该客户端使用官方 Anthropic SDK，支持：
    - 扩展思考内容
    - 工具调用
    - 重试逻辑
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.minimaxi.com/anthropic",
        model: str = "MiniMax-M2.5",
        retry_config: RetryConfig | None = None,
    ):
        """初始化 Anthropic 客户端。

        Args:
            api_key: 用于认证的 API 密钥
            api_base: API 的基础 URL（默认值：MiniMax Anthropic 端点）
            model: 使用的模型名称（默认值：MiniMax-M2.5）
            retry_config: 可选的重试配置
        """
        super().__init__(api_key, api_base, model, retry_config)

        # 初始化 Anthropic 异步客户端
        self.client = anthropic.AsyncAnthropic(
            base_url=api_base,
            api_key=api_key,
            default_headers={"Authorization": f"Bearer {api_key}"},
        )

    async def _make_api_request(
        self,
        system_message: str | None,
        api_messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
    ) -> anthropic.types.Message:
        """执行 API 请求（可重试的核心方法）。

        Args:
            system_message: 可选的系统消息
            api_messages: Anthropic 格式的消息列表
            tools: 可选的工具列表

        Returns:
            Anthropic Message 响应

        Raises:
            Exception: API 调用失败
        """
        params = {
            "model": self.model,
            "max_tokens": 16384,
            "messages": api_messages,
        }

        if system_message:
            params["system"] = system_message

        if tools:
            params["tools"] = self._convert_tools(tools)

        # 使用 Anthropic SDK 的异步 messages.create
        response = await self.client.messages.create(**params)
        return response

    def _convert_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """将工具转换为 Anthropic 格式。

        Anthropic 工具格式：
        {
            "name": "tool_name",
            "description": "Tool description",
            "input_schema": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }

        Args:
            tools: Tool 对象或字典列表

        Returns:
            Anthropic 字典格式的工具列表
        """
        result = []
        for tool in tools:
            if isinstance(tool, dict):
                result.append(tool)
            elif hasattr(tool, "to_schema"):
                # 具有 to_schema 方法的 Tool 对象
                result.append(tool.to_schema())
            else:
                raise TypeError(f"不支持的工具类型: {type(tool)}")
        return result

    def _convert_messages(self, messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        """将内部消息转换为 Anthropic 格式。

        Args:
            messages: 内部 Message 对象列表

        Returns:
            (system_message, api_messages) 元组
        """
        system_message = None
        api_messages = []

        for msg in messages:
            if msg.role == "system":
                system_message = msg.content
                continue

            # 对于 user 和 assistant 消息
            if msg.role in ["user", "assistant"]:
                # 处理带有 thinking 或 tool_calls 的 assistant 消息
                if msg.role == "assistant" and (msg.thinking or msg.tool_calls):
                    # 为带有 thinking 和/或 tool calls 的 assistant 构建内容块
                    content_blocks = []

                    # 如果有 thinking，添加 thinking 块
                    if msg.thinking:
                        content_blocks.append({"type": "thinking", "thinking": msg.thinking})

                    # 如果有 content，添加文本内容
                    if msg.content:
                        content_blocks.append({"type": "text", "text": msg.content})

                    # 添加 tool use 块
                    if msg.tool_calls:
                        for tool_call in msg.tool_calls:
                            content_blocks.append(
                                {
                                    "type": "tool_use",
                                    "id": tool_call.id,
                                    "name": tool_call.function.name,
                                    "input": tool_call.function.arguments,
                                }
                            )

                    api_messages.append({"role": "assistant", "content": content_blocks})
                else:
                    api_messages.append({"role": msg.role, "content": msg.content})

            # 对于 tool 结果消息
            elif msg.role == "tool":
                # Anthropic 使用 user 角色和 tool_result 内容块
                api_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content,
                            }
                        ],
                    }
                )

        return system_message, api_messages

    def _prepare_request(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
    ) -> dict[str, Any]:
        """准备 Anthropic API 请求。

        Args:
            messages: 对话消息列表
            tools: 可用的工具列表

        Returns:
            包含请求参数的字典
        """
        system_message, api_messages = self._convert_messages(messages)

        return {
            "system_message": system_message,
            "api_messages": api_messages,
            "tools": tools,
        }

    def _parse_response(self, response: anthropic.types.Message) -> LLMResponse:
        """将 Anthropic 响应解析为 LLMResponse。

        Args:
            response: Anthropic Message 响应

        Returns:
            LLMResponse 对象
        """
        # 提取文本内容、thinking 和工具调用
        text_content = ""
        thinking_content = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "thinking":
                thinking_content += block.thinking
            elif block.type == "tool_use":
                # 解析 Anthropic tool_use 块
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        type="function",
                        function=FunctionCall(
                            name=block.name,
                            arguments=block.input,
                        ),
                    )
                )

        # 从响应中提取 token 使用量
        # Anthropic 使用量包括：input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens
        usage = None
        if hasattr(response, "usage") and response.usage:
            input_tokens = response.usage.input_tokens or 0
            output_tokens = response.usage.output_tokens or 0
            cache_read_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0
            cache_creation_tokens = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            total_input_tokens = input_tokens + cache_read_tokens + cache_creation_tokens
            usage = TokenUsage(
                prompt_tokens=total_input_tokens,
                completion_tokens=output_tokens,
                total_tokens=total_input_tokens + output_tokens,
            )

        return LLMResponse(
            content=text_content,
            thinking=thinking_content if thinking_content else None,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason=response.stop_reason or "stop",
            usage=usage,
        )

    async def generate(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
    ) -> LLMResponse:
        """从 Anthropic LLM 生成响应。

        Args:
            messages: 对话消息列表
            tools: 可用的工具列表

        Returns:
            包含生成内容的 LLMResponse
        """
        # 准备请求
        request_params = self._prepare_request(messages, tools)

        # 使用重试逻辑发起 API 请求
        if self.retry_config.enabled:
            # 应用重试逻辑
            retry_decorator = async_retry(config=self.retry_config, on_retry=self.retry_callback)
            api_call = retry_decorator(self._make_api_request)
            response = await api_call(
                request_params["system_message"],
                request_params["api_messages"],
                request_params["tools"],
            )
        else:
            # 不使用重试
            response = await self._make_api_request(
                request_params["system_message"],
                request_params["api_messages"],
                request_params["tools"],
            )

        # 解析并返回响应
        return self._parse_response(response)
