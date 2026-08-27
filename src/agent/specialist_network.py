"""网络专家 Agent - 网络连通性、SNMP 设备发现与接口轮询

本 Agent 遵循 inspect_agent.py 的模式，专注于网络侧运维：
- 网络连通性检查（ping / traceroute / 端口探测）
- SNMP 设备发现与状态采集
- 网络接口流量与错误统计轮询
"""

from langchain_core.messages import HumanMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import SPECIALIST_NETWORK_PROMPT
from ..tools.base import ToolRegistry
from ..utils.logger import get_logger

logger = get_logger("specialist_network", agent_name="network")


class SpecialistNetwork:
    """网络专家 Agent - 网络连通性与设备状态分析"""

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "network")

        # 网络专家需要 SSH 执行网络命令与系统指标中的网络部分
        tool_names = ["ssh_execute", "system_metrics"]
        self.tools = [ToolRegistry.get(name) for name in tool_names]
        self.tools = [t for t in self.tools if t is not None]

        self._agent = None

    def _get_agent(self):
        """延迟初始化 Agent（避免在导入时依赖 LangChain）"""
        if self._agent is not None:
            return self._agent

        try:
            from langgraph.prebuilt import create_react_agent
        except ImportError:
            raise ImportError("langgraph 未安装，请执行: pip install langgraph")

        # 将 BaseTool 转换为 LangChain 工具
        langchain_tools = []
        for tool in self.tools:
            lc_tool = ToolRegistry._convert_single_tool(tool)
            if lc_tool:
                langchain_tools.append(lc_tool)

        self._agent = create_react_agent(
            model=self.llm,
            tools=langchain_tools,
            prompt=SPECIALIST_NETWORK_PROMPT,
        )
        return self._agent

    async def run(self, task: str, **kwargs) -> dict:
        """执行网络分析任务

        Args:
            task: 任务描述，如 "检查 192.168.1.10 到 192.168.1.20 的连通性"
            **kwargs: 扩展参数（context 上下文等）

        Returns:
            包含网络分析结果的字典
        """
        logger.info(f"网络专家开始处理任务: {task}")
        agent = self._get_agent()

        try:
            # 构建完整的任务描述
            full_task = task
            context = kwargs.get("context")
            if context:
                full_task = f"{task}\n\n## 已知上下文\n{context}"

            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=full_task)]}
            )
            final_message = result["messages"][-1].content
            tool_calls = self._extract_tool_calls(result["messages"])

            logger.info(f"网络专家任务完成，工具调用次数: {len(tool_calls)}")
            return {
                "agent": "network",
                "task": task,
                "result": final_message,
                "tool_calls": tool_calls,
            }
        except Exception as e:
            logger.error(f"网络专家任务失败: {e}")
            return {
                "agent": "network",
                "task": task,
                "result": f"网络分析失败: {type(e).__name__}: {e}",
                "tool_calls": [],
                "error": str(e),
            }

    def _extract_tool_calls(self, messages) -> list:
        """提取 Agent 执行过程中的工具调用记录"""
        calls = []
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    calls.append({
                        "tool": tc.get("name", "unknown"),
                        "args": tc.get("args", {}),
                    })
        return calls
