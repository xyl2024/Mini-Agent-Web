"""配置管理模块

提供统一的配置加载和管理功能
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    """重试配置"""

    enabled: bool = True
    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0


class LLMConfig(BaseModel):
    """LLM 配置"""

    api_key: str
    api_base: str = "https://api.minimax.io"
    model: str = "MiniMax-M2.5"
    provider: str = "anthropic"  # "anthropic" 或 "openai"
    retry: RetryConfig = Field(default_factory=RetryConfig)


class AgentConfig(BaseModel):
    """Agent 配置"""

    max_steps: int = 50
    workspace_dir: str = "./workspace"
    system_prompt_path: str = "system_prompt.md"


class MCPConfig(BaseModel):
    """MCP（模型上下文协议）超时配置"""

    connect_timeout: float = 10.0  # 连接超时（秒）
    execute_timeout: float = 60.0  # 工具执行超时（秒）
    sse_read_timeout: float = 120.0  # SSE 读取超时（秒）


class ToolsConfig(BaseModel):
    """工具配置"""

    # 基础工具（文件操作、bash）
    enable_file_tools: bool = True
    enable_bash: bool = True
    enable_note: bool = True

    # 技能
    enable_skills: bool = True
    skills_dir: str = "./skills"

    # MCP 工具
    enable_mcp: bool = True
    mcp_config_path: str = "mcp.json"
    mcp: MCPConfig = Field(default_factory=MCPConfig)


class Config(BaseModel):
    """主配置类"""

    llm: LLMConfig
    agent: AgentConfig
    tools: ToolsConfig

    @classmethod
    def load(cls) -> "Config":
        """从默认搜索路径加载配置。"""
        config_path = cls.get_default_config_path()
        if not config_path.exists():
            raise FileNotFoundError("配置文件未找到。请运行 scripts/setup-config.sh 或将 config.yaml 放置在 mini_agent/config/ 目录下。")
        return cls.from_yaml(config_path)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Config":
        """从 YAML 文件加载配置

        Args:
            config_path: 配置文件路径

        Returns:
            Config 实例

        Raises:
            FileNotFoundError: 配置文件不存在
            ValueError: 配置格式无效或缺少必填字段
        """
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("配置文件为空")

        # 解析 LLM 配置
        if "api_key" not in data:
            raise ValueError("配置文件缺少必填字段: api_key")

        if not data["api_key"] or data["api_key"] == "YOUR_API_KEY_HERE":
            raise ValueError("请配置有效的 API Key")

        # 解析重试配置
        retry_data = data.get("retry", {})
        retry_config = RetryConfig(
            enabled=retry_data.get("enabled", True),
            max_retries=retry_data.get("max_retries", 3),
            initial_delay=retry_data.get("initial_delay", 1.0),
            max_delay=retry_data.get("max_delay", 60.0),
            exponential_base=retry_data.get("exponential_base", 2.0),
        )

        llm_config = LLMConfig(
            api_key=data["api_key"],
            api_base=data.get("api_base", "https://api.minimax.io"),
            model=data.get("model", "MiniMax-M2.5"),
            provider=data.get("provider", "anthropic"),
            retry=retry_config,
        )

        # 解析 Agent 配置
        agent_config = AgentConfig(
            max_steps=data.get("max_steps", 50),
            workspace_dir=data.get("workspace_dir", "./workspace"),
            system_prompt_path=data.get("system_prompt_path", "system_prompt.md"),
        )

        # 解析工具配置
        tools_data = data.get("tools", {})

        # 解析 MCP 配置
        mcp_data = tools_data.get("mcp", {})
        mcp_config = MCPConfig(
            connect_timeout=mcp_data.get("connect_timeout", 10.0),
            execute_timeout=mcp_data.get("execute_timeout", 60.0),
            sse_read_timeout=mcp_data.get("sse_read_timeout", 120.0),
        )

        tools_config = ToolsConfig(
            enable_file_tools=tools_data.get("enable_file_tools", True),
            enable_bash=tools_data.get("enable_bash", True),
            enable_note=tools_data.get("enable_note", True),
            enable_skills=tools_data.get("enable_skills", True),
            skills_dir=tools_data.get("skills_dir", "./skills"),
            enable_mcp=tools_data.get("enable_mcp", True),
            mcp_config_path=tools_data.get("mcp_config_path", "mcp.json"),
            mcp=mcp_config,
        )

        return cls(
            llm=llm_config,
            agent=agent_config,
            tools=tools_config,
        )

    @staticmethod
    def get_package_dir() -> Path:
        """获取包安装目录

        Returns:
            mini_agent 包目录路径
        """
        # 获取 config.py 文件所在的目录
        return Path(__file__).parent

    @classmethod
    def find_config_file(cls, filename: str) -> Path | None:
        """按优先级顺序查找配置文件

        按以下优先级顺序搜索配置文件：
        1) 当前目录的 mini_agent/config/{filename}（开发模式）
        2) 用户主目录的 ~/.mini-agent/config/{filename}
        3) 包安装目录的 {package}/mini_agent/config/{filename}

        Args:
            filename: 配置文件名（例如 "config.yaml", "mcp.json", "system_prompt.md"）

        Returns:
            找到的配置文件的路径，如果未找到则返回 None
        """
        # 优先级 1：开发模式 - 当前目录的 config/ 子目录
        dev_config = Path.cwd() / "mini_agent" / "config" / filename
        if dev_config.exists():
            return dev_config

        # 优先级 2：用户配置目录
        user_config = Path.home() / ".mini-agent" / "config" / filename
        if user_config.exists():
            return user_config

        # 优先级 3：包安装目录的 config/ 子目录
        package_config = cls.get_package_dir() / "config" / filename
        if package_config.exists():
            return package_config

        return None

    @classmethod
    def get_default_config_path(cls) -> Path:
        """获取默认配置文件路径，按优先级搜索

        Returns:
            config.yaml 的路径（优先级：开发配置 > 用户配置 > 包配置）
        """
        config_path = cls.find_config_file("config.yaml")
        if config_path:
            return config_path

        # 回退到包配置目录，用于错误消息
        return cls.get_package_dir() / "config" / "config.yaml"
