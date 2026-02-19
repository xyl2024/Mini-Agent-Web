"""
Mini Agent - 交互式运行时示例

用法:
    mini-agent [--workspace DIR] [--task TASK]

示例:
    mini-agent                              # 使用当前目录作为工作空间（交互模式）
    mini-agent --workspace /path/to/dir     # 使用指定的工作空间目录（交互模式）
    mini-agent --task "create a file"       # 非交互式执行任务
"""

import argparse
import asyncio
import platform
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import List

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from mini_agent import LLMClient
from mini_agent.agent import Agent
from mini_agent.config import Config
from mini_agent.schema import LLMProvider
from mini_agent.tools.base import Tool
from mini_agent.tools.bash_tool import BashKillTool, BashOutputTool, BashTool
from mini_agent.tools.file_tools import EditTool, ReadTool, WriteTool
from mini_agent.tools.mcp_loader import cleanup_mcp_connections, load_mcp_tools_async, set_mcp_timeout_config
from mini_agent.tools.note_tool import SessionNoteTool
from mini_agent.tools.skill_tool import create_skill_tools
from mini_agent.utils import calculate_display_width


# ANSI 颜色码
class Colors:
    """终端颜色定义"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # 前景色
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # 亮色
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # 背景色
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


def get_log_directory() -> Path:
    """获取日志目录路径。"""
    return Path.home() / ".mini-agent" / "log"


def show_log_directory(open_file_manager: bool = True) -> None:
    """显示日志目录内容并可选打开文件管理器。

    Args:
        open_file_manager: 是否打开系统文件管理器
    """
    log_dir = get_log_directory()

    print(f"\n{Colors.BRIGHT_CYAN}📁 日志目录: {log_dir}{Colors.RESET}")

    if not log_dir.exists() or not log_dir.is_dir():
        print(f"{Colors.RED}日志目录不存在: {log_dir}{Colors.RESET}\n")
        return

    log_files = list(log_dir.glob("*.log"))

    if not log_files:
        print(f"{Colors.YELLOW}目录中未找到日志文件。{Colors.RESET}\n")
        return

    # 按修改时间排序（最新的在前）
    log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BRIGHT_YELLOW}可用日志文件（最新的在前）:{Colors.RESET}")

    for i, log_file in enumerate(log_files[:10], 1):
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        size = log_file.stat().st_size
        size_str = f"{size:,}" if size < 1024 else f"{size / 1024:.1f}K"
        print(f"  {Colors.GREEN}{i:2d}.{Colors.RESET} {Colors.BRIGHT_WHITE}{log_file.name}{Colors.RESET}")
        print(f"      {Colors.DIM}修改时间: {mtime.strftime('%Y-%m-%d %H:%M:%S')}, 大小: {size_str}{Colors.RESET}")

    if len(log_files) > 10:
        print(f"  {Colors.DIM}... 还有 {len(log_files) - 10} 个文件{Colors.RESET}")

    print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}")

    # 打开文件管理器
    if open_file_manager:
        _open_directory_in_file_manager(log_dir)

    print()


def _open_directory_in_file_manager(directory: Path) -> None:
    """在系统文件管理器中打开目录（跨平台）。"""
    system = platform.system()

    try:
        if system == "Darwin":
            subprocess.run(["open", str(directory)], check=False)
        elif system == "Windows":
            subprocess.run(["explorer", str(directory)], check=False)
        elif system == "Linux":
            subprocess.run(["xdg-open", str(directory)], check=False)
    except FileNotFoundError:
        print(f"{Colors.YELLOW}无法打开文件管理器。请手动导航。{Colors.RESET}")
    except Exception as e:
        print(f"{Colors.YELLOW}打开文件管理器时出错: {e}{Colors.RESET}")


def read_log_file(filename: str) -> None:
    """读取并显示指定的日志文件。

    Args:
        filename: 要读取的日志文件名
    """
    log_dir = get_log_directory()
    log_file = log_dir / filename

    if not log_file.exists() or not log_file.is_file():
        print(f"\n{Colors.RED}❌ 日志文件未找到: {log_file}{Colors.RESET}\n")
        return

    print(f"\n{Colors.BRIGHT_CYAN}📄 正在读取: {log_file}{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 80}{Colors.RESET}")

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        print(content)
        print(f"{Colors.DIM}{'─' * 80}{Colors.RESET}")
        print(f"\n{Colors.GREEN}✅ 文件结束{Colors.RESET}\n")
    except Exception as e:
        print(f"\n{Colors.RED}❌ 读取文件时出错: {e}{Colors.RESET}\n")


def print_banner():
    """打印欢迎横幅并正确对齐"""
    BOX_WIDTH = 58
    banner_text = f"{Colors.BOLD}🤖 Mini Agent - 多轮交互式会话{Colors.RESET}"
    banner_width = calculate_display_width(banner_text)

    # 居中文本并添加适当的填充
    total_padding = BOX_WIDTH - banner_width
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding

    print()
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}╔{'═' * BOX_WIDTH}╗{Colors.RESET}")
    print(
        f"{Colors.BOLD}{Colors.BRIGHT_CYAN}║{Colors.RESET}{' ' * left_padding}{banner_text}{' ' * right_padding}{Colors.BOLD}{Colors.BRIGHT_CYAN}║{Colors.RESET}"
    )
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}╚{'═' * BOX_WIDTH}╝{Colors.RESET}")
    print()


def print_help():
    """打印帮助信息"""
    help_text = f"""
{Colors.BOLD}{Colors.BRIGHT_YELLOW}可用命令:{Colors.RESET}
  {Colors.BRIGHT_GREEN}/help{Colors.RESET}      - 显示此帮助信息
  {Colors.BRIGHT_GREEN}/clear{Colors.RESET}     - 清除会话历史（保留系统提示词）
  {Colors.BRIGHT_GREEN}/history{Colors.RESET}   - 显示当前会话消息数
  {Colors.BRIGHT_GREEN}/stats{Colors.RESET}     - 显示会话统计信息
  {Colors.BRIGHT_GREEN}/log{Colors.RESET}       - 显示日志目录和最近的文件
  {Colors.BRIGHT_GREEN}/log <file>{Colors.RESET} - 读取指定的日志文件
  {Colors.BRIGHT_GREEN}/exit{Colors.RESET}      - 退出程序（也可使用: exit, quit, q）

