# Agent 模块重构设计方案

## 当前问题分析

### 1. 现有架构

```
┌─────────────────────────────────────────────────────────────┐
│                         Agent                                │
├─────────────────────────────────────────────────────────────┤
│  __init__()     │ 初始化 LLM、工具、日志、消息历史           │
│  run()          │ 主循环：LLM 调用 → 工具执行 → 结果处理     │
│  add_user_msg() │ 添加用户消息                                │
│  get_history() │ 获取消息历史                                │
│  _summarize()   │ 消息摘要                                    │
│  _cleanup()    │ 清理不完整消息                               │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ 紧耦合问题                                                    │
├─────────────────────────────────────────────────────────────┤
│ • print() 直接输出终端内容，嵌入 ANSI 颜色码                 │
│ • AgentLogger 硬编码初始化                                   │
│ • 工具执行逻辑与主循环紧耦合                                  │
│ • 消息摘要逻辑嵌入主循环                                      │
│ • 取消机制与终端输入紧耦合                                    │
│ • 步骤进度、耗时统计直接打印                                  │
└─────────────────────────────────────────────────────────────┘
```

### 2. 具体耦合点

| 位置 | 问题 | 影响 |
|------|------|------|
| `agent.py:19-42` | `Colors` 类硬编码终端颜色 | 无法迁移| `agent.py到 Web |
:79` | `AgentLogger()` 硬编码 | 日志方式不可配置 |
| `agent.py:206-209` | `print()` 输出 token 统计 | 无法捕获用于 Web UI |
| `agent.py:336-337` | `print()` 输出日志文件路径 | Web 无需显示 |
| `agent.py:356-362` | `print()` 输出步骤头部 | 需要替换为回调 |
| `agent.py:407-413` | `print()` 输出思考/响应 | 需要替换为回调 |
| `agent.py:436-450` | `print()` 输出工具调用 | 需要替换为回调 |
| `agent.py:485-491` | `print()` 输出工具结果 | 需要替换为回调 |
| `agent.py:419,509-511` | `print()` 输出耗时统计 | 需要替换为回调 |

## 重构目标

1. **UI 解耦**: 将所有终端输出替换为事件回调/抽象接口
2. **日志解耦**: 日志组件可配置，支持多种输出（文件/Web）
3. **状态管理解耦**: 将执行状态（步骤、耗时）转换为状态流
4. **可扩展性**: 便于扩展为 WebSocket/SSE 等实时应用

## 重构设计方案

### 方案：事件驱动 + 状态流

```
┌────────────────────────────────────────────────────────────────────┐
│                        重构后的架构                                 │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐     ┌─────────────────┐     ┌────────────────┐  │
│  │   Callback   │     │    AgentCore    │     │    StateMgr    │  │
│  │  Handlers    │◄───►│   (纯业务逻辑)   │◄───►│   (状态流)      │  │
│  └──────────────┘     └─────────────────┘     └────────────────┘  │
│          │                    │                       │              │
│          │              ┌─────▼─────┐               │              │
│          │              │  LLMClient │               │              │
│          │              └───────────┘               │              │
│          │              ┌───────────┐               │              │
│          │              │   Tools   │               │              │
│          │              └───────────┘               │              │
│          │                                               │              │
│          ▼                                               ▼              │
│  ┌─────────────────────────────────────────────────────────────────┤
│  │                     实现层 (可插拔)                               │
│  ├─────────────────────────────────────────────────────────────────┤
│  │                                                                 │
│  │  TerminalHandler          WebHandler           CustomHandler   │
│  │  (print + colors)         (WebSocket/SSE)       (用户自定义)     │
│  │                                                                 │
│  └─────────────────────────────────────────────────────────────────┘
```

### 核心设计

#### 1. 事件回调接口

```python
# agent/events.py

from dataclasses import dataclass
from typing import Protocol, Optional, Any
from enum import Enum

class EventType(Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    STEP_START = "step_start"
    STEP_END = "step_end"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOKEN_USAGE = "token_usage"
    SUMMARIZE = "summarize"
    ERROR = "error"
    CANCELLED = "cancelled"

@dataclass
class AgentEvent:
    type: EventType
    data: dict[str, Any]
    timestamp: float

class EventHandler(Protocol):
    """事件处理器接口"""
    
    def on_event(self, event: AgentEvent) -> None:
        """处理事件"""
        ...
    
    async def on_event_async(self, event: AgentEvent) -> None:
        """异步处理事件"""
        ...
```

#### 2. Agent 核心（纯业务逻辑）

```python
# agent/core.py

class AgentCore:
    """Agent 核心逻辑，与 UI 完全解耦"""
    
    def __init__(
        self,
        llm_client: LLMClient,
        tools: list[Tool],
        event_handler: Optional[EventHandler] = None,
    ):
        self.llm = llm_client
        self.tools = {tool.name: tool for tool in tools}
        self.event_handler = event_handler or DefaultEventHandler()
        self.messages: list[Message] = []
        self._cancelled = False
    
    async def run(
        self,
        system_prompt: str,
        user_message: str,
        max_steps: int = 50,
    ) -> str:
        """执行 Agent，返回最终响应"""
        self.messages = [Message(role="system", content=system_prompt)]
        self.messages.append(Message(role="user", content=user_message))
        
        self._emit(EventType.RUN_START, {"max_steps": max_steps})
        
        step = 0
        while step < max_steps:
            if self._cancelled:
                self._emit(EventType.CANCELLED, {})
                return "任务已取消"
            
            self._emit(EventType.STEP_START, {"step": step + 1, "max_steps": max_steps})
            
            # LLM 调用
            response = await self._call_llm()
            
            # 工具执行
            if response.tool_calls:
                for tool_call in response.tool_calls:
                    await self._execute_tool(tool_call)
            else:
                self._emit(EventType.RUN_END, {"content": response.content})
                return response.content
            
            self._emit(EventType.STEP_END, {"step": step + 1})
            step += 1
        
        return f"任务在 {max_steps} 步后无法完成"
    
    def _emit(self, event_type: EventType, data: dict) -> None:
        event = AgentEvent(type=event_type, data=data, timestamp=time.time())
        self.event_handler.on_event(event)
    
    def cancel(self) -> None:
        """取消执行"""
        self._cancelled = True
```

