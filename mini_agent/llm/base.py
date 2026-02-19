"""LLM 客户端基类。"""

from abc import ABC, abstractmethod
from typing import Any

from ..retry import RetryConfig
from ..schema import LLMResponse, Message


class LLMClientBase(ABC):
    """LLM 客户端抽象基类。

    该类定义了所有 LLM 客户端必须实现的接口，
    无论底层 API 协议如何（Anthropic、OpenAI 等）。
    """

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        retry_config: RetryConfig | None = None,
    ):
        """初始化 LLM 客户端。

        Args:
            api_key: 用于认证的 API 密钥
            api_base: API 的基础 URL
            model: 使用的模型名称
            retry_config: 可选的重试配置
        """
        self.api_key = api_key
        self.api_base = api_base
        self.model = model
        self.retry_config = retry_config or RetryConfig()

        # 用于跟踪重试次数的回调
        self.retry_callback = None

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
    ) -> LLMResponse:
        """从 LLM 生成响应。

        Args:
            messages: 对话消息列表
            tools: 可选的 Tool 对象或字典列表

        Returns:
            包含生成内容、思考和工具调用的 LLMResponse
        """
        pass

    @abstractmethod
    def _prepare_request(
        self,
        messages: list[Message],
        tools: list[Any] | None = None,
    ) -> dict[str, Any]:
        """准备 API 请求负载。

        Args:
            messages: 对话消息列表
            tools: 可用的工具列表

        Returns:
            包含请求负载的字典
        """
        pass

    @abstractmethod
    def _convert_messages(self, messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        """将内部消息格式转换为 API 特定格式。

        Args:
            messages: 内部 Message 对象列表

        Returns:
            (system_message, api_messages) 元组
        """
        pass