{Colors.BOLD}{Colors.BRIGHT_YELLOW}键盘快捷键:{Colors.RESET}
  {Colors.BRIGHT_CYAN}Esc{Colors.RESET}        - 取消当前 agent 执行
  {Colors.BRIGHT_CYAN}Ctrl+C{Colors.RESET}     - 退出程序
  {Colors.BRIGHT_CYAN}Ctrl+U{Colors.RESET}     - 清除当前输入行
  {Colors.BRIGHT_CYAN}Ctrl+L{Colors.RESET}     - 清除屏幕
  {Colors.BRIGHT_CYAN}Ctrl+J{Colors.RESET}     - 插入换行符（也可 Ctrl+Enter）
  {Colors.BRIGHT_CYAN}Tab{Colors.RESET}        - 命令自动补全
  {Colors.BRIGHT_CYAN}↑/↓{Colors.RESET}        - 浏览命令历史
  {Colors.BRIGHT_CYAN}→{Colors.RESET}          - 接受自动建议

{Colors.BOLD}{Colors.BRIGHT_YELLOW}用法:{Colors.RESET}
  - 直接输入您的任务，Agent 会帮助您完成
  - Agent 会记住本会话中的所有对话内容
  - 使用 {Colors.BRIGHT_GREEN}/clear{Colors.RESET} 开始新会话
  - 按 {Colors.BRIGHT_CYAN}Enter{Colors.RESET} 提交您的消息
  - 使用 {Colors.BRIGHT_CYAN}Ctrl+J{Colors.RESET} 在消息中插入换行
