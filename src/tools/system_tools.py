"""系统操作工具 - 服务器指标采集、服务管理（支持 Linux 和 Windows）"""

import logging

from .base import BaseTool, ToolResult, ToolParameter

logger = logging.getLogger(__name__)


# Linux 系统指标采集命令
LINUX_METRIC_COMMANDS = {
    'cpu': (
        "echo '=== CPU Info ===' && "
        "echo 'Load Average:' && cat /proc/loadavg && "
        "echo 'CPU Usage:' && top -bn1 | head -5 | tail -1 && "
        "echo 'CPU Cores:' && nproc 2>/dev/null && "
        "echo 'Process Count:' && ps aux | wc -l"
    ),
    'memory': (
        "echo '=== Memory Info ===' && "
        "free -h && "
        "echo 'Top Memory Processes:' && "
        "ps aux --sort=-%mem | head -6"
    ),
    'disk': (
        "echo '=== Disk Info ===' && "
        "df -h | grep -v tmpfs && "
        "echo 'Inode Usage:' && df -i | grep -v tmpfs && "
        "echo 'Disk I/O:' && iostat -x 1 1 2>/dev/null || echo 'iostat not available'"
    ),
    'network': (
        "echo '=== Network Info ===' && "
        "echo 'Connections Summary:' && ss -s && "
        "echo 'Listening Ports:' && ss -tlnp 2>/dev/null | head -20 && "
        "echo 'Network Interfaces:' && ip -brief addr 2>/dev/null || ifconfig 2>/dev/null | head -30"
    ),
}

# Windows 系统指标采集命令（PowerShell）
WINDOWS_METRIC_COMMANDS = {
    'cpu': (
        "powershell -Command \""
        "Write-Host '=== CPU Info ==='; "
        "Write-Host 'CPU Usage:'; "
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage; "
        "Write-Host \"CPU Usage: $cpu%\"; "
        "Write-Host 'CPU Cores:'; "
        "(Get-CimInstance Win32_Processor).NumberOfLogicalProcessors; "
        "Write-Host 'Process Count:'; "
        "(Get-Process).Count"
        "\""
    ),
    'memory': (
        "powershell -Command \""
        "Write-Host '=== Memory Info ==='; "
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "$totalGB = [math]::Round($os.TotalVisibleMemorySize/1MB, 2); "
        "$freeGB = [math]::Round($os.FreePhysicalMemory/1MB, 2); "
        "$usedGB = [math]::Round($totalGB - $freeGB, 2); "
        "$usedPct = [math]::Round(($totalGB - $freeGB)/$totalGB*100, 1); "
        "Write-Host \"Total: ${totalGB}GB, Used: ${usedGB}GB (${usedPct}%), Free: ${freeGB}GB\"; "
        "Write-Host 'Top Memory Processes:'; "
        "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 Name, @{N='MemoryMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table -AutoSize"
        "\""
    ),
    'disk': (
        "powershell -Command \""
        "Write-Host '=== Disk Info ==='; "
        "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { "
        "$totalGB = [math]::Round($_.Size/1GB, 2); "
        "$freeGB = [math]::Round($_.FreeSpace/1GB, 2); "
        "$usedPct = [math]::Round(($_.Size - $_.FreeSpace)/$_.Size*100, 1); "
        "Write-Host \"$($_.DeviceID) Total: ${totalGB}GB, Free: ${freeGB}GB (${usedPct}% used)\"; "
        "}"
        "\""
    ),
    'network': (
        "powershell -Command \""
        "Write-Host '=== Network Info ==='; "
        "Write-Host 'Listening Ports:'; "
        "Get-NetTCPConnection -State Listen | Select-Object -First 20 LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize; "
        "Write-Host 'Network Interfaces:'; "
        "Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.PrefixOrigin -ne 'WellKnown'} | Select-Object InterfaceAlias,IPAddress | Format-Table -AutoSize"
        "\""
    ),
}


