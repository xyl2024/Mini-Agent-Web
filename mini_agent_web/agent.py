"""支持回调钩子的 Agent 实现。"""

import asyncio
import json
from abc import ABC, abstractmethod
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

import tiktoken

from mini_agent.llm import LLMClient
from mini_agent.logger import AgentLogger
from mini_agent.schema import Message
from mini_agent.tools.base import Tool, ToolResult
from mini_agent.utils import calculate_display_width


class AgentCallbacks(ABC):
    """Agent 回调接口。"""

    @abstractmethod
    async def on_thinking(self, thinking: str):
        """当 Agent 产生思考内容时调用。"""
        pass

    @abstractmethod
    async def on_message(self, content: str):
        """当 Agent 产生回复时调用。"""
        pass

    @abstractmethod
    async def on_tool_call(self, tool_name: str, arguments: dict[str, Any], call_id: str):
        """当 Agent 调用工具时调用。"""
        pass

    @abstractmethod
    async def on_tool_result(self, tool_name: str, success: bool, content: str):
        """当工具执行完成时调用。"""
        pass

    @abstractmethod
    async def on_done(self, final_content: str):
        """当 Agent 完成执行时调用。"""
        pass

    @abstractmethod
    async def on_error(self, error: str):
        """当发生错误时调用。"""
        pass


class WebAgentCallbacks(AgentCallbacks):
    """Web 模式回调实现，通过 WebSocket 推送消息。"""

    def __init__(self, session_id: str, connection_manager):
        """初始化回调。

        Args:
            session_id: 会话 ID
            connection_manager: WebSocket 连接管理器
        """
        self.session_id = session_id
        self.manager = connection_manager

    async def on_thinking(self, thinking: str):
        """发送思考内容。"""
        await self.manager.send_thinking(self.session_id, thinking)

    async def on_message(self, content: str):
        """发送助手消息。"""
        await self.manager.send_message(self.session_id, "assistant", content)

    async def on_tool_call(self, tool_name: str, arguments: dict[str, Any], call_id: str):
        """发送工具调用。"""
        await self.manager.send_tool_call(self.session_id, tool_name, arguments, call_id)

    async def on_tool_result(self, tool_name: str, success: bool, content: str):
        """发送工具结果。"""
        await self.manager.send_tool_result(self.session_id, tool_name, success, content)

    async def on_done(self, final_content: str):
        """发送完成消息。"""
        await self.manager.send_done(self.session_id, final_content)

    async def on_error(self, error: str):
        """发送错误消息。"""
        await self.manager.send_error(self.session_id, error)