"""
    print(help_text)


def print_session_info(agent: Agent, workspace_dir: Path, model: str):
    """打印会话信息并正确对齐"""
    BOX_WIDTH = 58

    def print_info_line(text: str):
        """打印带有适当填充的单个信息行"""
        # 考虑前导空格
        text_width = calculate_display_width(text)
        padding = max(0, BOX_WIDTH - 1 - text_width)
        print(f"{Colors.DIM}│{Colors.RESET} {text}{' ' * padding}{Colors.DIM}│{Colors.RESET}")

    # 顶部边框
    print(f"{Colors.DIM}┌{'─' * BOX_WIDTH}┐{Colors.RESET}")

    # 标题（居中）
    header_text = f"{Colors.BRIGHT_CYAN}会话信息{Colors.RESET}"
    header_width = calculate_display_width(header_text)
    header_padding_total = BOX_WIDTH - 1 - header_width  # -1 表示前导空格
    header_padding_left = header_padding_total // 2
    header_padding_right = header_padding_total - header_padding_left
    print(f"{Colors.DIM}│{Colors.RESET} {' ' * header_padding_left}{header_text}{' ' * header_padding_right}{Colors.DIM}│{Colors.RESET}")

    # 分隔线
    print(f"{Colors.DIM}├{'─' * BOX_WIDTH}┤{Colors.RESET}")

    # 信息行
    print_info_line(f"模型: {model}")
    print_info_line(f"工作空间: {workspace_dir}")
    print_info_line(f"消息历史: {len(agent.messages)} 条消息")
    print_info_line(f"可用工具: {len(agent.tools)} 个工具")

    # 底部边框
    print(f"{Colors.DIM}└{'─' * BOX_WIDTH}┘{Colors.RESET}")
    print()
    print(f"{Colors.DIM}输入 {Colors.BRIGHT_GREEN}/help{Colors.DIM} 获取帮助，{Colors.BRIGHT_GREEN}/exit{Colors.DIM} 退出{Colors.RESET}")
    print()


def print_stats(agent: Agent, session_start: datetime):
    """打印会话统计信息"""
    duration = datetime.now() - session_start
    hours, remainder = divmod(int(duration.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    # 统计不同类型的消息
    user_msgs = sum(1 for m in agent.messages if m.role == "user")
    assistant_msgs = sum(1 for m in agent.messages if m.role == "assistant")
    tool_msgs = sum(1 for m in agent.messages if m.role == "tool")

    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}会话统计:{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 40}{Colors.RESET}")
    print(f"  会话时长: {hours:02d}:{minutes:02d}:{seconds:02d}")
    print(f"  总消息数: {len(agent.messages)}")
    print(f"    - 用户消息: {Colors.BRIGHT_GREEN}{user_msgs}{Colors.RESET}")
    print(f"    - 助手回复: {Colors.BRIGHT_BLUE}{assistant_msgs}{Colors.RESET}")
    print(f"    - 工具调用: {Colors.BRIGHT_YELLOW}{tool_msgs}{Colors.RESET}")
    print(f"  可用工具: {len(agent.tools)}")
    if agent.api_total_tokens > 0:
        print(f"  使用的 API Tokens: {Colors.BRIGHT_MAGENTA}{agent.api_total_tokens:,}{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 40}{Colors.RESET}\n")


def parse_args() -> argparse.Namespace:
    """解析命令行参数

    Returns:
        解析后的参数
    """
    parser = argparse.ArgumentParser(
        description="Mini Agent - 支持文件工具和 MCP 的 AI 助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  mini-agent                              # 使用当前目录作为工作空间
  mini-agent --workspace /path/to/dir     # 使用指定的工作空间目录
  mini-agent log                          # 显示日志目录和最近的文件
  mini-agent log agent_run_xxx.log        # 读取指定的日志文件
        """,
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=None,
        help="工作空间目录（默认：当前目录）",
    )
    parser.add_argument(
        "--task",
        "-t",
        type=str,
        default=None,
        help="非交互式执行任务并退出",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version="mini-agent 0.1.0",
    )

    # 子命令
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # log 子命令
    log_parser = subparsers.add_parser("log", help="显示日志目录或读取日志文件")
    log_parser.add_argument(
        "filename",
        nargs="?",
        default=None,
        help="要读取的日志文件名（可选，省略则显示目录）",
    )

    return parser.parse_args()