class SystemMetricsTool(BaseTool):
    """系统指标采集工具 - CPU/内存/磁盘/网络（支持 Linux 和 Windows）"""

    name = "system_metrics"
    description = "获取服务器系统指标（CPU、内存、磁盘、网络）。自动识别 Linux/Windows 系统类型。"
    parameters = [
        ToolParameter(name="host", type="string", description="服务器地址"),
        ToolParameter(
            name="metric_type",
            type="string",
            description="指标类型: all(全部), cpu, memory, disk, network",
            required=False,
            default="all"
        ),
        ToolParameter(
            name="os_type",
            type="string",
            description="操作系统类型: linux(默认), windows。如不指定则自动从配置读取。",
            required=False,
            default=None
        ),
    ]

    def __init__(self, ssh_tool=None, config=None):
        self._ssh_tool = ssh_tool
        self._config = config
        self._server_map = {}
        if config and hasattr(config, 'servers'):
            for s in config.servers:
                self._server_map[s.host] = s

    def _set_ssh_tool(self, ssh_tool):
        """延迟设置 SSH 工具"""
        self._ssh_tool = ssh_tool

    def _get_os_type(self, host: str, os_type: str = None) -> str:
        """获取服务器操作系统类型"""
        if os_type and os_type.lower() in ('linux', 'windows'):
            return os_type.lower()
        server = self._server_map.get(host)
        if server and hasattr(server, 'os_type'):
            return server.os_type.lower()
        return 'linux'  # 默认 Linux

    def execute(self, **kwargs) -> ToolResult:
        if not self._ssh_tool:
            return ToolResult(success=False, error="SSH 工具未初始化")

        host = kwargs['host']
        metric_type = kwargs.get('metric_type', 'all')
        os_type = self._get_os_type(host, kwargs.get('os_type'))

        commands = WINDOWS_METRIC_COMMANDS if os_type == 'windows' else LINUX_METRIC_COMMANDS

        try:
            if metric_type == 'all':
                results = {}
                for mtype, cmd in commands.items():
                    r = self._ssh_tool.execute(host=host, command=cmd, timeout=30)
                    results[mtype] = r.data['stdout'] if r.success else f"获取失败: {r.error}"
                return ToolResult(
                    success=True, data=results,
                    metadata={"host": host, "os_type": os_type}
                )
            else:
                cmd = commands.get(metric_type)
                if not cmd:
                    return ToolResult(success=False, error=f"不支持的指标类型: {metric_type}")
                r = self._ssh_tool.execute(host=host, command=cmd, timeout=30)
                return ToolResult(
                    success=r.success,
                    data=r.data if r.success else None,
                    error=r.error,
                    metadata={"host": host, "metric_type": metric_type, "os_type": os_type}
                )
        except Exception as e:
            return ToolResult(success=False, error=str(e), metadata={"host": host})


class ServiceControlTool(BaseTool):
    """服务管理工具 - 支持 Linux(systemctl) 和 Windows(sc.exe/PowerShell)"""

    name = "service_control"
    description = "管理远程服务器上的系统服务（启动、停止、重启、查看状态）。自动适配 Linux/Windows。"
    requires_approval = True  # 服务控制操作风险较高，执行前需要人工审批
    parameters = [
        ToolParameter(name="host", type="string", description="服务器地址"),
        ToolParameter(name="service_name", type="string", description="服务名称（如 nginx, mysql, redis, nginxsvc）"),
        ToolParameter(name="action", type="string", description="操作: start, stop, restart, status"),
        ToolParameter(name="use_sudo", type="boolean", description="是否使用 sudo（仅 Linux）", required=False, default=True),
        ToolParameter(name="os_type", type="string", description="操作系统类型: linux, windows", required=False, default=None),
    ]

    def __init__(self, ssh_tool=None, config=None):
        self._ssh_tool = ssh_tool
        self._config = config
        self._server_map = {}
        if config and hasattr(config, 'servers'):
            for s in config.servers:
                self._server_map[s.host] = s

    def _set_ssh_tool(self, ssh_tool):
        """延迟设置 SSH 工具"""
        self._ssh_tool = ssh_tool

    def _get_os_type(self, host: str, os_type: str = None) -> str:
        """获取服务器操作系统类型"""
        if os_type and os_type.lower() in ('linux', 'windows'):
            return os_type.lower()
        server = self._server_map.get(host)
        if server and hasattr(server, 'os_type'):
            return server.os_type.lower()
        return 'linux'

    def execute(self, **kwargs) -> ToolResult:
        if not self._ssh_tool:
            return ToolResult(success=False, error="SSH 工具未初始化")

        host = kwargs['host']
        service = kwargs['service_name']
        action = kwargs['action']
        use_sudo = kwargs.get('use_sudo', True)
        os_type = self._get_os_type(host, kwargs.get('os_type'))

        # 校验操作类型
        valid_actions = ('start', 'stop', 'restart', 'status')
        if action not in valid_actions:
            return ToolResult(
                success=False,
                error=f"不支持的操作: {action}，允许的操作: {', '.join(valid_actions)}"
            )

        # 根据操作系统类型构建命令
        if os_type == 'windows':
            # Windows: 使用 PowerShell 的服务管理命令
            action_map = {
                'start': 'Start-Service',
                'stop': 'Stop-Service',
                'restart': 'Restart-Service',
                'status': 'Get-Service',
            }
            ps_cmd = action_map[action]
            cmd = f'powershell -Command "{ps_cmd} -Name \'{service}\' 2>&1"'
        else:
            # Linux: 使用 systemctl
            prefix = "sudo " if use_sudo else ""
            cmd = f"{prefix}systemctl {action} {service} 2>&1"

        result = self._ssh_tool.execute(host=host, command=cmd, timeout=30)
        return ToolResult(
            success=result.success,
            data=result.data if result.success else None,
            error=result.error,
            metadata={"host": host, "service": service, "action": action, "os_type": os_type}
        )
