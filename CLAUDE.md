# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在本项目中工作提供指导。

## 项目概述

**Mini Agent** 是一个基于 MiniMax M2.5 模型构建的轻量级 AI Agent 示例项目。它使用 Anthropic 兼容的 API，支持交错思考（interleaved thinking）以处理复杂推理任务。

## 常用命令

### 开发环境初始化

```bash
# 安装依赖
uv sync

# 初始化 git 子模块（用于 Claude Skills）
git submodule update --init --recursive

# 复制配置模板
cp mini_agent/config/config-example.yaml mini_agent/config/config.yaml
```

### 运行 Agent

```bash
# 作为模块运行（适合调试）
uv run python -m mini_agent.cli

# 以可编辑模式安装
uv tool install -e .
mini-agent --workspace /path/to/project
```

### 测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行指定测试文件
pytest tests/test_agent.py tests/test_note_tool.py -v
```

## 架构

```
mini_agent/
├── agent.py          # 核心 Agent 执行循环
├── cli.py            # CLI 入口，支持交互模式
├── config.py         # 配置管理
├── logger.py         # 日志工具
├── retry.py          # API 调用重试机制
├── llm/              # LLM 客户端实现
│   ├── anthropic_client.py  # Anthropic 兼容客户端
│   ├── openai_client.py     # OpenAI 兼容客户端
│   └── llm_wrapper.py      # 统一 LLM 接口
├── tools/            # 工具实现
│   ├── bash_tool.py        # Shell 命令执行
│   ├── file_tools.py      # 文件读写编辑操作
│   ├── note_tool.py       # Session 持久化记忆
│   ├── skill_loader.py    # Claude Skills 加载器
│   ├── skill_tool.py      # Skill 执行工具
│   └── mcp_loader.py     # MCP 协议支持
├── config/           # 配置文件
│   ├── config-example.yaml
│   ├── mcp-example.json
│   └── system_prompt.md
├── skills/           # Claude Skills（git 子模块）
│   └── [各种技能包]
└── acp/              # Agent Communication Protocol 服务器
    └── server.py
```

### 核心组件

- **Agent 循环** (`agent.py`): 主执行循环，包含工具调用、上下文管理和对话摘要
- **LLM 客户端** (`llm/`): 支持 Anthropic 兼容和 OpenAI 兼容 API，带自动重试
- **工具** (`tools/`): 文件操作、Shell 执行、Session 笔记、Claude Skills 和 MCP 集成
- **ACP 服务器** (`acp/`): Agent 通信协议，用于 Zed 编辑器集成

### 配置文件

配置文件位置（按优先级排序）：
1. `mini_agent/config/config.yaml` - 开发模式
2. `~/.mini-agent/config/config.yaml` - 用户配置
3. 包默认配置

关键配置项：
- `api_key`、`api_base`、`model` - LLM 配置
- `max_steps` - Agent 最大执行步数
- `workspace_dir` - 文件操作的工作目录
- `tools.enable_*` - 各工具类型的开关

## 依赖

- Python 3.10+
- pydantic, pyyaml, httpx, mcp, pytest
- tiktoken（token 计数）
- prompt-toolkit（CLI）