async def initialize_base_tools(config: Config):
    """初始化基础工具（不依赖工作空间）

    这些工具从包配置加载，不依赖工作空间。
    注意：文件工具现在依赖工作空间，在 add_workspace_tools() 中初始化

    Args:
        config: 配置对象

    Returns:
        元组（工具列表，如果启用技能则返回 skill loader）
    """

    tools = []
    skill_loader = None

    # 1. Bash 辅助工具（输出监控和终止）
    # 注意：BashTool 本身在 add_workspace_tools() 中创建，以 workspace_dir 作为工作目录
    if config.tools.enable_bash:
        bash_output_tool = BashOutputTool()
        tools.append(bash_output_tool)
        print(f"{Colors.GREEN}✅ 已加载 Bash Output 工具{Colors.RESET}")

        bash_kill_tool = BashKillTool()
        tools.append(bash_kill_tool)
        print(f"{Colors.GREEN}✅ 已加载 Bash Kill 工具{Colors.RESET}")

    # 3. Claude 技能（从包目录加载）
    if config.tools.enable_skills:
        print(f"{Colors.BRIGHT_CYAN}正在加载 Claude Skills...{Colors.RESET}")
        try:
            # 使用优先级搜索解析技能目录
            # 展开 ~ 为用户主目录以提高可移植性
            skills_path = Path(config.tools.skills_dir).expanduser()
            if skills_path.is_absolute():
                skills_dir = str(skills_path)
            else:
                # 按优先级顺序搜索：
                # 1. 当前目录（开发模式：./skills 或 ./mini_agent/skills）
                # 2. 包目录（安装后：site-packages/mini_agent/skills）
                search_paths = [
                    skills_path,  # ./skills 向后兼容
                    Path("mini_agent") / skills_path,  # ./mini_agent/skills
                    Config.get_package_dir() / skills_path,  # site-packages/mini_agent/skills
                ]

                # 找到第一个存在的路径
                skills_dir = str(skills_path)  # 默认
                for path in search_paths:
                    if path.exists():
                        skills_dir = str(path.resolve())
                        break

            skill_tools, skill_loader = create_skill_tools(skills_dir)
            if skill_tools:
                tools.extend(skill_tools)
                print(f"{Colors.GREEN}✅ 已加载 Skill 工具（get_skill）{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠️  未找到可用的 Skills{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠️  加载 Skills 失败: {e}{Colors.RESET}")

    # 4. MCP 工具（带优先级搜索加载）
    if config.tools.enable_mcp:
        print(f"{Colors.BRIGHT_CYAN}正在加载 MCP 工具...{Colors.RESET}")
        try:
            # 从 config.yaml 应用 MCP 超时配置
            mcp_config = config.tools.mcp
            set_mcp_timeout_config(
                connect_timeout=mcp_config.connect_timeout,
                execute_timeout=mcp_config.execute_timeout,
                sse_read_timeout=mcp_config.sse_read_timeout,
            )
            print(
                f"{Colors.DIM}  MCP 超时: connect={mcp_config.connect_timeout}s, "
                f"execute={mcp_config.execute_timeout}s, sse_read={mcp_config.sse_read_timeout}s{Colors.RESET}"
            )

            # 使用优先级搜索 mcp.json
            mcp_config_path = Config.find_config_file(config.tools.mcp_config_path)
            if mcp_config_path:
                mcp_tools = await load_mcp_tools_async(str(mcp_config_path))
                if mcp_tools:
                    tools.extend(mcp_tools)
                    print(f"{Colors.GREEN}✅ 已加载 {len(mcp_tools)} 个 MCP 工具（来自: {mcp_config_path}）{Colors.RESET}")
                else:
                    print(f"{Colors.YELLOW}⚠️  未找到可用的 MCP 工具{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}⚠️  未找到 MCP 配置文件: {config.tools.mcp_config_path}{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠️  加载 MCP 工具失败: {e}{Colors.RESET}")

    print()  # 空行分隔符
    return tools, skill_loader


