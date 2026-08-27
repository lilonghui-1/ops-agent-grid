"""告警规则与事件相关 Pydantic 模型

包含告警规则的创建/更新/查询模型，以及事件的
查询/确认/解决/静音请求与响应模型。
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 告警规则
# ---------------------------------------------------------------------------
class AlertRuleBase(BaseModel):
    """告警规则基础信息"""

    name: str = Field(..., description="规则名称")
    description: Optional[str] = Field(None, description="规则描述")
    rule_type: str = Field(
        ..., description="规则类型: threshold / log_keyword / promql"
    )
    metric_name: Optional[str] = Field(None, description="指标名称（threshold 类型）")
    operator: Optional[str] = Field(
        None, description="比较运算符: > / < / >= / <="
    )
    threshold_value: Optional[float] = Field(None, description="阈值（threshold 类型）")
    log_query: Optional[str] = Field(
        None, description="日志查询关键词（log_keyword 类型）"
    )
    promql_expr: Optional[str] = Field(
        None, description="PromQL 表达式（promql 类型）"
    )
    severity: str = Field("warning", description="严重级别: info / warning / critical")
    enabled: bool = Field(True, description="是否启用")


class AlertRuleCreate(AlertRuleBase):
    """创建告警规则请求"""


class AlertRuleUpdate(BaseModel):
    """更新告警规则请求（所有字段可选）"""

    name: Optional[str] = Field(None, description="规则名称")
    description: Optional[str] = Field(None, description="规则描述")
    rule_type: Optional[str] = Field(None, description="规则类型")
    metric_name: Optional[str] = Field(None, description="指标名称")
    operator: Optional[str] = Field(None, description="比较运算符")
    threshold_value: Optional[float] = Field(None, description="阈值")
    log_query: Optional[str] = Field(None, description="日志查询关键词")
    promql_expr: Optional[str] = Field(None, description="PromQL 表达式")
    severity: Optional[str] = Field(None, description="严重级别")
    enabled: Optional[bool] = Field(None, description="是否启用")


class AlertRuleInfo(AlertRuleBase):
    """告警规则详情（含数据库字段）"""

    id: int = Field(..., description="规则 ID")
    created_by: Optional[str] = Field(None, description="创建人")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    last_evaluated_at: Optional[datetime] = Field(
        None, description="最近评估时间"
    )
    last_fired_at: Optional[datetime] = Field(None, description="最近触发时间")

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------
class IncidentBase(BaseModel):
    """事件基础信息"""

    title: str = Field(..., description="事件标题")
    description: Optional[str] = Field(None, description="事件描述")
    severity: str = Field("warning", description="严重级别: info / warning / critical")
    source: str = Field("manual", description="事件来源: manual / webhook / scheduled")
    server_host: Optional[str] = Field(None, description="服务器地址")
    service_name: Optional[str] = Field(None, description="服务名称")


class IncidentCreate(IncidentBase):
    """创建事件请求"""

    alert_rule_id: Optional[int] = Field(None, description="关联告警规则 ID")


class IncidentInfo(IncidentBase):
    """事件详情（含数据库字段）"""

    id: int = Field(..., description="事件 ID")
    alert_rule_id: Optional[int] = Field(None, description="关联告警规则 ID")
    status: str = Field(..., description="事件状态: open / investigating / resolved / closed")
    rca_report: Optional[str] = Field(None, description="根因分析报告")
    timeline: Optional[str] = Field(None, description="事件时间线（JSON 文本）")
    created_by: Optional[str] = Field(None, description="创建人")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    resolved_at: Optional[datetime] = Field(None, description="解决时间")
    resolved_by: Optional[str] = Field(None, description="解决人")

    class Config:
        from_attributes = True


class IncidentPage(BaseModel):
    """事件分页响应"""

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页条数")
    items: List[IncidentInfo] = Field(default_factory=list, description="事件列表")


class IncidentAcknowledge(BaseModel):
    """确认事件请求"""

    note: Optional[str] = Field(None, description="备注信息")


class IncidentResolve(BaseModel):
    """解决事件请求"""

    note: Optional[str] = Field(None, description="解决备注")
    rca_report: Optional[str] = Field(None, description="根因分析报告")


class IncidentSilence(BaseModel):
    """静音事件请求"""

    duration_minutes: int = Field(60, description="静音时长（分钟）")
    note: Optional[str] = Field(None, description="静音备注")


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------
class AlertWebhookPayload(BaseModel):
    """外部告警 Webhook 载荷（通用格式）"""

    alertname: Optional[str] = Field(None, description="告警名称")
    severity: Optional[str] = Field(None, description="严重级别")
    message: Optional[str] = Field(None, description="告警消息")
    summary: Optional[str] = Field(None, description="告警摘要")
    description: Optional[str] = Field(None, description="告警描述")
    status: Optional[str] = Field(None, description="告警状态: firing / resolved")
    starts_at: Optional[str] = Field(None, description="告警开始时间")
    ends_at: Optional[str] = Field(None, description="告警结束时间")
    instance: Optional[str] = Field(None, description="实例地址")
    labels: Optional[dict] = Field(default_factory=dict, description="告警标签")
    annotations: Optional[dict] = Field(
        default_factory=dict, description="告警注解"
    )
    source: Optional[str] = Field(
        None, description="告警来源系统: alertmanager / grafana / 其他"
    )


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
class AlertStats(BaseModel):
    """告警统计信息"""

    total_rules: int = Field(0, description="告警规则总数")
    enabled_rules: int = Field(0, description="启用的规则数")
    total_incidents: int = Field(0, description="事件总数")
    open_incidents: int = Field(0, description="未解决事件数")
    investigating_incidents: int = Field(0, description="调查中事件数")
    resolved_incidents: int = Field(0, description="已解决事件数")
    critical_incidents: int = Field(0, description="严重事件数")
    warning_incidents: int = Field(0, description="警告事件数")
    recent_fired_rules: int = Field(0, description="近期触发的规则数")