class Agent:
    """支持回调钩子的 Agent。"""

    def __init__(
        self,
        llm_client: LLMClient,
        system_prompt: str,
        tools: list[Tool],
        max_steps: int = 50,
        workspace_dir: str = "./workspace",
        token_limit: int = 80000,
        callbacks: Optional[AgentCallbacks] = None,
    ):
        """初始化 Agent。

        Args:
            llm_client: LLM 客户端
            system_prompt: 系统提示词
            tools: 可用工具列表
            max_steps: 最大执行步数
            workspace_dir: 工作空间目录
            token_limit: Token 限制
            callbacks: 可选的回调处理器
        """
        self.llm = llm_client
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps = max_steps
        self.token_limit = token_limit
        self.workspace_dir = Path(workspace_dir)
        self.cancel_event: Optional[asyncio.Event] = None
        self.callbacks = callbacks

        # 确保工作目录存在
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # 如果系统提示中还没有工作目录信息，则注入
        if "Current Workspace" not in system_prompt:
            workspace_info = f"\n\n## 当前工作目录\n你当前工作目录为: `{self.workspace_dir.absolute()}`\n所有相对路径都将以此目录为基准解析。"
            system_prompt = system_prompt + workspace_info

        self.system_prompt = system_prompt

        # 初始化消息历史
        self.messages: list[Message] = [Message(role="system", content=system_prompt)]

        # 初始化日志记录器
        self.logger = AgentLogger()

        # 上次 API 响应的 token 使用量
        self.api_total_tokens: int = 0
        self._skip_next_token_check: bool = False

    def add_user_message(self, content: str):
        """向历史记录添加用户消息。"""
        self.messages.append(Message(role="user", content=content))

    def _check_cancelled(self) -> bool:
        """检查 Agent 执行是否已取消。"""
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        return False

    def _cleanup_incomplete_messages(self):
        """移除不完整的助手消息及其部分工具结果。"""
        last_assistant_idx = -1
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].role == "assistant":
                last_assistant_idx = i
                break

        if last_assistant_idx == -1:
            return

        removed_count = len(self.messages) - last_assistant_idx
        if removed_count > 0:
            self.messages = self.messages[:last_assistant_idx]

    def _estimate_tokens(self) -> int:
        """使用 tiktoken 精确计算消息历史的 token 数量。"""
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return self._estimate_tokens_fallback()

        total_tokens = 0

        for msg in self.messages:
            if isinstance(msg.content, str):
                total_tokens += len(encoding.encode(msg.content))
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        total_tokens += len(encoding.encode(str(block)))

            if msg.thinking:
                total_tokens += len(encoding.encode(msg.thinking))

            if msg.tool_calls:
                total_tokens += len(encoding.encode(str(msg.tool_calls)))

            total_tokens += 4

        return total_tokens

    def _estimate_tokens_fallback(self) -> int:
        """后备 token 估算方法。"""
        total_chars = 0
        for msg in self.messages:
            if isinstance(msg.content, str):
                total_chars += len(msg.content)
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        total_chars += len(str(block))

            if msg.thinking:
                total_chars += len(msg.thinking)

            if msg.tool_calls:
                total_chars += len(str(msg.tool_calls))

        return int(total_chars / 2.5)

    async def _summarize_messages(self):
        """消息历史摘要。"""
        if self._skip_next_token_check:
            self._skip_next_token_check = False
            return

        estimated_tokens = self._estimate_tokens()
        should_summarize = (
            estimated_tokens > self.token_limit or self.api_total_tokens > self.token_limit
        )

        if not should_summarize:
            return

        user_indices = [i for i, msg in enumerate(self.messages) if msg.role == "user" and i > 0]

        if len(user_indices) < 1:
            return

        new_messages = [self.messages[0]]
        summary_count = 0

        for i, user_idx in enumerate(user_indices):
            new_messages.append(self.messages[user_idx])

            if i < len(user_indices) - 1:
                next_user_idx = user_indices[i + 1]
            else:
                next_user_idx = len(self.messages)

            execution_messages = self.messages[user_idx + 1 : next_user_idx]

            if execution_messages:
                summary_text = await self._create_summary(execution_messages, i + 1)
                if summary_text:
                    summary_message = Message(
                        role="user",
                        content=f"[助手执行摘要]\n\n{summary_text}",
                    )
                    new_messages.append(summary_message)
                    summary_count += 1

        self.messages = new_messages
        self._skip_next_token_check = True

    async def _create_summary(self, messages: list[Message], round_num: int) -> str:
        """为单轮执行创建摘要。"""
        if not messages:
            return ""

        summary_content = f"第 {round_num} 轮执行过程:\n\n"
        for msg in messages:
            if msg.role == "assistant":
                content_text = msg.content if isinstance(msg.content, str) else str(msg.content)
                summary_content += f"助手: {content_text}\n"
                if msg.tool_calls:
                    tool_names = [tc.function.name for tc in msg.tool_calls]
                    summary_content += f"  → 调用工具: {', '.join(tool_names)}\n"
            elif msg.role == "tool":
                result_preview = msg.content if isinstance(msg.content, str) else str(msg.content)
                summary_content += f"  ← 工具返回: {result_preview}...\n"

        try:
            summary_prompt = f"""请简洁总结以下 Agent 执行过程:

{summary_content}

要求:
1. 专注于完成的任务和调用的工具
2. 保留关键执行结果和重要发现
3. 简洁清晰，不超过 1000 字
4. 使用中文
5. 不包含"用户"相关内容，只总结 Agent 的执行过程"""

            summary_msg = Message(role="user", content=summary_prompt)
            response = await self.llm.generate(
                messages=[
                    Message(
                        role="system",
                        content="你是一个擅长总结 Agent 执行过程的助手。",
                    ),
                    summary_msg,
                ]
            )

            return response.content

        except Exception:
            return summary_content

    async def run(self, cancel_event: Optional[asyncio.Event] = None) -> str:
        """执行 Agent 循环，直到任务完成或达到最大步数。

        Args:
            cancel_event: 可选的取消事件

        Returns:
            最终响应内容，或错误消息
        """
        if cancel_event is not None:
            self.cancel_event = cancel_event

        self.logger.start_new_run()

        step = 0
        run_start_time = perf_counter()

        while step < self.max_steps:
            # 检查取消
            if self._check_cancelled():
                self._cleanup_incomplete_messages()
                if self.callbacks:
                    await self.callbacks.on_error("任务已被用户取消。")
                return "任务已被用户取消。"

            step_start_time = perf_counter()
            await self._summarize_messages()

            # 获取工具列表
            tool_list = list(self.tools.values())
            self.logger.log_request(messages=self.messages, tools=tool_list)

            try:
                response = await self.llm.generate(messages=self.messages, tools=tool_list)
            except Exception as e:
                from mini_agent.retry import RetryExhaustedError

                if isinstance(e, RetryExhaustedError):
                    error_msg = f"LLM 调用在 {e.attempts} 次重试后失败\n最后错误: {str(e.last_exception)}"
                else:
                    error_msg = f"LLM 调用失败: {str(e)}"

                if self.callbacks:
                    await self.callbacks.on_error(error_msg)
                return error_msg

            # 累加 API 报告的 token 使用量
            if response.usage:
                self.api_total_tokens = response.usage.total_tokens

            # 记录 LLM 响应
            self.logger.log_response(
                content=response.content,
                thinking=response.thinking,
                tool_calls=response.tool_calls,
                finish_reason=response.finish_reason,
            )

            # 添加助手消息
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                thinking=response.thinking,
                tool_calls=response.tool_calls,
            )
            self.messages.append(assistant_msg)

            # 回调：思考内容
            if response.thinking and self.callbacks:
                await self.callbacks.on_thinking(response.thinking)

            # 回调：助手回复
            if response.content and self.callbacks:
                await self.callbacks.on_message(response.content)

            # 检查任务是否完成
            if not response.tool_calls:
                step_elapsed = perf_counter() - step_start_time
                total_elapsed = perf_counter() - run_start_time
                if self.callbacks:
                    await self.callbacks.on_done(response.content)
                return response.content

            # 工具执行前检查取消
            if self._check_cancelled():
                self._cleanup_incomplete_messages()
                if self.callbacks:
                    await self.callbacks.on_error("任务已被用户取消。")
                return "任务已被用户取消。"

            # 执行工具调用
            for tool_call in response.tool_calls:
                tool_call_id = tool_call.id
                function_name = tool_call.function.name
                arguments = tool_call.function.arguments

                # 回调：工具调用
                if self.callbacks:
                    await self.callbacks.on_tool_call(function_name, arguments, tool_call_id)

                # 执行工具
                if function_name not in self.tools:
                    result = ToolResult(
                        success=False,
                        content="",
                        error=f"未知工具: {function_name}",
                    )
                else:
                    try:
                        tool = self.tools[function_name]
                        result = await tool.execute(**arguments)
                    except Exception as e:
                        import traceback

                        error_detail = f"{type(e).__name__}: {str(e)}"
                        error_trace = traceback.format_exc()
                        result = ToolResult(
                            success=False,
                            content="",
                            error=f"工具执行失败: {error_detail}\n\n堆栈跟踪:\n{error_trace}",
                        )

                # 记录工具执行结果
                self.logger.log_tool_result(
                    tool_name=function_name,
                    arguments=arguments,
                    result_success=result.success,
                    result_content=result.content if result.success else None,
                    result_error=result.error if not result.success else None,
                )

                # 回调：工具结果
                if self.callbacks:
                    content = result.content if result.success else f"错误: {result.error}"
                    await self.callbacks.on_tool_result(function_name, result.success, content)

                # 添加工具结果消息
                tool_msg = Message(
                    role="tool",
                    content=result.content if result.success else f"错误: {result.error}",
                    tool_call_id=tool_call_id,
                    name=function_name,
                )
                self.messages.append(tool_msg)

                # 每次工具执行后检查取消
                if self._check_cancelled():
                    self._cleanup_incomplete_messages()
                    if self.callbacks:
                        await self.callbacks.on_error("任务已被用户取消。")
                    return "任务已被用户取消。"

            step += 1

        # 达到最大步数
        error_msg = f"任务在 {self.max_steps} 步后无法完成。"
        if self.callbacks:
            await self.callbacks.on_error(error_msg)
        return error_msg

    def get_history(self) -> list[Message]:
        """获取消息历史。"""
        return self.messages.copy()
