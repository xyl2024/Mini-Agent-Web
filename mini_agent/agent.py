"""核心 Agent 实现。"""

import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Optional

import tiktoken

from .llm import LLMClient
from .logger import AgentLogger
from .schema import Message
from .tools.base import Tool, ToolResult
from .utils import calculate_display_width


# ANSI 颜色码
class Colors:
    """终端颜色定义"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # 前景色
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    # 亮色
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


class Agent:
    """支持基本工具和 MCP 的单个 Agent。"""

    def __init__(
        self,
        llm_client: LLMClient,
        system_prompt: str,
        tools: list[Tool],
        max_steps: int = 50,
        workspace_dir: str = "./workspace",
        token_limit: int = 80000,  # 超过此值时触发摘要
    ):
        self.llm = llm_client
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps = max_steps
        self.token_limit = token_limit
        self.workspace_dir = Path(workspace_dir)
        # 用于中断 Agent 执行的事件（可由外部设置，如 Esc 键）
        self.cancel_event: Optional[asyncio.Event] = None

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

        # 上次 API 响应的 token 使用量（每次 LLM 调用后更新）
        self.api_total_tokens: int = 0
        # 摘要后跳过 token 检查的标志（避免连续触发）
        self._skip_next_token_check: bool = False

    def add_user_message(self, content: str):
        """向历史记录添加用户消息。"""
        self.messages.append(Message(role="user", content=content))

    def _check_cancelled(self) -> bool:
        """检查 Agent 执行是否已取消。

        Returns:
            如果已取消返回 True，否则返回 False。
        """
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        return False

    def _cleanup_incomplete_messages(self):
        """移除不完整的助手消息及其部分工具结果。

        这确保了取消后消息的一致性，只移除当前步骤的不完整消息，保留已完成的步骤。
        """
        # 查找最后一个助手消息的索引
        last_assistant_idx = -1
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].role == "assistant":
                last_assistant_idx = i
                break

        if last_assistant_idx == -1:
            # 未找到助手消息，无需清理
            return

        # 移除最后一个助手消息及其后面的所有工具结果
        removed_count = len(self.messages) - last_assistant_idx
        if removed_count > 0:
            self.messages = self.messages[:last_assistant_idx]
            print(f"{Colors.DIM}   已清理 {removed_count} 条不完整消息{Colors.RESET}")

    def _estimate_tokens(self) -> int:
        """使用 tiktoken 精确计算消息历史的 token 数量

        使用 cl100k_base 编码器（GPT-4/Claude/M2 兼容）
        """
        try:
            # 使用 cl100k_base 编码器（GPT-4 和大多数现代模型使用）
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # 后备方案：如果 tiktoken 初始化失败，使用简单估算
            return self._estimate_tokens_fallback()

        total_tokens = 0

        for msg in self.messages:
            # 统计文本内容
            if isinstance(msg.content, str):
                total_tokens += len(encoding.encode(msg.content))
            elif isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict):
                        # 将字典转换为字符串进行计算
                        total_tokens += len(encoding.encode(str(block)))

            # 统计思考内容
            if msg.thinking:
                total_tokens += len(encoding.encode(msg.thinking))

            # 统计工具调用
            if msg.tool_calls:
                total_tokens += len(encoding.encode(str(msg.tool_calls)))

            # 每条消息的元数据开销（约 4 个 token）
            total_tokens += 4

        return total_tokens

    def _estimate_tokens_fallback(self) -> int:
        """后备 token 估算方法（当 tiktoken 不可用时）"""
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

        # 粗略估算：平均 2.5 个字符 = 1 个 token
        return int(total_chars / 2.5)

    async def _summarize_messages(self):
        """消息历史摘要：当 token 超过限制时，对用户消息之间的对话进行摘要

        策略（Agent 模式）：
        - 保留所有用户消息（这些是用户意图）
        - 总结每对用户-用户之间的内容（Agent 执行过程）
        - 如果最后一轮仍在执行（有 agent/工具消息但没有下一个用户），也进行摘要
        - 结构：system -> user1 -> summary1 -> user2 -> summary2 -> user3 -> summary3（如果正在执行）

        摘要触发条件（满足任一即可）：
        - 本地 token 估算超过限制
        - API 报告的 total_tokens 超过限制
        """
        # 如果刚完成摘要则跳过检查（等待下次 LLM 调用更新 api_total_tokens）
        if self._skip_next_token_check:
            self._skip_next_token_check = False
            return

        estimated_tokens = self._estimate_tokens()

        # 同时检查本地估算和 API 报告的 token
        should_summarize = estimated_tokens > self.token_limit or self.api_total_tokens > self.token_limit

        # 如果两者都未超过，则不需要摘要
        if not should_summarize:
            return

        print(
            f"\n{Colors.BRIGHT_YELLOW}📊 Token 使用量 - 本地估算: {estimated_tokens}, API 报告: {self.api_total_tokens}, 限制: {self.token_limit}{Colors.RESET}"
        )
        print(f"{Colors.BRIGHT_YELLOW}🔄 触发消息历史摘要...{Colors.RESET}")

        # 查找所有用户消息索引（跳过系统提示）
        user_indices = [i for i, msg in enumerate(self.messages) if msg.role == "user" and i > 0]

        # 至少需要 1 条用户消息才能执行摘要
        if len(user_indices) < 1:
            print(f"{Colors.BRIGHT_YELLOW}⚠️  消息不足，无法摘要{Colors.RESET}")
            return

        # 构建新的消息列表
        new_messages = [self.messages[0]]  # 保留系统提示
        summary_count = 0

        # 遍历每个用户消息并总结其后的执行过程
        for i, user_idx in enumerate(user_indices):
            # 添加当前用户消息
            new_messages.append(self.messages[user_idx])

            # 确定要摘要的消息范围
            # 如果是最后一个用户，则到消息列表末尾；否则到下一个用户之前
            if i < len(user_indices) - 1:
                next_user_idx = user_indices[i + 1]
            else:
                next_user_idx = len(self.messages)

            # 提取该轮的执行消息
            execution_messages = self.messages[user_idx + 1 : next_user_idx]

            # 如果该轮有执行消息，则进行摘要
            if execution_messages:
                summary_text = await self._create_summary(execution_messages, i + 1)
                if summary_text:
                    summary_message = Message(
                        role="user",
                        content=f"[助手执行摘要]\n\n{summary_text}",
                    )
                    new_messages.append(summary_message)
                    summary_count += 1

        # 替换消息列表
        self.messages = new_messages

        # 跳过下次 token 检查以避免连续触发摘要
        # （api_total_tokens 将在下次 LLM 调用后更新）
        self._skip_next_token_check = True

        new_tokens = self._estimate_tokens()
        print(f"{Colors.BRIGHT_GREEN}✓ 摘要完成，本地 token: {estimated_tokens} → {new_tokens}{Colors.RESET}")
        print(f"{Colors.DIM}  结构: system + {len(user_indices)} 条用户消息 + {summary_count} 个摘要{Colors.RESET}")
        print(f"{Colors.DIM}  注意: API token 计数将在下次 LLM 调用后更新{Colors.RESET}")

    async def _create_summary(self, messages: list[Message], round_num: int) -> str:
        """为单轮执行创建摘要

        Args:
            messages: 要摘要的消息列表
            round_num: 轮次编号

        Returns:
            摘要文本
        """
        if not messages:
            return ""

        # 构建摘要内容
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

        # 调用 LLM 生成简洁摘要
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

            summary_text = response.content
            print(f"{Colors.BRIGHT_GREEN}✓ 第 {round_num} 轮摘要生成成功{Colors.RESET}")
            return summary_text

        except Exception as e:
            print(f"{Colors.BRIGHT_RED}✗ 第 {round_num} 轮摘要生成失败: {e}{Colors.RESET}")
            # 失败时使用简单文本摘要
            return summary_content

    async def run(self, cancel_event: Optional[asyncio.Event] = None) -> str:
        """执行 Agent 循环，直到任务完成或达到最大步数。

        Args:
            cancel_event: 可选的 asyncio.Event，可设置为执行。
                          设置取消后，Agent 将在下一个安全检查点停止
                          （在完成当前步骤后，以保持消息一致性）。

        Returns:
            最终响应内容，或错误消息（包括取消消息）。
        """
        # 设置取消事件（也可以在调用 run() 之前通过 self.cancel_event 设置）
        if cancel_event is not None:
            self.cancel_event = cancel_event

        # 开始新运行，初始化日志文件
        self.logger.start_new_run()
        print(f"{Colors.DIM}📝 日志文件: {self.logger.get_log_file_path()}{Colors.RESET}")

        step = 0
        run_start_time = perf_counter()

        while step < self.max_steps:
            # 每步开始时检查取消
            if self._check_cancelled():
                self._cleanup_incomplete_messages()
                cancel_msg = "任务已被用户取消。"
                print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {cancel_msg}{Colors.RESET}")
                return cancel_msg

            step_start_time = perf_counter()
            # 检查并摘要消息历史，防止上下文溢出
            await self._summarize_messages()

            # 带适当宽度计算的步骤头部
            BOX_WIDTH = 58
            step_text = f"{Colors.BOLD}{Colors.BRIGHT_CYAN}💭 步骤 {step + 1}/{self.max_steps}{Colors.RESET}"
            step_display_width = calculate_display_width(step_text)
            padding = max(0, BOX_WIDTH - 1 - step_display_width)  # -1 为前导空格

            print(f"\n{Colors.DIM}╭{'─' * BOX_WIDTH}╮{Colors.RESET}")
            print(f"{Colors.DIM}│{Colors.RESET} {step_text}{' ' * padding}{Colors.DIM}│{Colors.RESET}")
            print(f"{Colors.DIM}╰{'─' * BOX_WIDTH}╯{Colors.RESET}")

            # 获取 LLM 调用的工具列表
            tool_list = list(self.tools.values())

            # 记录 LLM 请求并直接使用 Tool 对象调用 LLM
            self.logger.log_request(messages=self.messages, tools=tool_list)

            try:
                response = await self.llm.generate(messages=self.messages, tools=tool_list)
            except Exception as e:
                # 检查是否是重试耗尽错误
                from .retry import RetryExhaustedError

                if isinstance(e, RetryExhaustedError):
                    error_msg = f"LLM 调用在 {e.attempts} 次重试后失败\n最后错误: {str(e.last_exception)}"
                    print(f"\n{Colors.BRIGHT_RED}❌ 重试失败:{Colors.RESET} {error_msg}")
                else:
                    error_msg = f"LLM 调用失败: {str(e)}"
                    print(f"\n{Colors.BRIGHT_RED}❌ 错误:{Colors.RESET} {error_msg}")
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

            # 如果有思考内容则打印
            if response.thinking:
                print(f"\n{Colors.BOLD}{Colors.MAGENTA}🧠 思考:{Colors.RESET}")
                print(f"{Colors.DIM}{response.thinking}{Colors.RESET}")

            # 打印助手响应
            if response.content:
                print(f"\n{Colors.BOLD}{Colors.BRIGHT_BLUE}🤖 助手:{Colors.RESET}")
                print(f"{response.content}")

            # 检查任务是否完成（没有工具调用）
            if not response.tool_calls:
                step_elapsed = perf_counter() - step_start_time
                total_elapsed = perf_counter() - run_start_time
                print(f"\n{Colors.DIM}⏱️  步骤 {step + 1} 完成，耗时 {step_elapsed:.2f}s（总计: {total_elapsed:.2f}s）{Colors.RESET}")
                return response.content

            # 执行工具前检查取消
            if self._check_cancelled():
                self._cleanup_incomplete_messages()
                cancel_msg = "任务已被用户取消。"
                print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {cancel_msg}{Colors.RESET}")
                return cancel_msg

            # 执行工具调用
            for tool_call in response.tool_calls:
                tool_call_id = tool_call.id
                function_name = tool_call.function.name
                arguments = tool_call.function.arguments

                # 工具调用头部
                print(f"\n{Colors.BRIGHT_YELLOW}🔧 工具调用:{Colors.RESET} {Colors.BOLD}{Colors.CYAN}{function_name}{Colors.RESET}")

                # 参数（格式化显示）
                print(f"{Colors.DIM}   参数:{Colors.RESET}")
                # 截断每个参数值以避免输出过长
                truncated_args = {}
                for key, value in arguments.items():
                    value_str = str(value)
                    if len(value_str) > 200:
                        truncated_args[key] = value_str[:200] + "..."
                    else:
                        truncated_args[key] = value
                args_json = json.dumps(truncated_args, indent=2, ensure_ascii=False)
                for line in args_json.split("\n"):
                    print(f"   {Colors.DIM}{line}{Colors.RESET}")

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
                        # 捕获工具执行期间的所有异常，转换为失败的 ToolResult
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

                # 打印结果
                if result.success:
                    result_text = result.content
                    if len(result_text) > 300:
                        result_text = result_text[:300] + f"{Colors.DIM}...{Colors.RESET}"
                    print(f"{Colors.BRIGHT_GREEN}✓ 结果:{Colors.RESET} {result_text}")
                else:
                    print(f"{Colors.BRIGHT_RED}✗ 错误:{Colors.RESET} {Colors.RED}{result.error}{Colors.RESET}")

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
                    cancel_msg = "任务已被用户取消。"
                    print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {cancel_msg}{Colors.RESET}")
                    return cancel_msg

            step_elapsed = perf_counter() - step_start_time
            total_elapsed = perf_counter() - run_start_time
            print(f"\n{Colors.DIM}⏱️  步骤 {step + 1} 完成，耗时 {step_elapsed:.2f}s（总计: {total_elapsed:.2f}s）{Colors.RESET}")

            step += 1

        # 达到最大步数
        error_msg = f"任务在 {self.max_steps} 步后无法完成。"
        print(f"\n{Colors.BRIGHT_YELLOW}⚠️  {error_msg}{Colors.RESET}")
        return error_msg

    def get_history(self) -> list[Message]:
        """获取消息历史。"""
        return self.messages.copy()
