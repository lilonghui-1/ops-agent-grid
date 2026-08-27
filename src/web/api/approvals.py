"""审批管理路由 - 工具执行前的人工审批流程

端点：
- GET    /stats              : 审批统计信息
- GET    /pending            : 当前用户的待审批列表
- GET    /                   : 分页查询审批列表，支持按状态/风险等级筛选
- POST   /                   : 创建审批请求（任意已认证用户）
- GET    /{approval_id}      : 获取审批详情
- POST   /{approval_id}/review : 审批通过或拒绝（仅管理员）
- DELETE /{approval_id}      : 取消待审批请求（请求人或管理员）
"""

import json
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.deps import get_current_active_user, require_admin
from ..database import get_db
from ..models.approval import Approval
from ..models.user import User
from ..schemas.approval import (
    ApprovalCreate,
    ApprovalList,
    ApprovalResponse,
    ApprovalReview,
)

router = APIRouter(tags=["审批管理"])

# 审批过期时间（小时）
APPROVAL_EXPIRE_HOURS = 24


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _mark_expired(db: Session) -> int:
    """将所有已过期但仍为 pending 状态的审批标记为 expired。

    Args:
        db: 数据库会话

    Returns:
        被标记为过期的审批数量
    """
    now = datetime.utcnow()
    expired_count = (
        db.query(Approval)
        .filter(Approval.status == "pending", Approval.expires_at < now)
        .update({Approval.status: "expired"})
    )
    if expired_count > 0:
        db.commit()
    return expired_count


def _to_response(approval: Approval) -> ApprovalResponse:
    """将 ORM 对象转换为响应模型。

    自动将 tool_params 字段从 JSON 文本解析为字典。

    Args:
        approval: Approval ORM 对象

    Returns:
        ApprovalResponse 响应模型
    """
    tool_params = None
    if approval.tool_params:
        try:
            tool_params = json.loads(approval.tool_params)
        except (json.JSONDecodeError, TypeError):
            tool_params = None

    return ApprovalResponse(
        id=approval.id,
        title=approval.title,
        description=approval.description or "",
        tool_name=approval.tool_name,
        tool_params=tool_params,
        risk_level=approval.risk_level,
        status=approval.status,
        requested_by=approval.requested_by,
        requested_at=approval.requested_at,
        reviewed_by=approval.reviewed_by,
        reviewed_at=approval.reviewed_at,
        review_comment=approval.review_comment,
        expires_at=approval.expires_at,
        impact_analysis=approval.impact_analysis or "",
        rollback_plan=approval.rollback_plan or "",
    )


# ---------------------------------------------------------------------------
# 统计信息响应模型
# ---------------------------------------------------------------------------
class ApprovalStats(BaseModel):
    """审批统计信息"""

    total: int = Field(0, description="审批总数")
    pending: int = Field(0, description="待审批数")
    approved: int = Field(0, description="已通过数")
    rejected: int = Field(0, description="已拒绝数")
    expired: int = Field(0, description="已过期数")
    by_risk_level: dict = Field(
        default_factory=dict, description="按风险等级统计"
    )


