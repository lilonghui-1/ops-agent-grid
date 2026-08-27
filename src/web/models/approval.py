"""审批模型 - 工具执行前的人工审批流程

当工具标记 requires_approval=True 时，执行前需先创建审批请求，
由管理员审核通过后方可执行。
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Approval(Base):
    """审批请求表

    字段：
    - id: 主键自增
    - title: 审批标题
    - description: 审批描述
    - tool_name: 需要审批的工具名称，索引
    - tool_params: 工具请求参数（JSON 文本）
    - risk_level: 风险等级（low / medium / high / critical）
    - status: 审批状态（pending / approved / rejected / expired），索引
    - requested_by: 请求人用户名，索引
    - requested_at: 请求时间
    - reviewed_by: 审核人用户名（可为空）
    - reviewed_at: 审核时间（可为空）
    - review_comment: 审核意见（可为空）
    - expires_at: 过期时间（创建后自动设为 24 小时后）
    - impact_analysis: 影响分析（执行后会发生什么）
    - rollback_plan: 回滚计划（如何撤销操作）
    """

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tool_params: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="medium"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    impact_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rollback_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Approval(id={self.id}, title={self.title!r}, "
            f"tool_name={self.tool_name!r}, status={self.status!r}, "
            f"risk_level={self.risk_level!r})>"
        )
