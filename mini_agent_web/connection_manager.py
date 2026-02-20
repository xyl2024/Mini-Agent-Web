"""WebSocket 连接管理器。"""

import asyncio
import json
from collections import defaultdict
from typing import Any, Optional

from fastapi import WebSocket


class ConnectionManager:
    """管理 WebSocket 连接。"""

    def __init__(self):
        """初始化连接管理器。"""
        # session_id -> set of WebSocket connections
        self.active_connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, session_id: str):
        """接受 WebSocket 连接并添加到会话。

        Args:
            websocket: WebSocket 连接
            session_id: 会话 ID
        """
        await websocket.accept()
        self.active_connections[session_id].add(websocket)

    def disconnect(self, websocket: WebSocket, session_id: str):
        """移除 WebSocket 连接。

        Args:
            websocket: WebSocket 连接
            session_id: 会话 ID
        """
        if session_id in self.active_connections:
            self.active_connections[session_id].discard(websocket)
            # 如果没有更多连接，清理该会话
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    async def send_json(self, websocket: WebSocket, data: dict[str, Any]):
        """发送 JSON 数据到 WebSocket。

        Args:
            websocket: WebSocket 连接
            data: 要发送的数据
        """
        try:
            await websocket.send_json(data)
        except Exception:
            # 连接可能已关闭，忽略错误
            pass

    async def broadcast(self, session_id: str, message: dict[str, Any]):
        """向指定会话的所有连接广播消息。

        Args:
            session_id: 会话 ID
            message: 要广播的消息
        """
        if session_id not in self.active_connections:
            return

        # 复制连接集合以避免在迭代时修改
        connections = self.active_connections[session_id].copy()
        dead_connections = set()

        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception:
                # 连接已关闭，标记为死亡
                dead_connections.add(connection)

        # 清理死亡连接
        for dead in dead_connections:
            self.disconnect(dead, session_id)

    async def send_thinking(self, session_id: str, thinking: str):
        """发送思考内容。

        Args:
            session_id: 会话 ID
            thinking: 思考内容
        """
        await self.broadcast(session_id, {"type": "thinking", "content": thinking})

    async def send_message(self, session_id: str, role: str, content: str):
        """发送助手消息。

        Args:
            session_id: 会话 ID
            role: 消息角色
            content: 消息内容
        """
        await self.broadcast(
            session_id, {"type": "message", "role": role, "content": content}
        )

    async def send_tool_call(
        self, session_id: str, tool: str, arguments: dict, call_id: str
    ):
        """发送工具调用。

        Args:
            session_id: 会话 ID
            tool: 工具名称
            arguments: 工具参数
            call_id: 工具调用 ID
        """
        await self.broadcast(
            session_id,
            {
                "type": "tool_call",
                "tool": tool,
                "arguments": arguments,
                "id": call_id,
            },
        )

    async def send_tool_result(
        self, session_id: str, tool: str, success: bool, content: str
    ):
        """发送工具结果。

        Args:
            session_id: 会话 ID
            tool: 工具名称
            success: 是否成功
            content: 结果内容
        """
        await self.broadcast(
            session_id,
            {"type": "tool_result", "tool": tool, "success": success, "content": content},
        )

    async def send_done(self, session_id: str, content: str):
        """发送完成消息。

        Args:
            session_id: 会话 ID
            content: 最终响应内容
        """
        await self.broadcast(session_id, {"type": "done", "content": content})

    async def send_error(self, session_id: str, message: str):
        """发送错误消息。

        Args:
            session_id: 会话 ID
            message: 错误信息
        """
        await self.broadcast(session_id, {"type": "error", "message": message})


# 全局连接管理器实例
manager = ConnectionManager()
