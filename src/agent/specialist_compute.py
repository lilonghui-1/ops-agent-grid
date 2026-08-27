"""计算专家 Agent - Kubernetes 工作负载与容器状态分析

本 Agent 遵循 inspect_agent.py 的模式，专注于容器与编排侧运维：
- K8s 工作负载状态检查（Deployment / Pod / Service）
- 容器运行状态与资源占用分析
- 集群节点资源调度情况
"""

from langchain_core.messages import HumanMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import SPECIALIST_COMPUTE_PROMPT
from ..tools.base import ToolRegistry
from ..utils.logger import get_logger

logger = get_logger("specialist_compute", agent_name="compute")


class SpecialistCompute:
    """计算专家 Agent - K8s 工作负载与容器状态分析"""

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "compute")

        # 计算专家通过 SSH 执行 kubectl / docker / crictl 命令
        # 并结合系统指标采集容器节点的资源占用
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
            prompt=SPECIALIST_COMPUTE_PROMPT,
        )
        return self._agent

    async def run(self, task: str, **kwargs) -> dict:
        """执行计算/K8s 分析任务

        Args:
            task: 任务描述，如 "检查 default 命名空间下所有 Pod 状态"
            **kwargs: 扩展参数（context 上下文等）

        Returns:
            包含容器/K8s 分析结果的字典
        """
        logger.info(f"计算专家开始处理任务: {task}")
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

            logger.info(f"计算专家任务完成，工具调用次数: {len(tool_calls)}")
            return {
                "agent": "compute",
                "task": task,
                "result": final_message,
                "tool_calls": tool_calls,
            }
        except Exception as e:
            logger.error(f"计算专家任务失败: {e}")
            return {
                "agent": "compute",
                "task": task,
                "result": f"计算分析失败: {type(e).__name__}: {e}",
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
