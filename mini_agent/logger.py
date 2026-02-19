"""Agent 运行日志记录器"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import Message, ToolCall


class AgentLogger:
    """Agent 运行日志记录器

    负责记录每次 Agent 运行的完整交互过程，包括：
    - LLM 请求和响应
    - 工具调用和结果
    """

    def __init__(self):
        """初始化日志记录器

        日志存储在 ~/.mini-agent/log/ 目录
        """
        # 使用 ~/.mini-agent/log/ 目录存储日志
        self.log_dir = Path.home() / ".mini-agent" / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = None
        self.log_index = 0

    def start_new_run(self):
        """开始新运行，创建新日志文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"agent_run_{timestamp}.log"
        self.log_file = self.log_dir / log_filename
        self.log_index = 0

        # 写入日志头部
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"Agent 运行日志 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n\n")

    def log_request(self, messages: list[Message], tools: list[Any] | None = None):
        """记录 LLM 请求

        Args:
            messages: 消息列表
            tools: 工具列表（可选）
        """
        self.log_index += 1

        # 构建完整的请求数据结构
        request_data = {
            "messages": [],
            "tools": [],
        }

        # 将消息转换为 JSON 可序列化格式
        for msg in messages:
            msg_dict = {
                "role": msg.role,
                "content": msg.content,
            }
            if msg.thinking:
                msg_dict["thinking"] = msg.thinking
            if msg.tool_calls:
                msg_dict["tool_calls"] = [tc.model_dump() for tc in msg.tool_calls]
            if msg.tool_call_id:
                msg_dict["tool_call_id"] = msg.tool_call_id
            if msg.name:
                msg_dict["name"] = msg.name

            request_data["messages"].append(msg_dict)

        # 只记录工具名称
        if tools:
            request_data["tools"] = [tool.name for tool in tools]

        # 格式化为 JSON
        content = "LLM 请求:\n\n"
        content += json.dumps(request_data, indent=2, ensure_ascii=False)

        self._write_log("REQUEST", content)

    def log_response(
        self,
        content: str,
        thinking: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        finish_reason: str | None = None,
    ):
        """记录 LLM 响应

        Args:
            content: 响应内容
            thinking: 思考内容（可选）
            tool_calls: 工具调用列表（可选）
            finish_reason: 结束原因（可选）
        """
        self.log_index += 1

        # 构建完整的响应数据结构
        response_data = {
            "content": content,
        }

        if thinking:
            response_data["thinking"] = thinking

        if tool_calls:
            response_data["tool_calls"] = [tc.model_dump() for tc in tool_calls]

        if finish_reason:
            response_data["finish_reason"] = finish_reason

        # 格式化为 JSON
        log_content = "LLM 响应:\n\n"
        log_content += json.dumps(response_data, indent=2, ensure_ascii=False)

        self._write_log("RESPONSE", log_content)

    def log_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_success: bool,
        result_content: str | None = None,
        result_error: str | None = None,
    ):
        """记录工具执行结果

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            result_success: 是否成功
            result_content: 结果内容（成功时）
            result_error: 错误消息（失败时）
        """
        self.log_index += 1

        # 构建完整的工具执行结果数据结构
        tool_result_data = {
            "tool_name": tool_name,
            "arguments": arguments,
            "success": result_success,
        }

        if result_success:
            tool_result_data["result"] = result_content
        else:
            tool_result_data["error"] = result_error

        # 格式化为 JSON
        content = "工具执行:\n\n"
        content += json.dumps(tool_result_data, indent=2, ensure_ascii=False)

        self._write_log("TOOL_RESULT", content)

    def _write_log(self, log_type: str, content: str):
        """写入日志条目

        Args:
            log_type: 日志类型（REQUEST, RESPONSE, TOOL_RESULT）
            content: 日志内容
        """
        if self.log_file is None:
            return

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n" + "-" * 80 + "\n")
            f.write(f"[{self.log_index}] {log_type}\n")
            f.write(f"时间戳: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}\n")
            f.write("-" * 80 + "\n")
            f.write(content + "\n")

    def get_log_file_path(self) -> Path:
        """获取当前日志文件路径"""
        return self.log_file
