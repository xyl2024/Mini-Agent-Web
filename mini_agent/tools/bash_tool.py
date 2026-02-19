"""Shell 命令执行工具，支持后台进程管理。

支持 bash（Unix/Linux/macOS）和 PowerShell（Windows）。
"""

import asyncio
import platform
import re
import time
import uuid
from typing import Any

from pydantic import Field, model_validator

from .base import Tool, ToolResult


class BashOutputResult(ToolResult):
    """Bash 命令执行结果，分离 stdout 和 stderr。

    继承自 ToolResult，包含：
    - success: bool
    - content: str（用于格式化的输出消息，从 stdout/stderr 自动生成）
    - error: str | None（用于错误消息）
    """

    stdout: str = Field(description="命令的标准输出")
    stderr: str = Field(description="命令的标准错误输出")
    exit_code: int = Field(description="命令的退出码")
    bash_id: str | None = Field(default=None, description="Shell 进程 ID（仅当 run_in_background=True 时）")

    @model_validator(mode="after")
    def format_content(self) -> "BashOutputResult":
        """如果 content 为空，则从 stdout 和 stderr 自动格式化内容。"""
        output = ""
        if self.stdout:
            output += self.stdout
        if self.stderr:
            output += f"\n[stderr]:\n{self.stderr}"
        if self.bash_id:
            output += f"\n[bash_id]:\n{self.bash_id}"
        if self.exit_code:
            output += f"\n[exit_code]:\n{self.exit_code}"

        if not output:
            output = "(no output)"

        self.content = output
        return self


class BackgroundShell:
    """后台 Shell 数据容器。

    纯数据类，仅存储状态和输出。
    IO 操作由外部的 BackgroundShellManager 管理。
    """

    def __init__(self, bash_id: str, command: str, process: "asyncio.subprocess.Process", start_time: float):
        self.bash_id = bash_id
        self.command = command
        self.process = process
        self.start_time = start_time
        self.output_lines: list[str] = []
        self.last_read_index = 0
        self.status = "running"
        self.exit_code: int | None = None

    def add_output(self, line: str):
        """添加新的输出行。"""
        self.output_lines.append(line)

    def get_new_output(self, filter_pattern: str | None = None) -> list[str]:
        """获取自上次检查以来的新输出，可按正则表达式过滤。"""
        new_lines = self.output_lines[self.last_read_index :]
        self.last_read_index = len(self.output_lines)

        if filter_pattern:
            try:
                pattern = re.compile(filter_pattern)
                new_lines = [line for line in new_lines if pattern.search(line)]
            except re.error:
                # 无效的正则表达式，返回所有行
                pass

        return new_lines

    def update_status(self, is_alive: bool, exit_code: int | None = None):
        """更新进程状态。"""
        if not is_alive:
            self.status = "completed" if exit_code == 0 else "failed"
            self.exit_code = exit_code
        else:
            self.status = "running"

    async def terminate(self):
        """终止后台进程。"""
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
        self.status = "terminated"
        self.exit_code = self.process.returncode


