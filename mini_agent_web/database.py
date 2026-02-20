"""SQLite 数据库初始化和模型定义。"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import DateTime, Index, JSON, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy 声明基类。"""


class Session(Base):
    """会话表模型。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_dir: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Message(Base):
    """消息表模型。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    thinking: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_messages_session_created", "session_id", "created_at"),
    )


# 全局引擎和会话工厂
_engine = None
_SessionLocal = None


def get_engine():
    """获取或创建数据库引擎。"""
    global _engine
    if _engine is None:
        # 默认使用项目目录下的 agent_data 目录
        db_path = Path(__file__).parent.parent / "agent_data" / "mini_agent.db"

        # 确保目录存在
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # 创建引擎
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

    return _engine


def get_session_factory():
    """获取会话工厂。"""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    return _SessionLocal


def init_db():
    """初始化数据库表。"""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
