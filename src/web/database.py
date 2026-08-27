"""数据库层 - SQLAlchemy 2.0 引擎与会话工厂

提供：
- engine / SessionLocal：SQLite 引擎与会话工厂（WAL 模式）
- Base：声明式基类，所有 ORM 模型继承自此
- get_db()：FastAPI 依赖注入用的数据库会话生成器
- init_database()：创建所有表
"""

from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# 数据库文件配置
# ---------------------------------------------------------------------------
DATABASE_DIR = Path("/workspace/ops-agent/data")
DATABASE_PATH = DATABASE_DIR / "ops_agent_web.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# 确保数据目录存在
DATABASE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 引擎与会话工厂
# ---------------------------------------------------------------------------
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

# 每次新建底层连接时启用 WAL 模式，提升 SQLite 并发读写性能


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
    """为 SQLite 连接设置 PRAGMA（WAL 模式等）。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)

# 声明式基类，所有 ORM 模型继承自 Base
Base = declarative_base()


# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """获取数据库会话的生成器，用于 FastAPI 依赖注入。

    用法::

        from fastapi import Depends
        from src.web.database import get_db

        @app.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------
def init_database() -> None:
    """创建所有表。

    会先导入所有模型，确保它们注册到 Base.metadata，再调用 create_all()。
    """
    # 延迟导入以避免循环导入
    from .models import (  # noqa: F401
        Alert,
        AlertRule,
        Approval,
        AuditLog,
        ChatHistory,
        ConfigBackup,
        CustomConfig,
        Incident,
        ServerMetric,
        TopologyEdge,
        TopologyNode,
        User,
    )

    Base.metadata.create_all(bind=engine)