def add_workspace_tools(tools: List[Tool], config: Config, workspace_dir: Path):
    """添加依赖工作空间的工具

    这些工具需要知道工作空间目录。

    Args:
        tools: 要添加的现有工具列表
        config: 配置对象
        workspace_dir: 工作空间目录路径
    """
    # 确保工作空间目录存在
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Bash 工具 - 需要 workspace 作为命令执行的工作目录
    if config.tools.enable_bash:
        bash_tool = BashTool(workspace_dir=str(workspace_dir))
        tools.append(bash_tool)
        print(f"{Colors.GREEN}✅ 已加载 Bash 工具（工作目录: {workspace_dir}）{Colors.RESET}")

    # 文件工具 - 需要 workspace 来解析相对路径
    if config.tools.enable_file_tools:
        tools.extend(
            [
                ReadTool(workspace_dir=str(workspace_dir)),
                WriteTool(workspace_dir=str(workspace_dir)),
                EditTool(workspace_dir=str(workspace_dir)),
            ]
        )
        print(f"{Colors.GREEN}✅ 已加载文件操作工具（工作空间: {workspace_dir}）{Colors.RESET}")

    # 会话笔记工具 - 需要 workspace 来存储记忆文件
    if config.tools.enable_note:
        tools.append(SessionNoteTool(memory_file=str(workspace_dir / ".agent_memory.json")))
        print(f"{Colors.GREEN}✅ 已加载会话笔记工具{Colors.RESET}")


async def _quiet_cleanup():
    """清理 MCP 连接，抑制嘈杂的 asyncgen 清理 traceback。"""
    # 静默处理 anyio/mcp 在 stdio_client 任务组被拆除时发出的 asyncgen 最终噪音。
    # 此处理程序故意不恢复：asyncgen 清理发生在 run_agent 返回后的 asyncio.run() 关闭期间，
    # 因此在此处恢复处理程序仍然会让噪音通过。由于这在进程退出前运行，吞掉后期异常是安全的。
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(lambda _loop, _ctx: None)
    try:
        await cleanup_mcp_connections()
    except Exception:
        pass


