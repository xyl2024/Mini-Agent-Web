"""Agent 管理器 - 管理 Agent 实例生命周期。"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from mini_agent.config import Config
from mini_agent.tools.base import Tool

from mini_agent_web.agent import Agent, AgentCallbacks
from mini_agent_web.connection_manager import ConnectionManager


# 日志工具
def log(level: str, msg: str):
    """打印日志。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [AGENT-MGR] [{level}] {msg}")


def log_info(msg: str):
    log("INFO", msg)


def log_error(msg: str):
    log("ERROR", msg)


class AgentManager:
    """Agent 管理器 - 单例模式，每个 session_id 对应一个 Agent 实例。"""

    _instance: Optional["AgentManager"] = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # session_id -> Agent 实例
        self._agents: dict[str, Agent] = {}
        # session_id -> 创建时间（用于超时清理）
        self._agent_timestamps: dict[str, float] = {}
        # session_id -> 锁（防止同一 session 并发执行）
        self._agent_locks: dict[str, asyncio.Lock] = {}

        # Agent 空闲超时时间（秒）
        self.timeout = 3600  # 1 小时

        # 加载配置
        self._config = None
        self._config_path = None

    def _load_config(self):
        """加载配置。"""
        if self._config is None:
            self._config_path = Config.get_default_config_path()
            self._config = Config.from_yaml(self._config_path)

    @property
    def config(self):
        """获取配置。"""
        self._load_config()
        return self._config

    @property
    def config_path(self):
        """获取配置路径。"""
        self._load_config()
        return self._config_path

    async def get_agent(
        self,
        session_id: str,
        workspace_dir: Optional[Path] = None,
        callbacks: Optional[AgentCallbacks] = None,
    ) -> Agent:
        """获取或创建 Agent 实例。

        Args:
            session_id: 会话 ID
            workspace_dir: 工作空间目录
            callbacks: 回调处理器

        Returns:
            Agent 实例
        """
        async with self._lock:
            # 如果已存在，直接返回
            if session_id in self._agents:
                # 更新回调（可能连接改变了）
                self._agents[session_id].callbacks = callbacks
                self._agent_timestamps[session_id] = asyncio.get_event_loop().time()
                log_info(f"复用已有 Agent 实例: session_id={session_id}, tools_count={len(self._agents[session_id].tools)}")
                return self._agents[session_id]

            # 创建新的 Agent
            if workspace_dir is None:
                workspace_dir = Path.cwd() / "workspace"
                workspace_dir.mkdir(parents=True, exist_ok=True)

            log_info(f"创建新 Agent 实例: session_id={session_id}, workspace={workspace_dir}")
            agent = await self._create_agent(workspace_dir, callbacks)

            self._agents[session_id] = agent
            self._agent_timestamps[session_id] = asyncio.get_event_loop().time()
            self._agent_locks[session_id] = asyncio.Lock()

            log_info(f"Agent 实例已创建: session_id={session_id}, tools_count={len(agent.tools)}, max_steps={agent.max_steps}")
            return agent

    async def _create_agent(
        self,
        workspace_dir: Path,
        callbacks: Optional[AgentCallbacks] = None,
    ) -> Agent:
        """创建 Agent 实例。

        Args:
            workspace_dir: 工作空间目录
            callbacks: 回调处理器

        Returns:
            Agent 实例
        """
        from mini_agent import LLMClient
        from mini_agent.retry import RetryConfig
        from mini_agent.schema import LLMProvider
        from mini_agent.tools.bash_tool import BashKillTool, BashOutputTool, BashTool
        from mini_agent.tools.file_tools import EditTool, ReadTool, WriteTool
        from mini_agent.tools.note_tool import SessionNoteTool

        config = self.config

        # 初始化 LLM 客户端
        retry_config = RetryConfig(
            enabled=config.llm.retry.enabled,
            max_retries=config.llm.retry.max_retries,
            initial_delay=config.llm.retry.initial_delay,
            max_delay=config.llm.retry.max_delay,
            exponential_base=config.llm.retry.exponential_base,
            retryable_exceptions=(Exception,),
        )

        provider = (
            LLMProvider.ANTHROPIC
            if config.llm.provider.lower() == "anthropic"
            else LLMProvider.OPENAI
        )

        llm_client = LLMClient(
            api_key=config.llm.api_key,
            provider=provider,
            api_base=config.llm.api_base,
            model=config.llm.model,
            retry_config=retry_config if config.llm.retry.enabled else None,
        )

        # 初始化工具
        tools: list[Tool] = []

        # Bash 辅助工具
        if config.tools.enable_bash:
            tools.append(BashOutputTool())
            tools.append(BashKillTool())

        # Claude 技能
        if config.tools.enable_skills:
            from mini_agent.tools.skill_tool import create_skill_tools

            try:
                skills_path = Path(config.tools.skills_dir).expanduser()
                if not skills_path.is_absolute():
                    search_paths = [
                        skills_path,
                        Path("mini_agent") / skills_path,
                        Config.get_package_dir() / skills_path,
                    ]
                    skills_dir = str(skills_path)
                    for path in search_paths:
                        if path.exists():
                            skills_dir = str(path.resolve())
                            break
                else:
                    skills_dir = str(skills_path)

                skill_tools, _ = create_skill_tools(skills_dir)
                if skill_tools:
                    tools.extend(skill_tools)
            except Exception:
                pass

        # MCP 工具
        if config.tools.enable_mcp:
            from mini_agent.tools.mcp_loader import load_mcp_tools_async

            try:
                mcp_config_path = Config.find_config_file(config.tools.mcp_config_path)
                if mcp_config_path:
                    mcp_tools = await load_mcp_tools_async(str(mcp_config_path))
                    if mcp_tools:
                        tools.extend(mcp_tools)
            except Exception:
                pass

        # 工作空间工具
        if config.tools.enable_bash:
            tools.append(BashTool(workspace_dir=str(workspace_dir)))

        if config.tools.enable_file_tools:
            tools.extend(
                [
                    ReadTool(workspace_dir=str(workspace_dir)),
                    WriteTool(workspace_dir=str(workspace_dir)),
                    EditTool(workspace_dir=str(workspace_dir)),
                ]
            )

        if config.tools.enable_note:
            tools.append(
                SessionNoteTool(memory_file=str(workspace_dir / ".agent_memory.json"))
            )

        # 加载系统提示词
        system_prompt_path = Config.find_config_file(config.agent.system_prompt_path)
        if system_prompt_path and system_prompt_path.exists():
            system_prompt = system_prompt_path.read_text(encoding="utf-8")
        else:
            system_prompt = "You are Mini-Agent, an intelligent assistant powered by MiniMax M2.5 that can help users complete various tasks."

        # 创建 Agent 实例
        agent = Agent(
            llm_client=llm_client,
            system_prompt=system_prompt,
            tools=tools,
            max_steps=config.agent.max_steps,
            workspace_dir=str(workspace_dir),
            callbacks=callbacks,
        )

        return agent

    def get_lock(self, session_id: str) -> asyncio.Lock:
        """获取 session 的锁。

        Args:
            session_id: 会话 ID

        Returns:
            asyncio.Lock
        """
        return self._agent_locks.get(session_id, asyncio.Lock())

    async def cleanup_old_agents(self):
        """清理超时的 Agent 实例。"""
        async with self._lock:
            import time

            current_time = time.time()
            to_remove = []

            for session_id, timestamp in self._agent_timestamps.items():
                if current_time - timestamp > self.timeout:
                    to_remove.append(session_id)

            for session_id in to_remove:
                del self._agents[session_id]
                del self._agent_timestamps[session_id]
                if session_id in self._agent_locks:
                    del self._agent_locks[session_id]

    def remove_agent(self, session_id: str):
        """移除 Agent 实例。

        Args:
            session_id: 会话 ID
        """
        if session_id in self._agents:
            del self._agents[session_id]
        if session_id in self._agent_timestamps:
            del self._agent_timestamps[session_id]
        if session_id in self._agent_locks:
            del self._agent_locks[session_id]

    def get_active_sessions(self) -> list[str]:
        """获取活跃的会话 ID 列表。"""
        return list(self._agents.keys())


# 全局单例
agent_manager = AgentManager()