class BackgroundShellManager:
    """所有后台 Shell 进程的管理器。"""

    _shells: dict[str, BackgroundShell] = {}
    _monitor_tasks: dict[str, asyncio.Task] = {}

    @classmethod
    def add(cls, shell: BackgroundShell) -> None:
        """将后台 shell 添加到管理。"""
        cls._shells[shell.bash_id] = shell

    @classmethod
    def get(cls, bash_id: str) -> BackgroundShell | None:
        """通过 ID 获取后台 shell。"""
        return cls._shells.get(bash_id)

    @classmethod
    def get_available_ids(cls) -> list[str]:
        """获取所有可用的 bash ID。"""
        return list(cls._shells.keys())

    @classmethod
    def _remove(cls, bash_id: str) -> None:
        """从管理中移除后台 shell（仅供内部使用）。"""
        if bash_id in cls._shells:
            del cls._shells[bash_id]

    @classmethod
    async def start_monitor(cls, bash_id: str) -> None:
        """开始监控后台 shell 的输出。"""
        shell = cls.get(bash_id)
        if not shell:
            return

        async def monitor():
            try:
                process = shell.process
                # 持续读取输出直到进程结束
                while process.returncode is None:
                    try:
                        if process.stdout:
                            line = await asyncio.wait_for(process.stdout.readline(), timeout=0.1)
                            if line:
                                decoded_line = line.decode("utf-8", errors="replace").rstrip("\n")
                                shell.add_output(decoded_line)
                            else:
                                break
                    except asyncio.TimeoutError:
                        await asyncio.sleep(0.1)
                        continue
                    except Exception:
                        await asyncio.sleep(0.1)
                        continue

                # 进程已结束，等待退出码
                try:
                    returncode = await process.wait()
                except Exception:
                    returncode = -1

                shell.update_status(is_alive=False, exit_code=returncode)

            except Exception as e:
                if bash_id in cls._shells:
                    cls._shells[bash_id].status = "error"
                    cls._shells[bash_id].add_output(f"监控错误: {str(e)}")
            finally:
                if bash_id in cls._monitor_tasks:
                    del cls._monitor_tasks[bash_id]

        task = asyncio.create_task(monitor())
        cls._monitor_tasks[bash_id] = task

    @classmethod
    def _cancel_monitor(cls, bash_id: str) -> None:
        """取消并移除监控任务（仅供内部使用）。"""
        if bash_id in cls._monitor_tasks:
            task = cls._monitor_tasks[bash_id]
            if not task.done():
                task.cancel()
            del cls._monitor_tasks[bash_id]

    @classmethod
    async def terminate(cls, bash_id: str) -> BackgroundShell:
        """终止后台 shell 并清理所有资源。

       _id: 后台 shell 的唯一标识 Args:
            bash符

        Returns:
            已终止的 BackgroundShell 对象

        Raises:
            ValueError: 如果未找到 shell
        """
        shell = cls.get(bash_id)
        if not shell:
            raise ValueError(f"未找到 Shell: {bash_id}")

        # 终止进程
        await shell.terminate()

        # 清理监控并从管理器中移除
        cls._cancel_monitor(bash_id)
        cls._remove(bash_id)

        return shell


