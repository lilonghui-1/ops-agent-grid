"""技能目录路由 - 查询已注册工具及其分类

端点：
- GET /            : 列出所有已注册工具（名称、描述、参数）
- GET /categories  : 按类别分组工具（system / database / network / log / k8s 等）
- GET /{tool_name} : 获取指定工具的详细信息（含 JSON Schema）
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from ..core.deps import get_current_active_user
from ..models.user import User
from ...tools.base import BaseTool, ToolRegistry

router = APIRouter(tags=["技能目录"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------
class ToolParameterItem(BaseModel):
    """工具参数条目"""

    name: str = Field(..., description="参数名称")
    type: str = Field("string", description="参数类型")
    description: str = Field("", description="参数描述")
    required: bool = Field(True, description="是否必填")
    default: Any = Field(None, description="默认值")


class ToolItem(BaseModel):
    """工具条目"""

    name: str = Field(..., description="工具名称")
    description: str = Field("", description="工具描述")
    parameters: List[ToolParameterItem] = Field(
        default_factory=list, description="参数列表"
    )
    requires_approval: bool = Field(False, description="是否需要审批")


class ToolDetail(BaseModel):
    """工具详细信息"""

    name: str = Field(..., description="工具名称")
    description: str = Field("", description="工具描述")
    parameters: List[ToolParameterItem] = Field(
        default_factory=list, description="参数列表"
    )
    requires_approval: bool = Field(False, description="是否需要审批")
    schema: Dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema"
    )


class CategoryGroup(BaseModel):
    """工具分类分组"""

    category: str = Field(..., description="类别名称")
    tools: List[ToolItem] = Field(
        default_factory=list, description="该类别下的工具列表"
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _tool_to_item(tool: BaseTool) -> ToolItem:
    """将 BaseTool 转换为 ToolItem 响应模型"""
    return ToolItem(
        name=tool.name,
        description=tool.description,
        parameters=[
            ToolParameterItem(
                name=p.name,
                type=p.type,
                description=p.description,
                required=p.required,
                default=p.default,
            )
            for p in tool.parameters
        ],
        requires_approval=tool.requires_approval,
    )


def _categorize_tool(name: str) -> str:
    """根据工具名称推断所属类别

    类别：system / database / network / log / k8s / ssh /
          topology / notify / terminal / other
    """
    # SSH 与终端类
    if name.startswith("ssh_") or name == "web_terminal":
        return "terminal"
    # 数据库类
    if name.startswith("db_") or name == "redis_info":
        return "database"
    # 日志类
    if name.startswith("log_"):
        return "log"
    # 系统类
    if name.startswith("system_") or name.startswith("service_"):
        return "system"
    # 网络类
    if name.startswith("network_") or name.startswith("snmp_") or name.startswith("port_"):
        return "network"
    # Kubernetes 类
    if name.startswith("k8s_"):
        return "k8s"
    # 拓扑类
    if name.startswith("topology_") or name == "blast_radius":
        return "topology"
    # 通知类
    if name.startswith("notify_") or name == "send_notification":
        return "notify"
    return "other"


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.get("", response_model=List[ToolItem], summary="列出所有已注册工具")
def list_tools(
    current_user: User = Depends(get_current_active_user),
):
    """列出所有已注册工具，包含名称、描述和参数信息。

    Returns:
        List[ToolItem]: 工具列表
    """
    all_tools = ToolRegistry.get_all()
    return [_tool_to_item(tool) for tool in all_tools.values()]


@router.get(
    "/categories",
    response_model=List[CategoryGroup],
    summary="按类别分组工具",
)
def list_categories(
    current_user: User = Depends(get_current_active_user),
):
    """按类别分组所有已注册工具。

    类别包括：system / database / network / log / k8s /
    terminal / topology / notify / other 等。

    Returns:
        List[CategoryGroup]: 分类分组列表
    """
    all_tools = ToolRegistry.get_all()
    groups: Dict[str, List[ToolItem]] = {}

    for name, tool in all_tools.items():
        category = _categorize_tool(name)
        groups.setdefault(category, []).append(_tool_to_item(tool))

    return [
        CategoryGroup(category=cat, tools=tools)
        for cat, tools in groups.items()
    ]


@router.get(
    "/{tool_name}",
    response_model=ToolDetail,
    summary="获取工具详细信息",
)
def get_tool_detail(
    tool_name: str = Path(..., description="工具名称"),
    current_user: User = Depends(get_current_active_user),
):
    """获取指定工具的详细信息，包含完整参数定义和 JSON Schema。

    Args:
        tool_name: 工具名称
        current_user: 当前登录用户

    Returns:
        ToolDetail: 工具详细信息

    Raises:
        HTTPException 404: 工具不存在
    """
    tool = ToolRegistry.get(tool_name)
    if not tool:
        raise HTTPException(
            status_code=404, detail=f"工具 '{tool_name}' 不存在"
        )

    return ToolDetail(
        name=tool.name,
        description=tool.description,
        parameters=[
            ToolParameterItem(
                name=p.name,
                type=p.type,
                description=p.description,
                required=p.required,
                default=p.default,
            )
            for p in tool.parameters
        ],
        requires_approval=tool.requires_approval,
        schema=tool.get_schema(),
    )
