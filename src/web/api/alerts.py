"""告警管理路由 - 告警规则与事件的增删改查、Webhook 接收

端点：
- GET    /rules                     : 查询告警规则列表
- POST   /rules                     : 创建告警规则（管理员）
- PUT    /rules/{rule_id}           : 更新告警规则（管理员）
- DELETE /rules/{rule_id}           : 删除告警规则（管理员）
- GET    /incidents                 : 分页查询事件列表
- GET    /incidents/{incident_id}   : 查询事件详情
- POST   /incidents/{incident_id}/acknowledge : 确认事件
- POST   /incidents/{incident_id}/resolve      : 解决事件
- POST   /incidents/{incident_id}/silence      : 静音事件
- POST   /webhook                   : 接收外部告警 Webhook（使用 token 鉴权）
- GET    /stats                     : 告警统计摘要
"""

import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..core.deps import get_current_active_user, require_admin
from ..database import get_db
from ..models.alert_rule import AlertRule
from ..models.incident import Incident
from ..models.user import User
from ..schemas.alert import (
    AlertRuleCreate,
    AlertRuleInfo,
    AlertRuleUpdate,
    AlertStats,
    AlertWebhookPayload,
    IncidentAcknowledge,
    IncidentInfo,
    IncidentPage,
    IncidentResolve,
    IncidentSilence,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["告警管理"])

# Webhook 鉴权 token（生产环境应通过配置注入）
WEBHOOK_TOKEN = "ops-agent-webhook-token"


