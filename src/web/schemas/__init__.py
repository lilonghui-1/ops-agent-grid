"""Pydantic Schema 集合 - 导出所有请求/响应模型"""

from .approval import (
    ApprovalCreate,
    ApprovalList,
    ApprovalResponse,
    ApprovalReview,
)
from .auth import (
    LoginRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserResponse,
)
from .chat import (
    ChatMessage,
    ChatSession,
    CreateSessionRequest,
    ModelInfo,
    SendMessageRequest,
)
from .config_file import (
    ConfigBackupInfo,
    ConfigFileContent,
    ConfigFileInfo,
    ConfigRollbackRequest,
    ConfigSaveRequest,
)
from .log import (
    LogExportRequest,
    LogPlatformQueryRequest,
    LogSearchRequest,
    LogSearchResponse,
)
from .server import (
    CPUInfo,
    DiskInfo,
    MemInfo,
    MetricHistoryResponse,
    PowerRequest,
    ServerInfo,
    ServerStatusResponse,
    ThresholdConfig,
)
from .service import (
    BatchServiceOperationRequest,
    ServiceInfo,
    ServiceListResponse,
    ServiceOperationRequest,
)

__all__ = [
    # approval
    "ApprovalCreate",
    "ApprovalList",
    "ApprovalResponse",
    "ApprovalReview",
    # auth
    "LoginRequest",
    "RefreshTokenRequest",
    "TokenResponse",
    "UserResponse",
    # chat
    "ChatMessage",
    "ChatSession",
    "CreateSessionRequest",
    "ModelInfo",
    "SendMessageRequest",
    # config_file
    "ConfigBackupInfo",
    "ConfigFileContent",
    "ConfigFileInfo",
    "ConfigRollbackRequest",
    "ConfigSaveRequest",
    # log
    "LogExportRequest",
    "LogPlatformQueryRequest",
    "LogSearchRequest",
    "LogSearchResponse",
    # server
    "CPUInfo",
    "DiskInfo",
    "MemInfo",
    "MetricHistoryResponse",
    "PowerRequest",
    "ServerInfo",
    "ServerStatusResponse",
    "ThresholdConfig",
    # service
    "BatchServiceOperationRequest",
    "ServiceInfo",
    "ServiceListResponse",
    "ServiceOperationRequest",
]
