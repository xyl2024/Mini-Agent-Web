# Mini Agent 架构文档

## 系统架构图

```
+------------------------------------------------------------------+
|                         Client Layer                              |
+------------------------------------------------------------------+
|                                                                  |
|  +----------------+              +----------------+              |
|  |   CLI Entry    |              |  ACP Server    |              |
|  |   cli.py       |              |  acp/server.py |              |
|  +-------+--------+              +-------+--------+              |
|          |                               |                       |
+----------+-------------------------------+-----------------------+
           |                               |
           v                               v
+------------------------------------------------------------------+
|                         Core Layer                                |
+------------------------------------------------------------------+
|                                                                  |
|  +------------+  +-----------+  +-------+  +-------+  +--------+  |
|  |   Agent    |  |  Config   |  |Logger |  | Retry |  | Schema |  |
|  |  agent.py  |  | config.py |  |logger.|  |retry. |  |schema/ |  |
|  |            |  |           |  |  py   |  |  py   |  |        |  |
|  +----+------+  +-----------+  +-------+  +-------+  +--------+  |
|       |                                                          |
+-------+----------------------------------------------------------+
         |
         v
+------------------------------------------------------------------+
|                          LLM Layer                                |
+------------------------------------------------------------------+
|                                                                  |
|  +-------------+                                                  |
|  |LLM Wrapper  |                                                  |
|  |llm_wrapper. |                                                  |
|  |    py       |                                                  |
|  +-----+-------+                                                  |
|        |                                                          |
|   +----+----+                      +---------------+             |
|   |Anthropic |                      |   OpenAI      |             |
|   | Client   |                      |   Client      |             |
|   |anthropic_|                      |   openai_     |             |
|   | client.py|                      |   client.py   |             |
|   +----+-----+                      +-------+-------+             |
|        |                                   |                      |
+--------+-----------------------------------+----------------------+
         |                                   |
         v                                   v
+------------------------------------------------------------------+
|                        External APIs                              |
+------------------------------------------------------------------+
|                                                                  |
|  +-------------+                  +-------------+                |
|  | Claude API  |                  | OpenAI API  |                |
|  +-------------+                  +-------------+                |
|                                                                  |
+------------------------------------------------------------------+
         |
         v
+------------------------------------------------------------------+
|                          Tool Layer                               |
+------------------------------------------------------------------+
|                                                                  |
|  +-------------+     +----------+     +-----------+              |
|  |  Tool Base  |     |  Bash    |     | File      |              |
|  |   base.py   |     |  Tool    |     | Tools     |              |
|  | (abstract)  |     |bash_tool.|     |file_tools.|              |
|  +------+------+     |   py     |     |   py      |              |
|       /|\            +-----+----+     +------+-----+             |
|        |                   |                  |                  |
|  +-----+----+        +-----+----+        +-----+-----+            |
|  | Note Tool|        |Skill Tool|        |MCP Loader|            |
|  |note_tool.|        |skill_    |        |mcp_loader.|            |
|  |   py     |        |tool.py   |        |   py      |            |
|  +----------+        +-----+----+        +-----------+            |
|                                                                  |
|                   +---------------+                                |
|                   |Skill Loader   |                                |
|                   |skill_loader.py|                               |
|                   +-------+-------+                                |
+------------------------------------------------------------------+
         |
         v
+------------------------------------------------------------------+
|                           Skills                                  |
+------------------------------------------------------------------+
|                                                                  |
|  +----------------+  +------------------+  +-----------------+   |
|  |algorithmic-art |  |artifacts-builder |  |document-skills  |   |
|  +----------------+  +------------------+  +-----------------+   |
|                                                                  |
|  +----------------+  +------------------+  +-----------------+   |
|  |  mcp-builder   |  |  skill-creator   |  |slack-gif-creator|  |
|  +----------------+  +------------------+  +-----------------+   |
|                                                                  |
|  +----------------+  +------------------+                        |
|  | webapp-testing |  |  template-skill  |                        |
|  +----------------+  +------------------+                        |
|                                                                  |
+------------------------------------------------------------------+
```

