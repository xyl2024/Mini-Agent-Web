"""MCP 工具加载器，集成真实 MCP 客户端并支持超时处理。"""

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .base import Tool, ToolResult

# Connection type aliases
ConnectionType = Literal["stdio", "sse", "http", "streamable_http"]


@dataclass
class MCPTimeoutConfig:
    """MCP 超时配置。"""

    connect_timeout: float = 10.0  # 连接超时（秒）
    execute_timeout: float = 60.0  # 工具执行超时（秒）
    sse_read_timeout: float = 120.0  # SSE 读取超时（秒）


# 全局默认超时配置
_default_timeout_config = MCPTimeoutConfig()


def set_mcp_timeout_config(
    connect_timeout: float | None = None,
    execute_timeout: float | None = None,
    sse_read_timeout: float | None = None,
) -> None:
    """设置全局 MCP 超时配置。

    Args:
        connect_timeout: 连接超时（秒）
        execute_timeout: 工具执行超时（秒）
        sse_read_timeout: SSE 读取超时（秒）
    """
    global _default_timeout_config
    if connect_timeout is not None:
        _default_timeout_config.connect_timeout = connect_timeout
    if execute_timeout is not None:
        _default_timeout_config.execute_timeout = execute_timeout
    if sse_read_timeout is not None:
        _default_timeout_config.sse_read_timeout = sse_read_timeout


def get_mcp_timeout_config() -> MCPTimeoutConfig:
    """获取当前 MCP 超时配置。"""
    return _default_timeout_config


