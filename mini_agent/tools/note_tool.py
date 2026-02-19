"""会话笔记工具 - 让 Agent 记录和回忆重要信息。

此工具允许 Agent：
- 在会话期间记录关键点和重要信息
- 回忆之前记录的笔记
- 在 Agent 执行链中维护上下文
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult


class SessionNoteTool(Tool):
    """用于记录和回忆会话笔记的工具。

    Agent 可以使用此工具：
    - 在会话期间记录重要事实、决策或上下文
    - 回忆之前会话中的信息
    - 随着时间积累知识

    Agent 使用示例：
    - record_note("用户偏好简洁的回复")
    - record_note("项目使用 Python 3.12 和 async/await")
    - recall_notes() -> 检索所有记录的笔记
    """

    def __init__(self, memory_file: str = "./workspace/.agent_memory.json"):
        """初始化会话笔记工具。

        Args:
            memory_file: 笔记存储文件的路径
        """
        self.memory_file = Path(memory_file)
        # 延迟加载：文件和目录仅在首次记录笔记时创建

    @property
    def name(self) -> str:
        return "record_note"

    @property
    def description(self) -> str:
        return (
            "将重要信息记录为会话笔记以供将来参考。"
            "使用此工具记录关键事实、用户偏好、决策或在 Agent 执行链中稍后需要回忆的上下文。每条笔记都带有时间戳。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记录为笔记的信息。请简洁但具体。",
                },
                "category": {
                    "type": "string",
                    "description": "此笔记的可选类别/标签（例如 'user_preference'、'project_info'、'decision'）",
                },
            },
            "required": ["content"],
        }

    def _load_from_file(self) -> list:
        """从文件加载笔记。

        如果文件不存在则返回空列表（延迟加载）。
        """
        if not self.memory_file.exists():
            return []

        try:
            return json.loads(self.memory_file.read_text())
        except Exception:
            return []

    def _save_to_file(self, notes: list):
        """保存笔记到文件。

        如果父目录和文件不存在则创建（延迟初始化）。
        """
        # 实际保存时确保父目录存在
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.memory_file.write_text(json.dumps(notes, indent=2, ensure_ascii=False))

    async def execute(self, content: str, category: str = "general") -> ToolResult:
        """记录会话笔记。

        Args:
            content: 要记录的信息
            category: 此笔记的类别/标签

        Returns:
            包含成功状态的 ToolResult
        """
        try:
            # 加载现有笔记
            notes = self._load_from_file()

            # 添加带时间戳的新笔记
            note = {
                "timestamp": datetime.now().isoformat(),
                "category": category,
                "content": content,
            }
            notes.append(note)

            # 保存回文件
            self._save_to_file(notes)

            return ToolResult(
                success=True,
                content=f"已记录笔记: {content} (类别: {category})",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"记录笔记失败: {str(e)}",
            )


class RecallNoteTool(Tool):
    """用于回忆已记录的会话笔记的工具。"""

    def __init__(self, memory_file: str = "./workspace/.agent_memory.json"):
        """初始化回忆笔记工具。

        Args:
            memory_file: 笔记存储文件的路径
        """
        self.memory_file = Path(memory_file)

    @property
    def name(self) -> str:
        return "recall_notes"

    @property
    def description(self) -> str:
        return (
            "回忆所有之前记录的会话笔记。"
            "使用此工具检索会话早期或之前 Agent 执行链中的重要信息、上下文或决策。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "可选：按类别过滤笔记",
                },
            },
        }

    async def execute(self, category: str = None) -> ToolResult:
        """回忆会话笔记。

        Args:
            category: 可选的类别过滤器

        Returns:
            包含笔记内容的 ToolResult
        """
        try:
            if not self.memory_file.exists():
                return ToolResult(
                    success=True,
                    content="尚未记录任何笔记。",
                )

            notes = json.loads(self.memory_file.read_text())

            if not notes:
                return ToolResult(
                    success=True,
                    content="尚未记录任何笔记。",
                )

            # 如果指定了类别则过滤
            if category:
                notes = [n for n in notes if n.get("category") == category]
                if not notes:
                    return ToolResult(
                        success=True,
                        content=f"未找到类别为 {category} 的笔记。",
                    )

            # 格式化笔记以供显示
            formatted = []
            for idx, note in enumerate(notes, 1):
                timestamp = note.get("timestamp", "未知时间")
                cat = note.get("category", "general")
                content = note.get("content", "")
                formatted.append(f"{idx}. [{cat}] {content}\n   (记录于 {timestamp})")

            result = "已记录的笔记:\n" + "\n".join(formatted)

            return ToolResult(success=True, content=result)

        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"回忆笔记失败: {str(e)}",
            )