class BashTool(Tool):
    """在前台或后台执行 Shell 命令。

    自动检测操作系统并使用相应的 Shell：
    - Windows: PowerShell
    - Unix/Linux/macOS: bash
    """

    def __init__(self, workspace_dir: str | None = None):
        """使用特定于操作系统的 Shell 检测初始化 BashTool。

        Args:
            workspace_dir: 命令执行的工作目录。
                           如果提供，所有命令都在此目录中运行。
                           如果为 None，命令在进程的 cwd 中运行。
        """
        self.is_windows = platform.system() == "Windows"
        self.shell_name = "PowerShell" if self.is_windows else "bash"
        self.workspace_dir = workspace_dir

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        shell_examples = {
            "Windows": """在前台或后台执行 PowerShell 命令。

适用于 git、npm、docker 等终端操作。请勿用于文件操作 - 请使用专门的工具。

参数：
  - command（必需）：要执行的 PowerShell 命令
  - timeout（可选）：超时时间（秒），前台命令默认 120，最大 600
  - run_in_background（可选）：设为 true 以运行长时间执行的命令（如服务器等）

提示：
  - 包含空格的路径需要加引号：cd "My Documents"
  - 用分号连接依赖命令：git add . ; git commit -m "msg"
  - 尽可能使用绝对路径而不是 cd
  - 后台命令使用 bash_output 监控，使用 bash_kill 终止

示例：
  - git status
  - npm test
  - python -m http.server 8080（配合 run_in_background=true）""",
            "Unix": """在前台或后台执行 bash 命令。

适用于 git、npm、docker 等终端操作。请勿用于文件操作 - 请使用专门的工具。

参数：
  - command（必需）：要执行的 bash 命令
  - timeout（可选）：超时时间（秒），前台命令默认 120，最大 600
  - run_in_background（可选）：设为 true 以运行长时间执行的命令（如服务器等）

提示：
  - 包含空格的路径需要加引号：cd "My Documents"
  - 用 && 连接依赖命令：git add . && git commit -m "msg"
  - 尽可能使用绝对路径而不是 cd
  - 后台命令使用 bash_output 监控，使用 bash_kill 终止

示例：
  - git status
  - npm test
  - python3 -m http.server 8080（配合 run_in_background=true）""",
        }
        return shell_examples["Windows"] if self.is_windows else shell_examples["Unix"]

    @property
    def parameters(self) -> dict[str, Any]:
        cmd_desc = f"要执行的 {self.shell_name} 命令。包含空格的路径使用双引号括起来。"
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": cmd_desc,
                },
                "timeout": {
                    "type": "integer",
                    "description": "可选：超时时间（秒），默认 120，最大 600。仅适用于前台命令。",
                    "default": 120,
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "可选：设为 true 以在后台运行命令。适用于长时间运行的命令（如服务器）。可使用 bash_output 工具监控输出。",
                    "default": False,
                },
            },
            "required": ["command"],
        }

    async def execute(
        self,
        command: str,
        timeout: int = 120,
        run_in_background: bool = False,
    ) -> ToolResult:
        """执行 Shell 命令，支持可选的后台执行。

        Args:
            command: 要执行的 Shell 命令
            timeout: 超时时间（秒），默认 120，最大 600
            run_in_background: 设为 true 以在后台运行命令

        Returns:
            包含命令输出和状态的 BashExecutionResult
        """

        try:
            # 验证超时时间
            if timeout > 600:
                timeout = 600
            elif timeout < 1:
                timeout = 120

            # 准备特定于 Shell 的命令执行
            if self.is_windows:
                # Windows：使用 PowerShell 并设置适当的编码
                shell_cmd = ["powershell.exe", "-NoProfile", "-Command", command]
            else:
                # Unix/Linux/macOS：使用 bash
                shell_cmd = command

            if run_in_background:
                # 后台执行：创建隔离进程
                bash_id = str(uuid.uuid4())[:8]

                # 启动后台进程，合并 stdout/stderr
                if self.is_windows:
                    process = await asyncio.create_subprocess_exec(
                        *shell_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=self.workspace_dir,
                    )
                else:
                    process = await asyncio.create_subprocess_shell(
                        shell_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=self.workspace_dir,
                    )

                # 创建后台 shell 并添加到管理器
                bg_shell = BackgroundShell(bash_id=bash_id, command=command, process=process, start_time=time.time())
                BackgroundShellManager.add(bg_shell)

                # 启动监控任务
                await BackgroundShellManager.start_monitor(bash_id)

                # 立即返回 bash_id
                message = f"命令已在后台启动。使用 bash_output 监控 (bash_id='{bash_id}')。"
                formatted_content = f"{message}\n\n命令: {command}\nBash ID: {bash_id}"

                return BashOutputResult(
                    success=True,
                    content=formatted_content,
                    stdout=f"后台命令已启动，ID: {bash_id}",
                    stderr="",
                    exit_code=0,
                    bash_id=bash_id,
                )

            else:
                # 前台执行：创建隔离进程
                if self.is_windows:
                    process = await asyncio.create_subprocess_exec(
                        *shell_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=self.workspace_dir,
                    )
                else:
                    process = await asyncio.create_subprocess_shell(
                        shell_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=self.workspace_dir,
                    )

                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    process.kill()
                    error_msg = f"命令在 {timeout} 秒后超时"
                    return BashOutputResult(
                        success=False,
                        error=error_msg,
                        stdout="",
                        stderr=error_msg,
                        exit_code=-1,
                    )

                # 解码输出
                stdout_text = stdout.decode("utf-8", errors="replace")
                stderr_text = stderr.decode("utf-8", errors="replace")

                # 创建结果（content 由 model_validator 自动格式化）
                is_success = process.returncode == 0
                error_msg = None
                if not is_success:
                    error_msg = f"命令失败，退出码 {process.returncode}"
                    if stderr_text:
                        error_msg += f"\n{stderr_text.strip()}"

                return BashOutputResult(
                    success=is_success,
                    error=error_msg,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=process.returncode or 0,
                )

        except Exception as e:
            return BashOutputResult(
                success=False,
                error=str(e),
                stdout="",
                stderr=str(e),
                exit_code=-1,
            )


