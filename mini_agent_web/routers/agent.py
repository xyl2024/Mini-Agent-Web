"""Agent 路由 - 处理聊天和 WebSocket 连接。"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mini_agent_web.agent import WebAgentCallbacks
from mini_agent_web.agent_manager import agent_manager
from mini_agent_web.connection_manager import manager
from mini_agent_web.database import Message, Session as DBSession, get_session_factory, init_db


# 日志工具
def log(level: str, msg: str):
    """打印日志。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def log_request(msg: str):
    log("REQUEST", msg)


def log_response(msg: str):
    log("RESPONSE", msg)


def log_info(msg: str):
    log("INFO", msg)


def log_error(msg: str):
    log("ERROR", msg)


def log_websocket(msg: str):
    log("WEBSOCKET", msg)

# 创建路由
router = APIRouter(prefix="/agent", tags=["agent"])


def get_db():
    """获取数据库会话。"""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 请求/响应模型
class ChatRequest(BaseModel):
    """聊天请求模型。"""

    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """聊天响应模型。"""

    status: str
    session_id: str


class MessageResponse(BaseModel):
    """消息响应模型。"""

    id: int
    session_id: str
    role: str
    content: str
    thinking: Optional[str] = None
    tool_calls: Optional[list] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    created_at: Any


class HistoryResponse(BaseModel):
    """历史消息响应模型。"""

    messages: list[MessageResponse]


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
):
    """处理聊天消息。

    Args:
        request: 聊天请求
        db: 数据库会话

    Returns:
        聊天响应
    """
    log_request(f"收到聊天请求: session_id={request.session_id}, message={request.message[:50]}...")

    # 初始化数据库（确保表存在）
    init_db()
    log_info("数据库已初始化")

    # 获取或创建会话 ID
    session_id = request.session_id
    if not session_id:
        session_id = str(uuid.uuid4())
        log_info(f"创建新会话: {session_id}")

    # 获取或创建数据库会话记录
    db_session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not db_session:
        # 使用用户工作目录或临时目录
        workspace_dir = Path.cwd() / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        db_session = DBSession(
            id=session_id,
            workspace_dir=str(workspace_dir),
        )
        db.add(db_session)
        db.commit()
        log_info(f"创建数据库会话记录: workspace={workspace_dir}")

    # 保存用户消息到数据库
    user_message = Message(
        session_id=session_id,
        role="user",
        content=request.message,
    )
    db.add(user_message)
    db.commit()
    log_info(f"用户消息已保存: id={user_message.id}")

    log_response(f"返回响应: session_id={session_id}, status=processing")

    # 在后台启动 Agent 运行（不阻塞响应）
    asyncio.create_task(
        run_agent_background(
            session_id=session_id,
            message=request.message,
            workspace_dir=db_session.workspace_dir,
        )
    )

    return ChatResponse(status="processing", session_id=session_id)


async def run_agent_background(
    session_id: str,
    message: str,
    workspace_dir: str,
):
    """在后台运行 Agent。

    Args:
        session_id: 会话 ID
        message: 用户消息
        workspace_dir: 工作空间目录
    """
    # 获取 Agent 锁，防止同一 session 并发执行
    agent_lock = agent_manager.get_lock(session_id)
    log_info(f"后台任务获取 Agent 锁: session_id={session_id}")

    async with agent_lock:
        # 获取或创建 Agent 实例（单例）
        callbacks = WebAgentCallbacks(session_id, manager)

        log_info(f"后台任务获取 Agent 实例: session_id={session_id}, workspace={workspace_dir}")
        agent = await agent_manager.get_agent(
            session_id=session_id,
            workspace_dir=Path(workspace_dir),
            callbacks=callbacks,
        )
        log_info(f"后台任务 Agent 实例准备就绪: session_id={session_id}, messages_count={len(agent.messages)}")

        # 添加用户消息
        agent.add_user_message(message)
        log_info(f"后台任务用户消息已添加到 Agent: session_id={session_id}")

        # 运行 Agent
        try:
            log_info(f"后台任务开始运行 Agent: session_id={session_id}")
            result = await agent.run()
            log_info(f"后台任务 Agent 运行完成: session_id={session_id}, result={result[:100] if result else 'empty'}...")

            # 保存助手消息到数据库
            log_info(f"后台任务保存助手消息到数据库: session_id={session_id}")
            SessionLocal = get_session_factory()
            db = SessionLocal()
            try:
                history = agent.get_history()
                saved_count = 0
                for msg in history:
                    if msg.role == "assistant":
                        # 检查是否已存在（避免重复保存）
                        existing = db.query(Message).filter(
                            Message.session_id == session_id,
                            Message.role == "assistant",
                            Message.content == msg.content,
                        ).first()
                        if not existing:
                            db_msg = Message(
                                session_id=session_id,
                                role=msg.role,
                                content=msg.content or "",
                                thinking=msg.thinking,
                                tool_calls=[tc.model_dump() for tc in msg.tool_calls] if msg.tool_calls else None,
                            )
                            db.add(db_msg)
                            saved_count += 1
                    elif msg.role == "tool":
                        # 检查是否已存在
                        existing = db.query(Message).filter(
                            Message.session_id == session_id,
                            Message.role == "tool",
                            Message.tool_call_id == msg.tool_call_id,
                        ).first()
                        if not existing:
                            db_msg = Message(
                                session_id=session_id,
                                role=msg.role,
                                content=msg.content or "",
                                tool_call_id=msg.tool_call_id,
                                name=msg.name,
                            )
                            db.add(db_msg)
                            saved_count += 1

                db.commit()
                log_info(f"后台任务助手消息已保存: session_id={session_id}, count={saved_count}")
            finally:
                db.close()

        except Exception as e:
            log_error(f"后台任务 Agent 运行失败: session_id={session_id}, error={str(e)}")
            await callbacks.on_error(f"执行失败: {str(e)}")