## 模块说明

### 1. 入口层 (Client Layer)

```
+----------------+     +----------------+
|    CLI Entry   |     |   ACP Server   |
|    cli.py      |     | acp/server.py  |
+----------------+     +----------------+
```

| 模块 | 文件 | 说明 |
|------|------|------|
| CLI | `cli.py` | 命令行入口，提供交互式 agent 界面 |
| ACP Server | `acp/server.py` | Agent Communication Protocol 服务器 |

### 2. 核心层 (Core Layer)

```
+--------+    +--------+    +-------+    +-------+    +--------+
| Agent  |    | Config |    |Logger |    | Retry |    | Schema |
|agent.py|<-->|config. |<-->|logger.|<-->|retry. |<-->|schema/ |
|        |    |  py    |    |  py   |    |  py   |    |        |
+--------+    +--------+    +-------+    +-------+    +--------+
```

| 模块 | 文件 | 说明 |
|------|------|------|
| Agent | `agent.py` | 主 agent 循环，协调各模块工作 |
| Config | `config.py` | 配置管理，加载 YAML 配置 |
| Logger | `logger.py` | 日志工具 |
| Retry | `retry.py` | API 重试机制 |
| Schema | `schema/schema.py` | 数据模型定义 |

### 3. LLM 层 (LLM Layer)

```
+---------------------+         +---------------------+
|    LLM Wrapper      |         |    LLM Clients      |
|  +---------------+  |         |  +---------------+  |
|  |llm_wrapper.py |  |         |  |Anthropic Client|
|  |               |  |         |  |anthropic_client.|
|  +-------+-------+  |         |  |      py        |  |
|          |          |         |  +---------------+  |
|          |          |         |  +---------------+  |
+----------+----------+         |  |OpenAI Client   ||
                               |  |openai_client.py ||
                               |  +---------------+  |
                               +---------------------+
```

 | 说明 |
|| 模块 | 文件------|------|------|
| LLM Wrapper | `llm_wrapper.py` | LLM 客户端封装，统一接口 |
| Anthropic Client | `anthropic_client.py` | Anthropic API 客户端 |
| OpenAI Client | `openai_client.py` | OpenAI API 客户端 |

### 4. 工具层 (Tool Layer)

```
+---------------------------+
|      Tool Base (abstract) |
|        tools/base.py      |
+-----------+---------------+
            ^
            |
    +-------+-------+
    |               |
+----+-------+  +---+---------+
| File Tools |  |  Note Tool  |
|file_tools. |  | note_tool.py|
|    py      |  +-------------+
+-------------+
            |
+--------+  +---------+  +------------+  +-------------+
| Bash   |  | Skill   |  | MCP Loader |  |Skill Loader |
|Tool    |  | Tool    |  |mcp_loader. |  |skill_loader.|
|bash_   |  |skill_   |  |    py      |  |    py       |
|tool.py |  |tool.py  |  +-------------+  +-------------+
+--------+  +---------+
```

| 模块 | 文件 | 说明 |
|------|------|------|
| Tool Base | `tools/base.py` | 工具基类，定义接口 |
| Bash Tool | `tools/bash_tool.py` | 执行 shell 命令 |
| File Tools | `tools/file_tools.py` | 文件读写、搜索、编辑 |
| Note Tool | `tools/note_tool.py` | 会话笔记管理 |
| Skill Tool | `tools/skill_tool.py` | 执行 skill |
| MCP Loader | `tools/mcp_loader.py` | 加载 MCP 服务器工具 |
| Skill Loader | `tools/skill_loader.py` | 加载本地 skills |

### 5. Skills