# ---------------------------------------------------------------------------
# 告警规则管理
# ---------------------------------------------------------------------------
@router.get("/rules", response_model=List[AlertRuleInfo], summary="查询告警规则列表")
def list_alert_rules(
    rule_type: Optional[str] = Query(None, description="按规则类型筛选: threshold/log_keyword/promql"),
    severity: Optional[str] = Query(None, description="按严重级别筛选: info/warning/critical"),
    enabled: Optional[bool] = Query(None, description="按启用状态筛选"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """查询告警规则列表，支持按类型、严重级别、启用状态筛选。

    Args:
        rule_type: 规则类型筛选
        severity: 严重级别筛选
        enabled: 启用状态筛选
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        AlertRuleInfo 列表
    """
    query = db.query(AlertRule)

    if rule_type:
        query = query.filter(AlertRule.rule_type == rule_type)
    if severity:
        query = query.filter(AlertRule.severity == severity)
    if enabled is not None:
        query = query.filter(AlertRule.enabled == enabled)

    rules = query.order_by(AlertRule.created_at.desc()).all()
    return [AlertRuleInfo.model_validate(r) for r in rules]


@router.post("/rules", response_model=AlertRuleInfo, summary="创建告警规则")
def create_alert_rule(
    request: AlertRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """创建告警规则（需要管理员权限）。

    Args:
        request: 规则创建请求
        db: 数据库会话
        current_user: 当前登录用户（管理员）

    Returns:
        AlertRuleInfo: 创建的规则信息
    """
    # 校验规则类型
    valid_types = ("threshold", "log_keyword", "promql")
    if request.rule_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的规则类型: {request.rule_type}，允许: {', '.join(valid_types)}",
        )

    # 校验严重级别
    valid_severities = ("info", "warning", "critical")
    if request.severity not in valid_severities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的严重级别: {request.severity}，允许: {', '.join(valid_severities)}",
        )

    # 校验规则参数完整性
    _validate_rule_params(request)

    rule = AlertRule(
        name=request.name,
        description=request.description,
        rule_type=request.rule_type,
        metric_name=request.metric_name,
        operator=request.operator,
        threshold_value=request.threshold_value,
        log_query=request.log_query,
        promql_expr=request.promql_expr,
        severity=request.severity,
        enabled=request.enabled,
        created_by=current_user.username,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)

    logger.info(
        f"用户 {current_user.username} 创建告警规则 id={rule.id} name={rule.name}"
    )
    return AlertRuleInfo.model_validate(rule)


@router.put("/rules/{rule_id}", response_model=AlertRuleInfo, summary="更新告警规则")
def update_alert_rule(
    rule_id: int,
    request: AlertRuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新告警规则（需要管理员权限）。

    Args:
        rule_id: 规则 ID
        request: 规则更新请求（字段可选）
        db: 数据库会话
        current_user: 当前登录用户（管理员）

    Returns:
        AlertRuleInfo: 更新后的规则信息
    """
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"告警规则不存在: id={rule_id}",
        )

    # 逐字段更新（仅更新提供的字段）
    update_data = request.model_dump(exclude_unset=True)

    # 校验规则类型
    if "rule_type" in update_data and update_data["rule_type"]:
        valid_types = ("threshold", "log_keyword", "promql")
        if update_data["rule_type"] not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不支持的规则类型: {update_data['rule_type']}",
            )

    # 校验严重级别
    if "severity" in update_data and update_data["severity"]:
        valid_severities = ("info", "warning", "critical")
        if update_data["severity"] not in valid_severities:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不支持的严重级别: {update_data['severity']}",
            )

    for field, value in update_data.items():
        setattr(rule, field, value)

    db.commit()
    db.refresh(rule)

    logger.info(
        f"用户 {current_user.username} 更新告警规则 id={rule_id}"
    )
    return AlertRuleInfo.model_validate(rule)


@router.delete("/rules/{rule_id}", summary="删除告警规则")
def delete_alert_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """删除告警规则（需要管理员权限）。

    Args:
        rule_id: 规则 ID
        db: 数据库会话
        current_user: 当前登录用户（管理员）

    Returns:
        操作结果
    """
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"告警规则不存在: id={rule_id}",
        )

    rule_name = rule.name
    db.delete(rule)
    db.commit()

    logger.info(
        f"用户 {current_user.username} 删除告警规则 id={rule_id} name={rule_name}"
    )
    return {
        "success": True,
        "message": f"告警规则 {rule_name} 已删除",
        "rule_id": rule_id,
    }


# ---------------------------------------------------------------------------
# 事件管理
# ---------------------------------------------------------------------------
@router.get("/incidents", response_model=IncidentPage, summary="查询事件列表")
def list_incidents(
    status_filter: Optional[str] = Query(
        None, alias="status", description="按状态筛选: open/investigating/resolved/closed"
    ),
    severity: Optional[str] = Query(None, description="按严重级别筛选: info/warning/critical"),
    source: Optional[str] = Query(None, description="按来源筛选: manual/webhook/scheduled"),
    server_host: Optional[str] = Query(None, description="按服务器地址筛选"),
    start_time: Optional[str] = Query(None, description="起始时间（ISO 格式）"),
    end_time: Optional[str] = Query(None, description="结束时间（ISO 格式）"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """分页查询事件列表，支持多维度筛选。

    Args:
        status_filter: 事件状态筛选
        severity: 严重级别筛选
        source: 来源筛选
        server_host: 服务器地址筛选
        start_time: 起始时间
        end_time: 结束时间
        page: 页码
        page_size: 每页条数
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        IncidentPage: 分页事件列表
    """
    query = db.query(Incident)

    if status_filter:
        query = query.filter(Incident.status == status_filter)
    if severity:
        query = query.filter(Incident.severity == severity)
    if source:
        query = query.filter(Incident.source == source)
    if server_host:
        query = query.filter(Incident.server_host == server_host)

    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time)
            query = query.filter(Incident.created_at >= start_dt)
        except ValueError:
            pass
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time)
            query = query.filter(Incident.created_at <= end_dt)
        except ValueError:
            pass

    # 按创建时间倒序排列
    query = query.order_by(Incident.created_at.desc())

    total = query.count()
    offset = (page - 1) * page_size
    incidents = query.offset(offset).limit(page_size).all()

    items = [IncidentInfo.model_validate(i) for i in incidents]

    return IncidentPage(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentInfo,
    summary="查询事件详情",
)
def get_incident_detail(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """查询单个事件详情。

    Args:
        incident_id: 事件 ID
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        IncidentInfo: 事件详情
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"事件不存在: id={incident_id}",
        )
    return IncidentInfo.model_validate(incident)


@router.post(
    "/incidents/{incident_id}/acknowledge",
    response_model=IncidentInfo,
    summary="确认事件",
)
def acknowledge_incident(
    incident_id: int,
    request: IncidentAcknowledge,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """确认事件，将状态从 open 推进到 investigating。

    Args:
        incident_id: 事件 ID
        request: 确认请求（含备注）
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        IncidentInfo: 更新后的事件信息
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"事件不存在: id={incident_id}",
        )

    # 仅 open 状态可确认
    if incident.status not in ("open", "investigating"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"事件当前状态为 {incident.status}，无法确认",
        )

    incident.status = "investigating"

    # 追加时间线
    _append_timeline(
        incident,
        event=f"用户 {current_user.username} 确认事件",
        note=request.note,
    )

    db.commit()
    db.refresh(incident)

    logger.info(
        f"用户 {current_user.username} 确认事件 id={incident_id}"
    )
    return IncidentInfo.model_validate(incident)


@router.post(
    "/incidents/{incident_id}/resolve",
    response_model=IncidentInfo,
    summary="解决事件",
)
def resolve_incident(
    incident_id: int,
    request: IncidentResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """解决事件，将状态推进到 resolved 并记录解决人。

    Args:
        incident_id: 事件 ID
        request: 解决请求（含备注与可选 RCA 报告）
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        IncidentInfo: 更新后的事件信息
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"事件不存在: id={incident_id}",
        )

    if incident.status in ("resolved", "closed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"事件当前状态为 {incident.status}，无需重复解决",
        )

    incident.status = "resolved"
    incident.resolved_at = datetime.now()
    incident.resolved_by = current_user.username

    # 如提供 RCA 报告则覆盖
    if request.rca_report:
        incident.rca_report = request.rca_report

    # 追加时间线
    _append_timeline(
        incident,
        event=f"用户 {current_user.username} 解决事件",
        note=request.note,
    )

    db.commit()
    db.refresh(incident)

    logger.info(
        f"用户 {current_user.username} 解决事件 id={incident_id}"
    )
    return IncidentInfo.model_validate(incident)


@router.post(
    "/incidents/{incident_id}/silence",
    response_model=IncidentInfo,
    summary="静音事件",
)
def silence_incident(
    incident_id: int,
    request: IncidentSilence,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """静音事件，在指定时长内抑制该事件的重复告警。

    实现方式：将事件状态置为 closed，并在时间线记录静音时长与备注。

    Args:
        incident_id: 事件 ID
        request: 静音请求（含时长与备注）
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        IncidentInfo: 更新后的事件信息
    """
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"事件不存在: id={incident_id}",
        )

    silence_until = datetime.now() + timedelta(
        minutes=request.duration_minutes
    )

    # 追加时间线（包含静音到期时间）
    _append_timeline(
        incident,
        event=f"用户 {current_user.username} 静音事件 {request.duration_minutes} 分钟",
        note=request.note,
        silence_until=silence_until.isoformat(),
    )

    # 静音即将事件状态置为 closed
    incident.status = "closed"
    incident.resolved_at = datetime.now()
    incident.resolved_by = current_user.username

    db.commit()
    db.refresh(incident)

    logger.info(
        f"用户 {current_user.username} 静音事件 id={incident_id}，"
        f"时长 {request.duration_minutes} 分钟"
    )
    return IncidentInfo.model_validate(incident)


# ---------------------------------------------------------------------------
# Webhook 接收
# ---------------------------------------------------------------------------
@router.post("/webhook", summary="接收外部告警 Webhook")
async def receive_webhook(
    payload: AlertWebhookPayload,
    token: str = Query(None, description="Webhook 鉴权 token"),
):
    """接收外部告警系统的 Webhook 推送（如 Prometheus Alertmanager、Grafana）。

    该端点不需要登录认证，通过 token 参数进行鉴权。

    Args:
        payload: 告警 Webhook 载荷
        token: 鉴权 token

    Returns:
        接收处理结果
    """
    # token 鉴权
    if token != WEBHOOK_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook token 无效",
        )

    # 延迟导入以避免循环依赖
    from ...alerts.webhook_receiver import WebhookReceiver

    receiver = WebhookReceiver()
    # 将 Pydantic 模型转为字典传递
    raw_payload = payload.model_dump()
    results = receiver.receive_alert_webhook(raw_payload)

    created_count = len([r for r in results if r.get("incident_id")])
    return {
        "success": True,
        "message": f"已接收并处理外部告警，创建事件 {created_count} 个",
        "results": results,
    }


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
@router.get("/stats", response_model=AlertStats, summary="告警统计摘要")
def get_alert_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取告警统计摘要，包括规则数量、事件数量等。

    Args:
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        AlertStats: 告警统计信息
    """
    # 规则统计
    total_rules = db.query(AlertRule).count()
    enabled_rules = (
        db.query(AlertRule).filter(AlertRule.enabled.is_(True)).count()
    )

    # 事件统计
    total_incidents = db.query(Incident).count()
    open_incidents = (
        db.query(Incident).filter(Incident.status == "open").count()
    )
    investigating_incidents = (
        db.query(Incident)
        .filter(Incident.status == "investigating")
        .count()
    )
    resolved_incidents = (
        db.query(Incident).filter(Incident.status == "resolved").count()
    )
    critical_incidents = (
        db.query(Incident).filter(Incident.severity == "critical").count()
    )
    warning_incidents = (
        db.query(Incident).filter(Incident.severity == "warning").count()
    )

    # 近 24 小时触发的规则数
    since = datetime.now() - timedelta(hours=24)
    recent_fired_rules = (
        db.query(AlertRule)
        .filter(AlertRule.last_fired_at.isnot(None))
        .filter(AlertRule.last_fired_at >= since)
        .count()
    )

    return AlertStats(
        total_rules=total_rules,
        enabled_rules=enabled_rules,
        total_incidents=total_incidents,
        open_incidents=open_incidents,
        investigating_incidents=investigating_incidents,
        resolved_incidents=resolved_incidents,
        critical_incidents=critical_incidents,
        warning_incidents=warning_incidents,
        recent_fired_rules=recent_fired_rules,
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _validate_rule_params(request: AlertRuleCreate) -> None:
    """校验规则参数与规则类型的匹配性。

    Args:
        request: 规则创建请求

    Raises:
        HTTPException 400: 参数不完整
    """
    if request.rule_type == "threshold":
        if not request.metric_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="阈值规则必须指定 metric_name",
            )
        if request.threshold_value is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="阈值规则必须指定 threshold_value",
            )
    elif request.rule_type == "log_keyword":
        if not request.log_query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="日志关键词规则必须指定 log_query",
            )
    elif request.rule_type == "promql":
        if not request.promql_expr:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PromQL 规则必须指定 promql_expr",
            )


def _append_timeline(
    incident: Incident,
    event: str,
    note: Optional[str] = None,
    **extra: object,
) -> None:
    """向事件时间线追加一条记录。

    Args:
        incident: 事件对象
        event: 事件描述
        note: 备注
        **extra: 额外字段
    """
    existing: list = []
    if incident.timeline:
        try:
            existing = json.loads(incident.timeline)
            if not isinstance(existing, list):
                existing = []
        except (ValueError, TypeError):
            existing = []

    entry = {
        "time": datetime.now().isoformat(),
        "event": event,
    }
    if note:
        entry["note"] = note
    entry.update(extra)

    existing.append(entry)
    incident.timeline = json.dumps(existing, ensure_ascii=False)
