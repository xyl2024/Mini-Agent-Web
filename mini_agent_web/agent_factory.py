"""Agent 工厂 - 复用 CLI 逻辑创建 Agent 实例。"""

from pathlib import Path
from typing import Optional

from mini_agent import LLMClient
from mini_agent.config import Config
from mini_agent.retry import RetryConfig
from mini_agent.schema import LLMProvider
from mini_agent.tools.base import Tool

from mini_agent_web.agent import Agent, AgentCallbacks
from mini_agent_web.connection_manager import ConnectionManager


async def create_agent(
    workspace_dir: Path,
    config_path: Optional[Path] = None,
    session_id: Optional[str] = None,
    callbacks: Optional[AgentCallbacks] = None,
) -> Agent:
    """创建 Agent 实例。

    Args:
        workspace_dir: 工作空间目录
        config_path: 配置文件路径（可选）
        session_id: 会话 ID（可选）
        callbacks: 回调处理器（可选）

    Returns:
        Agent 实例
    """
    # 1. 加载配置
    if config_path is None:
        config_path = Config.get_default_config_path()

    config = Config.from_yaml(config_path)

    # 2. 初始化 LLM 客户端
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

    # 3. 初始化基础工具
    tools: list[Tool] = []

    # Bash 辅助工具
    if config.tools.enable_bash:
        from mini_agent.tools.bash_tool import BashKillTool, BashOutputTool

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

    # 4. 添加工作空间工具
    if config.tools.enable_bash:
        from mini_agent.tools.bash_tool import BashTool

        tools.append(BashTool(workspace_dir=str(workspace_dir)))

    if config.tools.enable_file_tools:
        from mini_agent.tools.file_tools import EditTool, ReadTool, WriteTool

        tools.extend(
            [
                ReadTool(workspace_dir=str(workspace_dir)),
                WriteTool(workspace_dir=str(workspace_dir)),
                EditTool(workspace_dir=str(workspace_dir)),
            ]
        )

    if config.tools.enable_note:
        from mini_agent.tools.note_tool import SessionNoteTool

        tools.append(
            SessionNoteTool(memory_file=str(workspace_dir / ".agent_memory.json"))
        )

    # 5. 加载系统提示词
    system_prompt_path = Config.find_config_file(config.agent.system_prompt_path)
    if system_prompt_path and system_prompt_path.exists():
        system_prompt = system_prompt_path.read_text(encoding="utf-8")
    else:
        system_prompt = "You are Mini-Agent, an intelligent assistant powered by MiniMax M2.5 that can help users complete various tasks."

    # 6. 创建 Agent 实例
    agent = Agent(
        llm_client=llm_client,
        system_prompt=system_prompt,
        tools=tools,
        max_steps=config.agent.max_steps,
        workspace_dir=str(workspace_dir),
        callbacks=callbacks,
    )

    return agent