class MCPTool(Tool):
    """带有超时处理的 MCP 工具包装器。"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        session: ClientSession,
        execute_timeout: float | None = None,
    ):
        self._name = name
        self._description = description
        self._parameters = parameters
        self._session = session
        self._execute_timeout = execute_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs) -> ToolResult:
        """通过会话执行 MCP 工具，带超时保护。"""
        timeout = self._execute_timeout or _default_timeout_config.execute_timeout

        try:
            # 用超时包装 call_tool
            async with asyncio.timeout(timeout):
                result = await self._session.call_tool(self._name, arguments=kwargs)

            # MCP 工具结果是一个内容项列表
            content_parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    content_parts.append(item.text)
                else:
                    content_parts.append(str(item))

            content_str = "\n".join(content_parts)

            is_error = result.isError if hasattr(result, "isError") else False

            return ToolResult(success=not is_error, content=content_str, error=None if not is_error else "工具返回错误")

        except TimeoutError:
            return ToolResult(
                success=False,
                content="",
                error=f"MCP 工具执行在 {timeout}s 后超时。远程服务器可能较慢或无响应。",
            )
        except Exception as e:
            return ToolResult(success=False, content="", error=f"MCP 工具执行失败: {str(e)}")


class MCPServerConnection:
    """管理到单个 MCP 服务器（STDIO 或基于 URL）的连接，带超时处理。"""

    def __init__(
        self,
        name: str,
        connection_type: ConnectionType = "stdio",
        # STDIO 参数
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        # 基于 URL 的参数
        url: str | None = None,
        headers: dict[str, str] | None = None,
        # 超时覆盖（每个服务器）
        connect_timeout: float | None = None,
        execute_timeout: float | None = None,
        sse_read_timeout: float | None = None,
    ):
        self.name = name
        self.connection_type = connection_type
        # STDIO
        self.command = command
        self.args = args or []
        self.env = env or {}
        # 基于 URL
        self.url = url
        self.headers = headers or {}
        # 超时设置（每个服务器覆盖）
        self.connect_timeout = connect_timeout
        self.execute_timeout = execute_timeout
        self.sse_read_timeout = sse_read_timeout
        # 连接状态
        self.session: ClientSession | None = None
        self.exit_stack: AsyncExitStack | None = None
        self.tools: list[MCPTool] = []

    def _get_connect_timeout(self) -> float:
        """获取有效的连接超时。"""
        return self.connect_timeout or _default_timeout_config.connect_timeout

    def _get_sse_read_timeout(self) -> float:
        """获取有效的 SSE 读取超时。"""
        return self.sse_read_timeout or _default_timeout_config.sse_read_timeout

    def _get_execute_timeout(self) -> float:
        """获取有效的执行超时。"""
        return self.execute_timeout or _default_timeout_config.execute_timeout

    async def connect(self) -> bool:
        """连接到 MCP 服务器，带超时保护。"""
        connect_timeout = self._get_connect_timeout()

        try:
            self.exit_stack = AsyncExitStack()

            # 用超时包装连接
            async with asyncio.timeout(connect_timeout):
                if self.connection_type == "stdio":
                    read_stream, write_stream = await self._connect_stdio()
                elif self.connection_type == "sse":
                    read_stream, write_stream = await self._connect_sse()
                else:  # http / streamable_http
                    read_stream, write_stream = await self._connect_streamable_http()

                # 进入客户端会话上下文
                session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
                self.session = session

                # 初始化会话
                await session.initialize()

                # 列出可用工具
                tools_list = await session.list_tools()

            # 为每个工具包装执行超时
            execute_timeout = self._get_execute_timeout()
            for tool in tools_list.tools:
                parameters = tool.inputSchema if hasattr(tool, "inputSchema") else {}
                mcp_tool = MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    parameters=parameters,
                    session=session,
                    execute_timeout=execute_timeout,
                )
                self.tools.append(mcp_tool)

            conn_info = self.url if self.url else self.command
            print(f"✓ 已连接到 MCP 服务器 '{self.name}' ({self.connection_type}: {conn_info}) - 已加载 {len(self.tools)} 个工具")
            for tool in self.tools:
                desc = tool.description[:60] if len(tool.description) > 60 else tool.description
                print(f"  - {tool.name}: {desc}...")
            return True

        except TimeoutError:
            print(f"✗ 连接到 MCP 服务器 '{self.name}' 在 {connect_timeout}s 后超时")
            if self.exit_stack:
                await self.exit_stack.aclose()
                self.exit_stack = None
            return False

        except Exception as e:
            print(f"✗ 连接到 MCP 服务器 '{self.name}' 失败: {e}")
            if self.exit_stack:
                await self.exit_stack.aclose()
                self.exit_stack = None
            import traceback

            traceback.print_exc()
            return False

    async def _connect_stdio(self):
        """通过 STDIO 传输连接。"""
        server_params = StdioServerParameters(command=self.command, args=self.args, env=self.env if self.env else None)
        return await self.exit_stack.enter_async_context(stdio_client(server_params))

    async def _connect_sse(self):
        """通过 SSE 传输连接，带超时参数。"""
        connect_timeout = self._get_connect_timeout()
        sse_read_timeout = self._get_sse_read_timeout()

        return await self.exit_stack.enter_async_context(
            sse_client(
                url=self.url,
                headers=self.headers if self.headers else None,
                timeout=connect_timeout,
                sse_read_timeout=sse_read_timeout,
            )
        )

    async def _connect_streamable_http(self):
        """通过 Streamable HTTP 传输连接，带超时参数。"""
        connect_timeout = self._get_connect_timeout()
        sse_read_timeout = self._get_sse_read_timeout()

        # streamablehttp_client 返回 (read, write, get_session_id)
        read_stream, write_stream, _ = await self.exit_stack.enter_async_context(
            streamablehttp_client(
                url=self.url,
                headers=self.headers if self.headers else None,
                timeout=connect_timeout,
                sse_read_timeout=sse_read_timeout,
            )
        )
        return read_stream, write_stream

    async def disconnect(self):
        """正确断开与 MCP 服务器的连接。"""
        if self.exit_stack:
            try:
                await self.exit_stack.aclose()
            except Exception:
                # anyio 取消作用域可能会在关闭期间从不同的任务上下文
                # 关闭 stdio_client 的任务组时引发 RuntimeError 或 ExceptionGroup
                pass
            finally:
                self.exit_stack = None
                self.session = None


# 全局连接注册表
_mcp_connections: list[MCPServerConnection] = []


def _determine_connection_type(server_config: dict) -> ConnectionType:
    """从服务器配置确定连接类型。"""
    explicit_type = server_config.get("type", "").lower()
    if explicit_type in ("stdio", "sse", "http", "streamable_http"):
        return explicit_type
    # 自动检测：如果存在 url，则默认使用 streamable_http；否则使用 stdio
    if server_config.get("url"):
        return "streamable_http"
    return "stdio"


def _resolve_mcp_config_path(config_path: str) -> Path | None:
    """
    解析 MCP 配置路径，带回退逻辑。

    优先级：
    1. 如果指定路径存在，直接使用
    2. 如果 mcp.json 不存在，尝试同一目录中的 mcp-example.json
    3. 如果未找到配置则返回 None

    Args:
        config_path: 用户指定的配置路径

    Returns:
        解析后的 Path 对象，如果未找到则返回 None
    """
    config_file = Path(config_path)

    # 如果指定路径存在，直接使用
    if config_file.exists():
        return config_file

    # 回退：如果查找 mcp.json，尝试 mcp-example.json
    if config_file.name == "mcp.json":
        example_file = config_file.parent / "mcp-example.json"
        if example_file.exists():
            print(f"mcp.json 未找到，使用模板: {example_file}")
            return example_file

    return None


async def load_mcp_tools_async(config_path: str = "mcp.json") -> list[Tool]:
    """
    从配置文件加载 MCP 工具。

    此函数：
    1. 读取 MCP 配置文件（回退到 mcp-example.json）
    2. 连接到每个服务器（STDIO 或基于 URL）
    3. 获取工具定义
    4. 将它们包装为 Tool 对象

    支持的配置格式：
    - STDIO: {"command": "...", "args": [...], "env": {...}}
    - 基于 URL: {"url": "https://...", "type": "sse|http|streamable_http", "headers": {...}}

    每个服务器的超时覆盖（可选）：
    - "connect_timeout": float - 连接超时（秒）
    - "execute_timeout": float - 工具执行超时（秒）
    - "sse_read_timeout": float - SSE 读取超时（秒）

    注意：
    - 如果未找到 mcp.json，将自动回退到 mcp-example.json
    - 用户特定的 mcp.json 应通过复制 mcp-example.json 创建

    Args:
        config_path: MCP 配置文件路径（默认："mcp.json"）

    Returns:
        表示 MCP 工具的 Tool 对象列表
    """
    global _mcp_connections

    config_file = _resolve_mcp_config_path(config_path)

    if config_file is None:
        print(f"未找到 MCP 配置: {config_path}")
        return []

    try:
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)

        mcp_servers = config.get("mcpServers", {})

        if not mcp_servers:
            print("未配置 MCP 服务器")
            return []

        all_tools = []

        # 连接到每个已启用的服务器
        for server_name, server_config in mcp_servers.items():
            if server_config.get("disabled", False):
                print(f"跳过禁用的服务器: {server_name}")
                continue

            conn_type = _determine_connection_type(server_config)
            url = server_config.get("url")
            command = server_config.get("command")

            # 验证配置
            if conn_type == "stdio" and not command:
                print(f"STDIO 服务器未指定命令: {server_name}")
                continue
            if conn_type in ("sse", "http", "streamable_http") and not url:
                print(f"{conn_type.upper()} 服务器未指定 url: {server_name}")
                continue

            connection = MCPServerConnection(
                name=server_name,
                connection_type=conn_type,
                command=command,
                args=server_config.get("args", []),
                env=server_config.get("env", {}),
                url=url,
                headers=server_config.get("headers", {}),
                # 来自 mcp.json 的每个服务器超时覆盖
                connect_timeout=server_config.get("connect_timeout"),
                execute_timeout=server_config.get("execute_timeout"),
                sse_read_timeout=server_config.get("sse_read_timeout"),
            )
            success = await connection.connect()

            if success:
                _mcp_connections.append(connection)
                all_tools.extend(connection.tools)

        print(f"\n已加载的 MCP 工具总数: {len(all_tools)}")

        return all_tools

    except Exception as e:
        print(f"加载 MCP 配置时出错: {e}")
        import traceback

        traceback.print_exc()
        return []


async def cleanup_mcp_connections():
    """清理所有 MCP 连接。"""
    global _mcp_connections
    for connection in _mcp_connections:
        await connection.disconnect()
    _mcp_connections.clear()