@router.get("/chat", response_model=HistoryResponse)
async def get_chat_history(
    session_id: str = Query(..., description="会话 ID"),
    db: Session = Depends(get_db),
):
    """获取聊天历史。

    Args:
        session_id: 会话 ID
        db: 数据库会话

    Returns:
        历史消息
    """
    log_request(f"获取聊天历史: session_id={session_id}")

    # 初始化数据库
    init_db()

    # 查询消息
    messages = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at)
        .all()
    )

    log_response(f"返回 {len(messages)} 条消息: session_id={session_id}")
    return HistoryResponse(
        messages=[
            MessageResponse(
                id=msg.id,
                session_id=msg.session_id,
                role=msg.role,
                content=msg.content,
                thinking=msg.thinking,
                tool_calls=msg.tool_calls,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
                created_at=msg.created_at.isoformat(),
            )
            for msg in messages
        ]
    )


@router.delete("/session/{session_id}")
async def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
):
    """删除会话。

    Args:
        session_id: 会话 ID
        db: 数据库会话

    Returns:
        删除结果
    """
    log_request(f"删除会话: session_id={session_id}")

    # 移除 Agent 实例
    agent_manager.remove_agent(session_id)
    log_info(f"Agent 实例已移除: session_id={session_id}")

    # 删除数据库中的消息
    db.query(Message).filter(Message.session_id == session_id).delete()
    db.query(DBSession).filter(DBSession.id == session_id).delete()
    db.commit()
    log_info(f"数据库记录已删除: session_id={session_id}")

    log_response(f"会话已删除: session_id={session_id}")
    return {"status": "deleted", "session_id": session_id}


@router.get("/sessions")
async def list_sessions():
    """列出所有活跃会话。

    Returns:
        活跃会话列表
    """
    sessions = agent_manager.get_active_sessions()
    log_request(f"列出活跃会话: {len(sessions)} 个")
    return {"sessions": sessions}


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str = Query(...),
):
    """WebSocket 端点，用于实时推送消息。

    Args:
        websocket: WebSocket 连接
        session_id: 会话 ID
    """
    # 设置 WebSocket 超时为 10 分钟（600 秒），适应长时间 LLM 调用
    websocket.timeout = 600

    log_websocket(f"WebSocket 连接建立: session_id={session_id}, timeout={websocket.timeout}s")

    # 初始化数据库
    init_db()
    log_websocket(f"数据库已初始化: session_id={session_id}")

    # 连接
    await manager.connect(websocket, session_id)
    log_websocket(f"WebSocket 已连接: session_id={session_id}, connections={len(manager.active_connections.get(session_id, []))}")

    try:
        # 保持连接并处理消息
        while True:
            # 等待客户端消息（目前主要用于心跳）
            data = await websocket.receive_text()
            log_websocket(f"收到 WebSocket 消息: session_id={session_id}, data={data[:50]}...")

    except WebSocketDisconnect:
        log_websocket(f"WebSocket 断开连接: session_id={session_id}")
    finally:
        manager.disconnect(websocket, session_id)
        log_websocket(f"WebSocket 已断开: session_id={session_id}")
