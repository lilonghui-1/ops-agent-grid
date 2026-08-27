"""事件模型

存储告警触发后产生的事件记录，包含事件全生命周期信息：
标题、描述、关联告警规则、严重级别、状态、来源、
服务器与服务信息、根因分析报告、时间线等。

字段：
- id: 主键自增
- title: 事件标题
- description: 事件描述
- alert_rule_id: 关联告警规则 ID（可空）
- severity: 严重级别（info / warning / critical）
- status: 事件状态（open / investigating / resolved / closed）
- source: 事件来源（manual / webhook / scheduled）
- server_host: 服务器地址
- service_name: 服务名称
- rca_report: 根因分析报告（Text，可空）
- timeline: 事件时间线（JSON 文本，事件列表）
- created_by: 创建人
- created_at: 创建时间
- updated_at: 更新时间
- resolved_at: 解决时间
- resolved_by: 解决人
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Incident(Base):
    """事件表"""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 关联告警规则（可空，手动创建的事件无关联规则）
    alert_rule_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("alert_rules.id"), nullable=True, index=True
    )

    # 严重级别：info / warning / critical
    severity: Mapped[str] = mapped_column(
        String(32), nullable=False, default="warning", index=True
    )

    # 事件状态：open / investigating / resolved / closed
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open", index=True
    )

    # 事件来源：manual / webhook / scheduled
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual"
    )

    # 关联的服务器与服务
    server_host: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    service_name: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )

    # 根因分析报告（RCA）
    rca_report: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 事件时间线（JSON 文本，记录事件各阶段信息）
    timeline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 创建与维护信息
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Incident(id={self.id}, title={self.title!r}, "
            f"severity={self.severity!r}, status={self.status!r})>"
        )