```
+------------------+  +-------------------+  +------------------+
| algorithmic-art  |  | artifacts-builder |  | document-skills |
+------------------+  +-------------------+  +------------------+

+------------------+  +-------------------+  +------------------+
|   mcp-builder    |  |   skill-creator    |  |slack-gif-creator |
+------------------+  +-------------------+  +------------------+

+------------------+  +-------------------+
|  webapp-testing  |  |   template-skill  |
+------------------+  +-------------------+
```

| Skill | 路径 | 说明 |
|-------|------|------|
| algorithmic-art | `skills/algorithmic-art` | 算法艺术生成 |
| artifacts-builder | `skills/artifacts-builder` | 构建产物生成 |
| document-skills | `skills/document-skills` | 文档处理（PDF/DOCX/PPTX） |
| mcp-builder | `skills/mcp-builder` | MCP 服务器构建 |
| skill-creator | `skills/skill-creator` | Skill 创建工具 |
| slack-gif-creator | `skills/slack-gif-creator` | Slack GIF 创建 |
| webapp-testing | `skills/webapp-testing` | Web 应用测试 |
| template-skill | `skills/template-skill` | Skill 模板 |

## 数据流

```
+------+     +-------+     +-----+     +-----+     +------+     +------+
| User | --> |CLI/ACP| --> |Agent| --> | LLM | --> |Tools | --> |Skills|
+------+     +-------+     +-----+     +-----+     +------+     +------+
                                                                       |
                                                                       v
+------+     +-------+     +-----+     +-----+     +------+     +------+
| User | <-- |CLI/ACP| <-- |Agent| <-- | LLM | <-- |Tools | <-- |Skills|
+------+     +-------+     +-----+     +-----+     +------+     +------+

步骤说明:
1. User 输入请求到 CLI 或 ACP Server
2. CLI/ACP 创建 Agent 实例
3. Agent 发送消息到 LLM
4. LLM 返回响应，包含工具调用
5. Agent 解析工具调用，交给 Tools 执行
6. Tools 调用 Skills 完成具体任务
7. 结果逐层返回，最终展示给 User
```

## 配置文件结构

```yaml
# config.yaml
llm:
  providers:
    anthropic:
      api_key: ${ANTHROPIC_API_KEY}
      model: claude-sonnet-4-20250514
    openai:
      api_key: ${OPENAI_API_KEY}
      model: gpt-4o
  default_provider: anthropic

agent:
  max_steps: 50
  max_iterations: 100

workspace:
  dir: ./workspace

tools:
  enabled:
    - bash
    - read
    - write
    - glob
    - grep
    - edit
    - note
    - skill
  mcp:
    servers: []

skills:
  dir: ./skills
  enabled: []
```

## 目录结构

```
mini_agent/
├── agent.py              # 主 agent 循环 (21279 行)
├── cli.py                # CLI 入口 (34087 行)
├── config.py             # 配置管理
├── logger.py             # 日志工具
├── retry.py              # API 重试机制
├── acp/
│   └── server.py         # ACP 服务器
├── config/
│   ├── config-example.yaml
│   └── system_prompt.md
├── llm/
│   ├── base.py           # LLM 基类
│   ├── llm_wrapper.py    # LLM 封装
│   ├── anthropic_client.py
│   └── openai_client.py
├── schema/
│   └── schema.py
├── skills/               # 内置 skills (子模块)
│   ├── algorithmic-art/
│   ├── artifacts-builder/
│   ├── document-skills/
│   ├── mcp-builder/
│   ├── skill-creator/
│   ├── slack-gif-creator/
│   ├── template-skill/
│   └── webapp-testing/
├── tools/
│   ├── base.py           # 工具基类
│   ├── bash_tool.py      # Bash 工具
│   ├── file_tools.py     # 文件工具
│   ├── note_tool.py      # 笔记工具
│   ├── skill_tool.py    # Skill 工具
│   ├── mcp_loader.py     # MCP 加载器
│   └── skill_loader.py   # Skill 加载器
└── utils/
    └── terminal_utils.py
```
