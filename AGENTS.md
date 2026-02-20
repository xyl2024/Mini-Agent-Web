# AGENTS.md - Agent 编码指南

本文件为在本仓库工作的 agent 提供编码指南。

## 构建、测试和检查命令

### 环境配置

```bash
# 安装依赖
uv sync

# 初始化 git 子模块（Claude Skills）
git submodule update --init --recursive

# 复制配置模板
cp mini_agent/config/config-example.yaml mini_agent/config/config.yaml
```

### 运行应用

```bash
# 作为模块运行（适合调试）
uv run python -m mini_agent.cli

# 以可编辑模式安装
uv tool install -e .
mini-agent --workspace /path/to/project
```

### 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行指定的测试文件
pytest tests/test_agent.py tests/test_note_tool.py -v

# 运行单个测试
pytest tests/test_agent.py::test_agent_simple_task -v

# 运行测试并捕获打印输出
pytest tests/test_agent.py -v -s

# 并行运行测试（需安装 pytest-xdist）
pytest tests/ -n auto
```

### 开发工具

```bash
# 代码检查（pyproject.toml 中配置了 pylint）
# 无需显式 lint 命令，如需可添加 ruff

# 类型检查（如安装了 mypy）
# mypy mini_agent/
```

## 代码风格指南

### 通用规范

- **Python 版本**: 3.10+
- **类型提示**: 使用 Python 3.10+ 语法（用 `list[Tool]` 代替 `List[Tool]`，用 `str | None` 代替 `Optional[str]`）
- **异步**: I/O 操作使用 `async`/`await`；使用 `uv run` 确保正确的异步支持
- **文档字符串**: 使用 Google 风格或简单文档字符串；可用中文或英文

### 命名规范

- **类**: PascalCase（如 `Agent`、`LLMClient`、`ToolResult`）
- **函数/方法**: snake_case（如 `execute()`、`add_user_message()`）
- **常量**: UPPER_CASE（如 `MAX_STEPS`、`DEFAULT_TIMEOUT`）
- **私有方法**: 使用下划线前缀（如 `_check_cancelled()`）

### 导入顺序

- **标准库**: 最先
- **第三方库**: 其次（pydantic、httpx、pytest 等）
- **本地模块**: 最后（from .module import ...）

```python
# 导入示例
import asyncio
import json
from pathlib import Path
from typing import Optional

import pydantic
import httpx
import pytest

from .llm import LLMClient
from .logger import AgentLogger
from .tools.base import Tool, ToolResult
```

### 错误处理

- 尽可能使用特定的异常类型
- 工具失败时返回 `ToolResult(success=False, error=...)`
- 正确记录错误
- 记录后传播关键错误

```python
# 工具错误处理模式
try:
    result = await some_operation()
    return ToolResult(success=True, content=result)
except Exception as e:
    return ToolResult(success=False, error=f"操作失败: {str(e)}")
```

### Pydantic 模型

- 使用 `pydantic.BaseModel` 进行配置和数据传输
- 有默认值或验证的字段使用 `Field()`
- 复杂验证使用 `model_validator`

```python
from pydantic import BaseModel, Field

class AgentConfig(BaseModel):
    max_steps: int = 50
    workspace_dir: str = "./workspace"
    retry: RetryConfig = Field(default_factory=RetryConfig)
```

### 工具实现

所有工具应继承自 `Tool` 基类：

```python
from mini_agent.tools.base import Tool, ToolResult

class MyTool(Tool):
    @property
    def name(self) -> str:
        return "my_tool"
    
    @property
    def description(self) -> str:
        return "LLM 工具描述"
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "param": {"type": "string", "description": "参数描述"}
            },
            "required": ["param"]
        }
    
    async def execute(self, param: str) -> ToolResult:
        try:
            # 实现逻辑
            return ToolResult(success=True, content="结果")
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

### 文件结构

```
mini_agent/
├── agent.py          # 主 agent 循环
├── cli.py            # CLI 入口
├── config.py         # 配置管理
├── logger.py         # 日志工具
├── retry.py          # API 重试机制
├── llm/              # LLM 客户端
│   ├── anthropic_client.py
│   ├── openai_client.py
│   └── llm_wrapper.py
├── tools/           # 工具实现
│   ├── base.py      # 工具基类
│   ├── file_tools.py
│   ├── bash_tool.py
│   └── ...
├── config/          # 配置文件
│   ├── config-example.yaml
│   └── system_prompt.md
└── skills/          # Claude Skills（子模块）
```

### 测试规范

- 使用 `pytest` 和 `pytest-asyncio` 进行异步测试
- 异步测试函数使用 `@pytest.mark.asyncio` 装饰器
- 需要文件操作的测试使用 `tempfile.TemporaryDirectory()`
- 测试文件放在 `tests/` 目录

```python
@pytest.mark.asyncio
async def test_my_feature():
    # 测试实现
    pass
```

### 配置规范

- 使用 YAML 文件进行配置（`config.yaml`）
- 配置优先级：开发 > 用户 > 包默认
- 敏感信息尽量使用环境变量

### 日志规范

- 使用 `logging.getLogger(__name__)` 获取模块日志器
- 使用 `AgentLogger` 类进行 agent 特定日志记录
- 日志消息包含相关上下文

### Git 子模块

`skills/` 目录是 git 子模块。更新方式：

```bash
git submodule update --remote --merge
```