# ---------------------------------------------------------------------------
# GET /stats - 审批统计信息
# ---------------------------------------------------------------------------
@router.get("/stats", response_model=ApprovalStats, summary="审批统计信息")
def get_approval_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取审批统计信息，包括各状态数量和按风险等级的分布。

    Args:
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        ApprovalStats: 审批统计信息
    """
    # 先标记过期审批
    _mark_expired(db)

    total = db.query(Approval).count()
    pending = db.query(Approval).filter(Approval.status == "pending").count()
    approved = db.query(Approval).filter(Approval.status == "approved").count()
    rejected = db.query(Approval).filter(Approval.status == "rejected").count()
    expired = db.query(Approval).filter(Approval.status == "expired").count()

    by_risk_level = {
        level: db.query(Approval).filter(Approval.risk_level == level).count()
        for level in ("low", "medium", "high", "critical")
    }

    return ApprovalStats(
        total=total,
        pending=pending,
        approved=approved,
        rejected=rejected,
        expired=expired,
        by_risk_level=by_risk_level,
    )


# ---------------------------------------------------------------------------
# GET /pending - 当前用户待审批列表
# ---------------------------------------------------------------------------
@router.get(
    "/pending",
    response_model=List[ApprovalResponse],
    summary="当前用户待审批列表",
)
def list_pending_approvals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """获取当前用户的待审批列表。

    管理员可查看所有待审批记录，普通用户仅查看自己提交的待审批记录。

    Args:
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        List[ApprovalResponse]: 待审批列表
    """
    # 先标记过期审批
    _mark_expired(db)

    query = db.query(Approval).filter(Approval.status == "pending")

    # 非管理员仅查看自己提交的审批
    if current_user.role != "admin":
        query = query.filter(Approval.requested_by == current_user.username)

    approvals = query.order_by(Approval.requested_at.desc()).all()
    return [_to_response(a) for a in approvals]


# ---------------------------------------------------------------------------
# GET / - 分页查询审批列表
# ---------------------------------------------------------------------------
@router.get("", response_model=ApprovalList, summary="查询审批列表")
def list_approvals(
    status_filter: Optional[str] = Query(
        None, alias="status", description="按状态筛选: pending/approved/rejected/expired"
    ),
    risk_level: Optional[str] = Query(
        None, description="按风险等级筛选: low/medium/high/critical"
    ),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """分页查询审批列表，支持按状态和风险等级筛选。

    Args:
        status_filter: 按状态筛选
        risk_level: 按风险等级筛选
        page: 页码
        page_size: 每页条数
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        ApprovalList: 分页审批列表
    """
    # 先标记过期审批
    _mark_expired(db)

    query = db.query(Approval)

    if status_filter:
        query = query.filter(Approval.status == status_filter)

    if risk_level:
        query = query.filter(Approval.risk_level == risk_level)

    # 按请求时间倒序排列
    query = query.order_by(Approval.requested_at.desc())

    total = query.count()
    offset = (page - 1) * page_size
    approvals = query.offset(offset).limit(page_size).all()

    items = [_to_response(a) for a in approvals]

    return ApprovalList(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


# ---------------------------------------------------------------------------
# POST / - 创建审批请求
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建审批请求",
)
def create_approval(
    data: ApprovalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建审批请求，任意已认证用户均可提交。

    请求时间自动记录为当前时间，过期时间自动设为 24 小时后。

    Args:
        data: 审批创建请求数据
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        ApprovalResponse: 创建的审批详情
    """
    now = datetime.utcnow()
    approval = Approval(
        title=data.title,
        description=data.description,
        tool_name=data.tool_name,
        tool_params=json.dumps(data.tool_params, ensure_ascii=False),
        risk_level=data.risk_level,
        status="pending",
        requested_by=current_user.username,
        requested_at=now,
        expires_at=now + timedelta(hours=APPROVAL_EXPIRE_HOURS),
        impact_analysis=data.impact_analysis,
        rollback_plan=data.rollback_plan,
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)

    return _to_response(approval)


# ---------------------------------------------------------------------------
# GET /{approval_id} - 获取审批详情
# ---------------------------------------------------------------------------
@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
    summary="获取审批详情",
)
def get_approval(
    approval_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """根据 ID 获取审批详情。

    Args:
        approval_id: 审批 ID
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        ApprovalResponse: 审批详情

    Raises:
        HTTPException 404: 审批不存在
    """
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"审批不存在: ID={approval_id}",
        )

    return _to_response(approval)


# ---------------------------------------------------------------------------
# POST /{approval_id}/review - 审批通过或拒绝
# ---------------------------------------------------------------------------
@router.post(
    "/{approval_id}/review",
    response_model=ApprovalResponse,
    summary="审批通过或拒绝",
)
def review_approval(
    approval_id: int,
    data: ApprovalReview,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """审批通过或拒绝，仅管理员可操作。

    只能审核处于 pending 状态的审批，已审核或已过期的审批无法再次审核。

    Args:
        approval_id: 审批 ID
        data: 审核请求数据（status + review_comment）
        db: 数据库会话
        current_user: 当前登录的管理员用户

    Returns:
        ApprovalResponse: 审核后的审批详情

    Raises:
        HTTPException 404: 审批不存在
        HTTPException 400: 审批不在待审核状态
    """
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"审批不存在: ID={approval_id}",
        )

    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"审批当前状态为 {approval.status}，无法审核",
        )

    approval.status = data.status
    approval.reviewed_by = current_user.username
    approval.reviewed_at = datetime.utcnow()
    approval.review_comment = data.review_comment

    db.commit()
    db.refresh(approval)

    return _to_response(approval)


# ---------------------------------------------------------------------------
# DELETE /{approval_id} - 取消待审批请求
# ---------------------------------------------------------------------------
@router.delete("/{approval_id}", summary="取消待审批请求")
def cancel_approval(
    approval_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """取消待审批请求。

    仅审批请求人本人或管理员可取消，且只能取消处于 pending 状态的审批。

    Args:
        approval_id: 审批 ID
        db: 数据库会话
        current_user: 当前登录用户

    Returns:
        dict: 操作结果消息

    Raises:
        HTTPException 404: 审批不存在
        HTTPException 403: 无权取消该审批
        HTTPException 400: 审批不在待审核状态
    """
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"审批不存在: ID={approval_id}",
        )

    # 权限校验：仅请求人或管理员可取消
    is_owner = approval.requested_by == current_user.username
    is_admin = current_user.role == "admin"
    if not is_owner and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权取消该审批，仅请求人或管理员可操作",
        )

    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"审批当前状态为 {approval.status}，仅待审批状态可取消",
        )

    db.delete(approval)
    db.commit()

    return {"message": "审批请求已取消", "id": approval_id}
