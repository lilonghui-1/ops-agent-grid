"""审批相关 Pydantic 模型

包含审批创建、响应、审核、分页列表等数据结构。
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ApprovalCreate(BaseModel):
    """审批创建请求

    由任意已认证用户提交，描述需要审批的工具调用及其风险信息。
    """

    title: str = Field(..., description="审批标题")
    description: str = Field("", description="审批描述")
    tool_name: str = Field(..., description="请求审批的工具名称")
    tool_params: dict = Field(
        default_factory=dict, description="工具请求参数（JSON 对象）"
    )
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        "medium", description="风险等级: low(低) / medium(中) / high(高) / critical(严重)"
    )
    impact_analysis: str = Field("", description="影响分析（执行后会发生什么）")
    rollback_plan: str = Field("", description="回滚计划（如何撤销操作）")


class ApprovalResponse(BaseModel):
    """审批详情响应"""

    id: int = Field(..., description="审批 ID")
    title: str = Field(..., description="审批标题")
    description: str = Field("", description="审批描述")
    tool_name: str = Field(..., description="工具名称")
    tool_params: Optional[dict] = Field(None, description="工具请求参数")
    risk_level: str = Field(..., description="风险等级")
    status: str = Field(..., description="审批状态: pending/approved/rejected/expired")
    requested_by: str = Field(..., description="请求人用户名")
    requested_at: datetime = Field(..., description="请求时间")
    reviewed_by: Optional[str] = Field(None, description="审核人用户名")
    reviewed_at: Optional[datetime] = Field(None, description="审核时间")
    review_comment: Optional[str] = Field(None, description="审核意见")
    expires_at: datetime = Field(..., description="过期时间")
    impact_analysis: str = Field("", description="影响分析")
    rollback_plan: str = Field("", description="回滚计划")


class ApprovalReview(BaseModel):
    """审批审核请求

    由管理员提交，决定审批是通过还是拒绝。
    """

    status: Literal["approved", "rejected"] = Field(
        ..., description="审核结果: approved(通过) / rejected(拒绝)"
    )
    review_comment: Optional[str] = Field(None, description="审核意见")


class ApprovalList(BaseModel):
    """审批列表分页响应"""

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页条数")
    items: List[ApprovalResponse] = Field(
        default_factory=list, description="审批列表"
    )
