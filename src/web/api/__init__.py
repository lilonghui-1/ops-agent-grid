"""API 路由集合 - 导出所有路由模块的 router 实例"""

from .approvals import router as approvals_router
from .audit import router as audit_router
from .auth import router as auth_router
from .chat import router as chat_router
from .configs import router as configs_router
from .logs import router as logs_router
from .servers import router as servers_router
from .services import router as services_router

__all__ = [
    "auth_router",
    "servers_router",
    "logs_router",
    "services_router",
    "configs_router",
    "chat_router",
    "audit_router",
    "approvals_router",
]