#### 3. 状态管理器

```python
# agent/state.py

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

@dataclass
class AgentState:
    """Agent 执行状态"""
    status: str = "idle"  # idle, running, completed, cancelled, error
    current_step: int = 0
    max_steps: int = 0
    total_tokens: int = 0
    start_time: Optional[datetime] = None
    step_start_time: Optional[datetime] = None
    messages: list[Message] = field(default_factory=list)
    last_response: Optional[str] = None
    last_error: Optional[str] = None

class StateManager:
    """状态管理器，支持多观察者"""
    
    def __init__(self):
        self._state = AgentState()
        self._observers: list[Callable[[AgentState], None]] = []
    
    @property
    def state(self) -> AgentState:
        return self._state
    
    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self._state, key):
                setattr(self._state, key, value)
        self._notify()
    
    def subscribe(self, observer: Callable[[AgentState], None]) -> None:
        self._observers.append(observer)
    
    def _notify(self) -> None:
        for observer in self._observers:
            observer(self._state)
```

#### 4. 终端实现（可插拔）

```python
# agent/handlers/terminal.py

class TerminalEventHandler:
    """终端事件处理器"""
    
    def on_event(self, event: AgentEvent) -> None:
        handler = {
            EventType.STEP_START: self._on_step_start,
            EventType.STEP_END: self._on_step_end,
            EventType.LLM_RESPONSE: self._on_llm_response,
            EventType.TOOL_CALL: self._on_tool_call,
            EventType.TOOL_RESULT: self._on_tool_result,
            EventType.RUN_END: self._on_run_end,
        }.get(event.type)
        
        if handler:
            handler(event)
    
    def _on_step_start(self, event: AgentEvent) -> None:
        step = event.data["step"]
        max_steps = event.data["max_steps"]
        print(f"\n💭 步骤 {step}/{max_steps}")
    
    def _on_tool_call(self, event: AgentEvent) -> None:
        tool_name = event.data["tool_name"]
        arguments = event.data["arguments"]
        print(f"\n🔧 工具调用: {tool_name}")
        print(f"   参数: {json.dumps(arguments, indent=2, ensure_ascii=False)}")
    
    # ... 其他处理方法
```

#### 5. Web 实现示例（可插拔）

```python
# agent/handlers/websocket.py

class WebSocketEventHandler:
    """WebSocket 事件处理器"""
    
    def __init__(self, websocket):
        self.websocket = websocket
    
    async def on_event_async(self, event: AgentEvent) -> None:
        message = {
            "type": event.type.value,
            "data": event.data,
            "timestamp": event.timestamp,
        }
        await self.websocket.send_json(message)
```

### 文件重构计划

```
mini_agent/
├── agent/
│   ├── __init__.py
│   ├── core.py              # AgentCore（核心逻辑）
│   ├── events.py            # 事件定义和接口
│   ├── state.py             # 状态管理
│   ├── history.py           # 消息历史管理
│   ├── summarizer.py        # 消息摘要（独立模块）
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── base.py          # 基础处理器
│   │   ├── terminal.py      # 终端处理器
│   │   └── callback.py      # 回调处理器
│   └── agent.py             # 向后兼容（内部使用 core）
│
├── cli.py                    # 更新为使用新架构
└── web/
    └── app.py               # Web 应用入口（新增）
```

### 向后兼容性

```python
# agent/agent.py（向后兼容）

class Agent(AgentCore):
    """保留原有接口，向后兼容"""
    
    def __init__(self, *args, **kwargs):
        # 自动创建终端处理器
        terminal_handler = TerminalEventHandler()
        super().__init__(
            event_handler=terminal_handler,
            *args,
            **kwargs,
        )
        
        # 原有初始化逻辑
        self.max_steps = kwargs.get("max_steps", 50)
        ...
```

## 使用示例

### CLI 模式（终端）

```python
from agent.core import AgentCore
from agent.handlers.terminal import TerminalEventHandler
from agent.state import StateManager, print_state

agent = AgentCore(
    llm_client=llm,
    tools=tools,
    event_handler=TerminalEventHandler(),
)

state_mgr = StateManager()
state_mgr.subscribe(print_state)  # 订阅状态变化

result = await agent.run(system_prompt, user_message)
```

### Web 模式

```python
from agent.core import AgentCore
from agent.handlers.websocket import WebSocketEventHandler
from agent.state import StateManager

async def handle_websocket(websocket):
    handler = WebSocketEventHandler(websocket)
    state_mgr = StateManager()
    
    # 广播状态到 WebSocket
    async def broadcast_state(state):
        await websocket.send_json({"type": "state", "data": asdict(state)})
    state_mgr.subscribe(broadcast_state)
    
    agent = AgentCore(
        llm_client=llm,
        tools=tools,
        event_handler=handler,
        state_manager=state_mgr,
    )
    
    result = await agent.run(system_prompt, user_message)
```

## 总结

| 改进点 | 效果 |
|--------|------|
| 事件驱动架构 | UI 与业务逻辑完全解耦 |
| 状态流管理 | 便于实时展示进度 |
| 可插拔处理器 | 终端/Web/WebSocket 轻松切换 |
| 向后兼容 | 现有代码无需大改 |
| 模块化拆分 | 便于测试和维护 |