async def run_agent(workspace_dir: Path, task: str = None):
    """以交互模式或非交互模式运行 Agent。

    Args:
        workspace_dir: 工作空间目录路径
        task: 如果提供，执行此任务并退出（非交互模式）
    """
    session_start = datetime.now()

    # 1. 从包目录加载配置
    config_path = Config.get_default_config_path()

    if not config_path.exists():
        print(f"{Colors.RED}❌ 配置文件未找到{Colors.RESET}")
        print()
        print(f"{Colors.BRIGHT_CYAN}📦 配置搜索路径:{Colors.RESET}")
        print(f"  {Colors.DIM}1) mini_agent/config/config.yaml{Colors.RESET} (开发)")
        print(f"  {Colors.DIM}2) ~/.mini-agent/config/config.yaml{Colors.RESET} (用户)")
        print(f"  {Colors.DIM}3) <package>/config/config.yaml{Colors.RESET} (安装)")
        print()
        print(f"{Colors.BRIGHT_YELLOW}🚀 快速设置（推荐）:{Colors.RESET}")
        print(
            f"  {Colors.BRIGHT_GREEN}curl -fsSL https://raw.githubusercontent.com/MiniMax-AI/Mini-Agent/main/scripts/setup-config.sh | bash{Colors.RESET}"
        )
        print()
        print(f"{Colors.DIM}  这将自动:{Colors.RESET}")
        print(f"{Colors.DIM}    • 创建 ~/.mini-agent/config/{Colors.RESET}")
        print(f"{Colors.DIM}    • 下载配置文件{Colors.RESET}")
        print(f"{Colors.DIM}    • 引导您添加 API Key{Colors.RESET}")
        print()
        print(f"{Colors.BRIGHT_YELLOW}📝 手动设置:{Colors.RESET}")
        user_config_dir = Path.home() / ".mini-agent" / "config"
        example_config = Config.get_package_dir() / "config" / "config-example.yaml"
        print(f"  {Colors.DIM}mkdir -p {user_config_dir}{Colors.RESET}")
        print(f"  {Colors.DIM}cp {example_config} {user_config_dir}/config.yaml{Colors.RESET}")
        print(f"  {Colors.DIM}# 然后编辑 {user_config_dir}/config.yaml 添加您的 API Key{Colors.RESET}")
        print()
        return

    try:
        config = Config.from_yaml(config_path)
    except FileNotFoundError:
        print(f"{Colors.RED}❌ 错误: 配置文件未找到: {config_path}{Colors.RESET}")
        return
    except ValueError as e:
        print(f"{Colors.RED}❌ 错误: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}请检查配置文件格式{Colors.RESET}")
        return
    except Exception as e:
        print(f"{Colors.RED}❌ 错误: 加载配置文件失败: {e}{Colors.RESET}")
        return

    # 2. 初始化 LLM 客户端
    from mini_agent.retry import RetryConfig as RetryConfigBase

    # 转换配置格式
    retry_config = RetryConfigBase(
        enabled=config.llm.retry.enabled,
        max_retries=config.llm.retry.max_retries,
        initial_delay=config.llm.retry.initial_delay,
        max_delay=config.llm.retry.max_delay,
        exponential_base=config.llm.retry.exponential_base,
        retryable_exceptions=(Exception,),
    )

    # 创建重试回调函数以在终端中显示重试信息
    def on_retry(exception: Exception, attempt: int):
        """重试回调函数以显示重试信息"""
        print(f"\n{Colors.BRIGHT_YELLOW}⚠️  LLM 调用失败（尝试 {attempt}）: {str(exception)}{Colors.RESET}")
        next_delay = retry_config.calculate_delay(attempt - 1)
        print(f"{Colors.DIM}   将在 {next_delay:.1f} 秒后重试（尝试 {attempt + 1}）...{Colors.RESET}")

    # 将 provider 字符串转换为 LLMProvider 枚举
    provider = LLMProvider.ANTHROPIC if config.llm.provider.lower() == "anthropic" else LLMProvider.OPENAI

    llm_client = LLMClient(
        api_key=config.llm.api_key,
        provider=provider,
        api_base=config.llm.api_base,
        model=config.llm.model,
        retry_config=retry_config if config.llm.retry.enabled else None,
    )

    # 设置重试回调
    if config.llm.retry.enabled:
        llm_client.retry_callback = on_retry
        print(f"{Colors.GREEN}✅ LLM 重试机制已启用（最多重试 {config.llm.retry.max_retries} 次）{Colors.RESET}")

    # 3. 初始化基础工具（不依赖工作空间）
    tools, skill_loader = await initialize_base_tools(config)

    # 4. 添加依赖工作空间的工具
    add_workspace_tools(tools, config, workspace_dir)

    # 5. 加载系统提示词（带优先级搜索）
    system_prompt_path = Config.find_config_file(config.agent.system_prompt_path)
    if system_prompt_path and system_prompt_path.exists():
        system_prompt = system_prompt_path.read_text(encoding="utf-8")
        print(f"{Colors.GREEN}✅ 已加载系统提示词（来自: {system_prompt_path}）{Colors.RESET}")
    else:
        system_prompt = "You are Mini-Agent, an intelligent assistant powered by MiniMax M2.5 that can help users complete various tasks."
        print(f"{Colors.YELLOW}⚠️  未找到系统提示词，使用默认提示词{Colors.RESET}")

    # 6. 将技能元数据注入系统提示词（渐进式披露 - Level 1）
    if skill_loader:
        skills_metadata = skill_loader.get_skills_metadata_prompt()
        if skills_metadata:
            # 用实际元数据替换占位符
            system_prompt = system_prompt.replace("{SKILLS_METADATA}", skills_metadata)
            print(f"{Colors.GREEN}✅ 已将 {len(skill_loader.loaded_skills)} 个技能元数据注入系统提示词{Colors.RESET}")
        else:
            # 如果没有技能则移除占位符
            system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")
    else:
        # 如果未启用技能则移除占位符
        system_prompt = system_prompt.replace("{SKILLS_METADATA}", "")

    # 7. 创建 Agent
    agent = Agent(
        llm_client=llm_client,
        system_prompt=system_prompt,
        tools=tools,
        max_steps=config.agent.max_steps,
        workspace_dir=str(workspace_dir),
    )

    # 8. 显示欢迎信息
    if not task:
        print_banner()
        print_session_info(agent, workspace_dir, config.llm.model)

    # 8.5 非交互模式：执行任务并退出
    if task:
        print(f"\n{Colors.BRIGHT_BLUE}Agent{Colors.RESET} {Colors.DIM}›{Colors.RESET} {Colors.DIM}正在执行任务...{Colors.RESET}\n")
        agent.add_user_message(task)
        try:
            await agent.run()
        except Exception as e:
            print(f"\n{Colors.RED}❌ 错误: {e}{Colors.RESET}")
        finally:
            print_stats(agent, session_start)

        # 清理 MCP 连接
        await _quiet_cleanup()
        return

    # 9. 设置 prompt_toolkit 会话
    # 命令补全器
    command_completer = WordCompleter(
        ["/help", "/clear", "/history", "/stats", "/log", "/exit", "/quit", "/q"],
        ignore_case=True,
        sentence=True,
    )

    # 自定义提示样式
    prompt_style = Style.from_dict(
        {
            "prompt": "#00ff00 bold",  # 绿色加粗
            "separator": "#666666",  # 灰色
        }
    )

    # 自定义按键绑定
    kb = KeyBindings()

    @kb.add("c-u")  # Ctrl+U: 清除当前行
    def _(event):
        """清除当前输入行"""
        event.current_buffer.reset()

    @kb.add("c-l")  # Ctrl+L: 清除屏幕
    def _(event):
        """清除屏幕"""
        event.app.renderer.clear()

    @kb.add("c-j")  # Ctrl+J（对应 Ctrl+Enter）
    def _(event):
        """插入换行符"""
        event.current_buffer.insert_text("\n")

    # 创建带历史记录和自动建议的提示会话
    # 使用 FileHistory 实现跨会话持久化历史记录（存储在用户主目录）
    history_file = Path.home() / ".mini-agent" / ".history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(
        history=FileHistory(str(history_file)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=command_completer,
        style=prompt_style,
        key_bindings=kb,
    )

    # 10. 交互式循环
    while True:
        try:
            # 使用 prompt_toolkit 获取用户输入
            user_input = await session.prompt_async(
                [
                    ("class:prompt", "You"),
                    ("", " › "),
                ],
                multiline=False,
                enable_history_search=True,
            )
            user_input = user_input.strip()

            if not user_input:
                continue

            # 处理命令
            if user_input.startswith("/"):
                command = user_input.lower()

                if command in ["/exit", "/quit", "/q"]:
                    print(f"\n{Colors.BRIGHT_YELLOW}👋 再见！感谢使用 Mini Agent{Colors.RESET}\n")
                    print_stats(agent, session_start)
                    break

                elif command == "/help":
                    print_help()
                    continue

                elif command == "/clear":
                    # 清除消息历史但保留系统提示词
                    old_count = len(agent.messages)
                    agent.messages = [agent.messages[0]]  # 只保留系统消息
                    print(f"{Colors.GREEN}✅ 已清除 {old_count - 1} 条消息，开始新会话{Colors.RESET}\n")
                    continue

                elif command == "/history":
                    print(f"\n{Colors.BRIGHT_CYAN}当前会话消息数: {len(agent.messages)}{Colors.RESET}\n")
                    continue

                elif command == "/stats":
                    print_stats(agent, session_start)
                    continue

                elif command == "/log" or command.startswith("/log "):
                    # 解析 /log 命令
                    parts = user_input.split(maxsplit=1)
                    if len(parts) == 1:
                        # /log - 显示日志目录
                        show_log_directory(open_file_manager=True)
                    else:
                        # /log <filename> - 读取指定日志文件
                        filename = parts[1].strip("\"'")
                        read_log_file(filename)
                    continue

                else:
                    print(f"{Colors.RED}❌ 未知命令: {user_input}{Colors.RESET}")
                    print(f"{Colors.DIM}输入 /help 查看可用命令{Colors.RESET}\n")
                    continue

            # 普通对话 - 退出检查
            if user_input.lower() in ["exit", "quit", "q"]:
                print(f"\n{Colors.BRIGHT_YELLOW}👋 再见！感谢使用 Mini Agent{Colors.RESET}\n")
                print_stats(agent, session_start)
                break

            # 运行 Agent（支持 Esc 取消）
            print(
                f"\n{Colors.BRIGHT_BLUE}Agent{Colors.RESET} {Colors.DIM}›{Colors.RESET} {Colors.DIM}思考中...（按 Esc 取消）{Colors.RESET}\n"
            )
            agent.add_user_message(user_input)

            # 创建取消事件
            cancel_event = asyncio.Event()
            agent.cancel_event = cancel_event

            # Esc 键监听线程
            esc_listener_stop = threading.Event()
            esc_cancelled = [False]  # 用于线程访问的可变容器

            def esc_key_listener():
                """在单独线程中监听 Esc 键"""
                if platform.system() == "Windows":
                    try:
                        import msvcrt

                        while not esc_listener_stop.is_set():
                            if msvcrt.kbhit():
                                char = msvcrt.getch()
                                if char == b"\x1b":  # Esc
                                    print(f"\n{Colors.BRIGHT_YELLOW}⏹️  按下 Esc，正在取消...{Colors.RESET}")
                                    esc_cancelled[0] = True
                                    cancel_event.set()
                                    break
                            esc_listener_stop.wait(0.05)
                    except Exception:
                        pass
                    return

                # Unix/macOS
                try:
                    import select
                    import termios
                    import tty

                    fd = sys.stdin.fileno()
                    old_settings = termios.tcgetattr(fd)

                    try:
                        tty.setcbreak(fd)
                        while not esc_listener_stop.is_set():
                            rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if rlist:
                                char = sys.stdin.read(1)
                                if char == "\x1b":  # Esc
                                    print(f"\n{Colors.BRIGHT_YELLOW}⏹️  按下 Esc，正在取消...{Colors.RESET}")
                                    esc_cancelled[0] = True
                                    cancel_event.set()
                                    break
                    finally:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass

            # 启动 Esc 监听线程
            esc_thread = threading.Thread(target=esc_key_listener, daemon=True)
            esc_thread.start()

            # 运行 agent 并定期检查取消
            try:
                agent_task = asyncio.create_task(agent.run())

                # 在 agent 运行期间轮询取消状态
                while not agent_task.done():
                    if esc_cancelled[0]:
                        cancel_event.set()
                    await asyncio.sleep(0.1)

                # 获取结果
                _ = agent_task.result()

            except asyncio.CancelledError:
                print(f"\n{Colors.BRIGHT_YELLOW}⚠️  Agent 执行已取消{Colors.RESET}")
            finally:
                agent.cancel_event = None
                esc_listener_stop.set()
                esc_thread.join(timeout=0.2)

            # 视觉分隔
            print(f"\n{Colors.DIM}{'─' * 60}{Colors.RESET}\n")

        except KeyboardInterrupt:
            print(f"\n\n{Colors.BRIGHT_YELLOW}👋 检测到中断信号，正在退出...{Colors.RESET}\n")
            print_stats(agent, session_start)
            break

        except Exception as e:
            print(f"\n{Colors.RED}❌ 错误: {e}{Colors.RESET}")
            print(f"{Colors.DIM}{'─' * 60}{Colors.RESET}\n")

    # 11. 清理 MCP 连接
    await _quiet_cleanup()


def main():
    """CLI 主入口点"""
    # 解析命令行参数
    args = parse_args()

    # 处理 log 子命令
    if args.command == "log":
        if args.filename:
            read_log_file(args.filename)
        else:
            show_log_directory(open_file_manager=True)
        return

    # 确定工作空间目录
    # 展开 ~ 为用户主目录以提高可移植性
    if args.workspace:
        workspace_dir = Path(args.workspace).expanduser().absolute()
    else:
        # 使用当前工作目录
        workspace_dir = Path.cwd()

    # 确保工作空间目录存在
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # 运行 agent（配置始终从包目录加载）
    asyncio.run(run_agent(workspace_dir, task=args.task))


if __name__ == "__main__":
    main()
