"""文件操作工具。"""

from pathlib import Path
from typing import Any

import tiktoken

from .base import Tool, ToolResult


def truncate_text_by_tokens(
    text: str,
    max_tokens: int,
) -> str:
    """如果文本超过 token 限制则按 token 数量截断。

    当文本超过指定的 token 限制时，执行智能截断，保留开头和结尾部分，同时截断中间部分。

    Args:
        text: 要截断的文本
        max_tokens: 最大 token 限制

    Returns:
        str: 如果超过限制则返回截断后的文本，否则返回原始文本。

    示例:
        >>> text = "very long text..." * 10000
        >>> truncated = truncate_text_by_tokens(text, 64000)
        >>> print(truncated)
    """
    encoding = tiktoken.get_encoding("cl100k_base")
    token_count = len(encoding.encode(text))

    # 如果在限制内则返回原始文本
    if token_count <= max_tokens:
        return text

    # 计算 token/字符比率以进行近似
    char_count = len(text)
    ratio = token_count / char_count

    # 保留头尾模式：为每个分配一半空间（带 5% 安全余量）
    chars_per_half = int((max_tokens / 2) / ratio * 0.95)

    # 截断开头部分：查找最近的换行符
    head_part = text[:chars_per_half]
    last_newline_head = head_part.rfind("\n")
    if last_newline_head > 0:
        head_part = head_part[:last_newline_head]

    # 截断结尾部分：查找最近的换行符
    tail_part = text[-chars_per_half:]
    first_newline_tail = tail_part.find("\n")
    if first_newline_tail > 0:
        tail_part = tail_part[first_newline_tail + 1 :]

    # 组合结果
    truncation_note = f"\n\n... [内容已截断: {token_count} tokens -> ~{max_tokens} tokens 限制] ...\n\n"
    return head_part + truncation_note + tail_part


class ReadTool(Tool):
    """读取文件内容。"""

    def __init__(self, workspace_dir: str = "."):
        """使用工作目录初始化 ReadTool。

        Args:
            workspace_dir: 解析相对路径的基础目录
        """
        self.workspace_dir = Path(workspace_dir).absolute()

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "从文件系统读取文件内容。输出始终包含行号，"
            "格式为 'LINE_NUMBER|LINE_CONTENT'（1-indexed）。支持通过指定行偏移和限制来读取大文件的部分内容。"
            "可以多次并行调用此工具来同时读取不同的文件。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的绝对或相对路径",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始行号（1-indexed）。用于大文件从特定行开始读取",
                },
                "limit": {
                    "type": "integer",
                    "description": "要读取的行数。与 offset 配合使用以分块读取大文件",
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, offset: int | None = None, limit: int | None = None) -> ToolResult:
        """执行读取文件。"""
        try:
            file_path = Path(path)
            # 相对于 workspace_dir 解析相对路径
            if not file_path.is_absolute():
                file_path = self.workspace_dir / file_path

            if not file_path.exists():
                return ToolResult(
                    success=False,
                    content="",
                    error=f"文件未找到: {path}",
                )

            # 读取带行号的文件内容
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()

            # 应用偏移和限制
            start = (offset - 1) if offset else 0
            end = (start + limit) if limit else len(lines)
            if start < 0:
                start = 0
            if end > len(lines):
                end = len(lines)

            selected_lines = lines[start:end]

            # 格式化为带行号（1-indexed）
            numbered_lines = []
            for i, line in enumerate(selected_lines, start=start + 1):
                # 移除末尾换行符以便格式化
                line_content = line.rstrip("\n")
                numbered_lines.append(f"{i:6d}|{line_content}")

            content = "\n".join(numbered_lines)

            # 必要时应用 token 截断
            max_tokens = 32000
            content = truncate_text_by_tokens(content, max_tokens)

            return ToolResult(success=True, content=content)
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))


class WriteTool(Tool):
    """将内容写入文件。"""

    def __init__(self, workspace_dir: str = "."):
        """使用工作目录初始化 WriteTool。

        Args:
            workspace_dir: 解析相对路径的基础目录
        """
        self.workspace_dir = Path(workspace_dir).absolute()

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "将内容写入文件。将完全覆盖现有文件。"
            "对于现有文件，应先使用 read_file 读取文件。"
            "除非明确需要，否则优先编辑现有文件而不是创建新文件。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的绝对或相对路径",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的完整内容（将替换现有内容）",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str) -> ToolResult:
        """执行写入文件。"""
        try:
            file_path = Path(path)
            # 相对于 workspace_dir 解析相对路径
            if not file_path.is_absolute():
                file_path = self.workspace_dir / file_path

            # 如果父目录不存在则创建
            file_path.parent.mkdir(parents=True, exist_ok=True)

            file_path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, content=f"成功写入 {file_path}")
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))


class EditTool(Tool):
    """通过替换文本来编辑文件。"""

    def __init__(self, workspace_dir: str = "."):
        """使用工作目录初始化 EditTool。

        Args:
            workspace_dir: 解析相对路径的基础目录
        """
        self.workspace_dir = Path(workspace_dir).absolute()

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "在文件中执行精确的字符串替换。old_str 必须完全匹配"
            "并在文件中唯一出现，否则操作将失败。"
            "编辑前必须先读取文件。保留原始的精确缩进。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件的绝对或相对路径",
                },
                "old_str": {
                    "type": "string",
                    "description": "要查找和替换的精确字符串（必须在文件中唯一）",
                },
                "new_str": {
                    "type": "string",
                    "description": "替换字符串（用于重构、重命名等）",
                },
            },
            "required": ["path", "old_str", "new_str"],
        }

    async def execute(self, path: str, old_str: str, new_str: str) -> ToolResult:
        """执行编辑文件。"""
        try:
            file_path = Path(path)
            # 相对于 workspace_dir 解析相对路径
            if not file_path.is_absolute():
                file_path = self.workspace_dir / file_path

            if not file_path.exists():
                return ToolResult(
                    success=False,
                    content="",
                    error=f"文件未找到: {path}",
                )

            content = file_path.read_text(encoding="utf-8")

            if old_str not in content:
                return ToolResult(
                    success=False,
                    content="",
                    error=f"文件中未找到文本: {old_str}",
                )

            new_content = content.replace(old_str, new_str)
            file_path.write_text(new_content, encoding="utf-8")

            return ToolResult(success=True, content=f"成功编辑 {file_path}")
        except Exception as e:
            return ToolResult(success=False, content="", error=str(e))
