"""告警规则模型

存储告警规则定义，支持三种规则类型：
- threshold: 阈值规则（指标值与阈值比较）
- log_keyword: 日志关键词规则（日志内容包含指定关键词）
- promql: Prometheus 查询规则（执行 PromQL 表达式）

字段：
- id: 主键自增
- name: 规则名称
- description: 规则描述
- rule_type: 规则类型（threshold / log_keyword / promql）
- metric_name: 指标名称（threshold 类型使用）
- operator: 比较运算符（> / < / >= / <=）
- threshold_value: 阈值（threshold 类型使用）
- log_query: 日志查询关键词（log_keyword 类型使用）
- promql_expr: PromQL 表达式（promql 类型使用）
- severity: 严重级别（info / warning / critical）
- enabled: 是否启用
- created_by: 创建人
- created_at: 创建时间
- updated_at: 更新时间
- last_evaluated_at: 最近评估时间
- last_fired_at: 最近触发时间
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class AlertRule(Base):
    """告警规则表"""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 规则类型：threshold / log_keyword / promql
    rule_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )

    # threshold 规则相关字段
    metric_name: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    # 比较运算符：> / < / >= / <=
    operator: Mapped[Optional[str]] = mapped_column(
        String(8), nullable=True, default=">"
    )
    threshold_value: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )

    # log_keyword 规则相关字段
    log_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # promql 规则相关字段
    promql_expr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 严重级别：info / warning / critical
    severity: Mapped[str] = mapped_column(
        String(32), nullable=False, default="warning", index=True
    )

    # 是否启用
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # 创建与维护信息
    created_by: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # 评估与触发时间
    last_evaluated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    last_fired_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<AlertRule(id={self.id}, name={self.name!r}, "
            f"rule_type={self.rule_type!r}, severity={self.severity!r}, "
            f"enabled={self.enabled})>"
        )
