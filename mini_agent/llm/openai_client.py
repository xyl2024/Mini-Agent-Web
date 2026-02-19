"""OpenAI LLM 客户端实现。"""

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from ..retry import RetryConfig, async_retry
from ..schema import FunctionCall, LLMResponse, Message, TokenUsage, ToolCall
from .base import LLMClientBase

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClientBase):
    """使用 OpenAI 协议的 LLM 客户端。

    该客户端使用官方 OpenAI SDK，支持：
    - 推理内容（通过 reasoning_split=True）
    - 工具调用
    - 重试逻辑
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.minimaxi.com/v1",
        model: str = "MiniMax-M2.5",
        retry_config: RetryConfig | None = None,
    ):
        """初始化 OpenAI 客户端。

        Args:
            api_key: 用于认证的 API 密钥
            api_base: API 的基础 URL（默认值：MiniMax OpenAI 端点）
            model: 使用的模型名称（默认值：MiniMax-M2.5）
            retry_config: 可选的重试配置
        """
        super().__init__(api_key, api_base, model, retry_config)

        # 初始化 OpenAI 客户端
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
        )

    async def _make_api_request(
        self,
        api_messages: list[dict[str, Any]],
        tools: list[Any] | None = None,
    ) -> Any:
        """执行 API 请求（可重试的核心方法）。

        Args:
            api_messages: OpenAI 格式的消息列表
            tools: 可选的工具列表

        Returns:
            OpenAI ChatCompletion 响应（包含完整使用量信息）

        Raises:
            Exception: API 调用失败
        """
        params = {
            "model": self.model,
            "messages": api_messages,
            # 启用 reasoning_split 以分离思考内容
            "extra_body": {"reasoning_split": True},
        }

        if tools:
            params["tools"] = self._convert_tools(tools)

        # 使用 OpenAI SDK 的 chat.completions.create
        response = await self.client.chat.completions.create(**params)
        # 返回完整响应以访问使用量信息
        return response

    def _convert_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """将工具转换为 OpenAI 格式。

        Args:
            tools: Tool 对象或字典列表

        Returns:
            OpenAI 字典格式的工具列表
        """
        result = []
        for tool in tools:
            if isinstance(tool, dict):
                # 如果已经是字典，检查是否是 OpenAI 格式
                if "type" in tool and tool["type"] == "function":
                    result.append(tool)
                else:
                    # 假设是 Anthropic 格式，转换为 OpenAI
                    result.append(
                        {
                            "type": "function",
                            "function": {
                                "name": tool["name"],
                                "description": tool["description"],
                                "parameters": tool["input_schema"],
                            },
                        }
                    )
            elif hasattr(tool, "to_openai_schema"):
                # 具有 to_openai_schema 方法的 Tool 对象
                result.append(tool.to_openai_schema())
            else:
                raise TypeError(f"不支持的工具类型: {type(tool)}")
        return result

    def _convert_messages(self, messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        """将内部消息转换为 OpenAI 格式。

        Args:
            messages: 内部 Message 对象列表

        Returns:
            (system_message, api_messages) 元组
            注意：OpenAI 将系统消息包含在消息数组中
        """
        api_messages = []

        for msg in messages:
            if msg.role == "system":
                # OpenAI 将系统消息包含在消息数组中
                api_messages.append({"role": "system", "content": msg.content})
                continue

            # 对于 user 消息
            if msg.role == "user":
                api_messages.append({"role": "user", "content": msg.content})

            # 对于 assistant 消息
            elif msg.role == "assistant":
                assistant_msg = {"role": "assistant"}

                # 如果有 content，添加 content
                if msg.content:
                    assistant_msg["content"] = msg.content

                # 如果有 tool_calls，添加工具调用
                if msg.tool_calls:
                    tool_calls_list = []
                    for tool_call in msg.tool_calls:
                        tool_calls_list.append(
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": json.dumps(tool_call.function.arguments),
                                },
                            }
                        )
                    assistant_msg["tool_calls"] = tool_calls_list

                # 重要：如果有 thinking，添加 reasoning_details
                # 这对交错思考（Interleaved Thinking）正常工作至关重要！
                # 完整的 response_message（包括 reasoning_details）必须
                # 保存在消息历史中，并在下一轮传递回给模型。
                # 这确保了模型的思维链不会中断。
                if msg.thinking:
                    assistant_msg["reasoning_details"] = [{"text": msg.thinking}]

                api_messages.append(assistant_msg)

            # 对于 tool 结果消息
            elif msg.role == "tool":
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": msg.content,
                    }
                )

        return None, api_messages

    def _prepare_request(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
    ) -> dict[str, Any]:
        """准备 OpenAI API 请求。

        Args:
            messages: 对话消息列表
            tools: 可用的工具列表

        Returns:
            包含请求参数的字典
        """
        _, api_messages = self._convert_messages(messages)

        return {
            "api_messages": api_messages,
            "tools": tools,
        }

    def _parse_response(self, response: Any) -> LLMResponse:
        """将 OpenAI 响应解析为 LLMResponse。

        Args:
            response: OpenAI ChatCompletion 响应（完整的响应对象）

        Returns:
            LLMResponse 对象
        """
        # 从响应中获取消息
        message = response.choices[0].message

        # 提取文本内容
        text_content = message.content or ""

        # 从 reasoning_details 提取思考内容
        thinking_content = ""
        if hasattr(message, "reasoning_details") and message.reasoning_details:
            # reasoning_details 是一个推理块列表
            for detail in message.reasoning_details:
                if hasattr(detail, "text"):
                    thinking_content += detail.text

        # 提取工具调用
        tool_calls = []
        if message.tool_calls:
            for tool_call in message.tool_calls:
                # 从 JSON 字符串解析参数
                arguments = json.loads(tool_call.function.arguments)

                tool_calls.append(
                    ToolCall(
                        id=tool_call.id,
                        type="function",
                        function=FunctionCall(
                            name=tool_call.function.name,
                            arguments=arguments,
                        ),
                    )
                )

        # 从响应中提取 token 使用量
        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )

        return LLMResponse(
            content=text_content,
            thinking=thinking_content if thinking_content else None,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason="stop",  # OpenAI 不在消息中提供 finish_reason
            usage=usage,
        )

    async def generate(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
    ) -> LLMResponse:
        """从 OpenAI LLM 生成响应。

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
                request_params["api_messages"],
                request_params["tools"],
            )
        else:
            # 不使用重试
            response = await self._make_api_request(
                request_params["api_messages"],
                request_params["tools"],
            )

        # 解析并返回响应
        return self._parse_response(response)