class BashOutputTool(Tool):
    """获取后台 bash shell 的输出。"""

    @property
    def name(self) -> str:
        return "bash_output"

    @property
    def description(self) -> str:
        return """获取运行中或已完成的后台 bash shell 的输出。

        - 接受一个 bash_id 参数来标识 shell
        - 始终仅返回自上次检查以来的新输出
        - 返回 stdout 和 stderr 输出以及 shell 状态
        - 支持可选的正则表达式过滤，仅显示匹配模式的行
        - 当需要监控或检查长时间运行的 shell 输出时使用此工具
        - Shell ID 可通过使用 run_in_background=true 的 bash 工具获得

        进程状态值：
          - "running": 仍在执行
          - "completed": 成功完成
          - "failed": 带错误完成
          - "terminated": 已被终止
          - "error": 发生错误

        示例: bash_output(bash_id="abc12345")"""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bash_id": {
                    "type": "string",
                    "description": "要获取输出的后台 shell 的 ID。使用 run_in_background=true 启动命令时会返回 Shell ID。",
                },
                "filter_str": {
                    "type": "string",
                    "description": "可选的正则表达式用于过滤输出行。仅匹配此正则表达式的行将被包含在结果中。任何不匹配的行将无法再读取。",
                },
            },
            "required": ["bash_id"],
        }

    async def execute(
        self,
        bash_id: str,
        filter_str: str | None = None,
    ) -> BashOutputResult:
        """获取后台 shell 的输出。

        Args:
            bash_id: 后台 shell 的唯一标识符
            filter_str: 可选的正则表达式模式，用于过滤输出行

        Returns:
            包含 shell 输出的 BashOutputResult，包括 stdout、stderr、状态和成功标志
        """

        try:
            # 从管理器获取后台 shell
            bg_shell = BackgroundShellManager.get(bash_id)
            if not bg_shell:
                available_ids = BackgroundShellManager.get_available_ids()
                return BashOutputResult(
                    success=False,
                    error=f"未找到 Shell: {bash_id}。可用: {available_ids or 'none'}",
                    stdout="",
                    stderr="",
                    exit_code=-1,
                )

            # 获取新输出
            new_lines = bg_shell.get_new_output(filter_pattern=filter_str)
            stdout = "\n".join(new_lines) if new_lines else ""

            return BashOutputResult(
                success=True,
                stdout=stdout,
                stderr="",  # 后台 shell 合并 stdout/stderr
                exit_code=bg_shell.exit_code if bg_shell.exit_code is not None else 0,
                bash_id=bash_id,
            )

        except Exception as e:
            return BashOutputResult(
                success=False,
                error=f"获取 bash 输出失败: {str(e)}",
                stdout="",
                stderr=str(e),
                exit_code=-1,
            )


class BashKillTool(Tool):
    """终止正在运行的后台 bash shell。"""

    @property
    def name(self) -> str:
        return "bash_kill"

    @property
    def description(self) -> str:
        return """通过 ID 终止正在运行的后台 bash shell。

        - 接受一个 bash_id 参数来标识要终止的 shell
        - 首先尝试正常终止（SIGTERM），如需要则强制终止（SIGKILL）
        - 返回终止前的最终状态和任何剩余输出
        - 清理与 shell 关联的所有资源
        - 当需要终止长时间运行的 shell 时使用此工具
        - Shell ID 可通过使用 run_in_background=true 的 bash 工具获得

        示例: bash_kill(bash_id="abc12345")"""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bash_id": {
                    "type": "string",
                    "description": "要终止的后台 shell 的 ID。使用 run_in_background=true 启动命令时会返回 Shell ID。",
                },
            },
            "required": ["bash_id"],
        }

    async def execute(self, bash_id: str) -> BashOutputResult:
        """终止后台 shell 进程。

        Args:
            bash_id: 要终止的后台 shell 的唯一标识符

        Returns:
            包含终止状态和剩余输出的 BashOutputResult
        """

        try:
            # 在终止前获取剩余输出
            bg_shell = BackgroundShellManager.get(bash_id)
            if bg_shell:
                remaining_lines = bg_shell.get_new_output()
            else:
                remaining_lines = []

            # 通过管理器终止（处理所有清理工作）
            bg_shell = await BackgroundShellManager.terminate(bash_id)

            # 获取剩余输出
            stdout = "\n".join(remaining_lines) if remaining_lines else ""

            return BashOutputResult(
                success=True,
                stdout=stdout,
                stderr="",
                exit_code=bg_shell.exit_code if bg_shell.exit_code is not None else 0,
                bash_id=bash_id,
            )

        except ValueError as e:
            # 未找到 shell
            available_ids = BackgroundShellManager.get_available_ids()
            return BashOutputResult(
                success=False,
                error=f"{str(e)}。可用: {available_ids or 'none'}",
                stdout="",
                stderr=str(e),
                exit_code=-1,
            )
        except Exception as e:
            return BashOutputResult(
                success=False,
                error=f"终止 bash shell 失败: {str(e)}",
                stdout="",
                stderr=str(e),
                exit_code=-1,
            )
