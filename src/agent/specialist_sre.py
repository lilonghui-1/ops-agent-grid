"""SRE 专家 Agent - 服务器巡检、资源分析与容量规划

本 Agent 遵循 inspect_agent.py 的模式，专注于服务器侧运维：
- CPU / 内存 / 磁盘 / 网络资源采集与分析
- 容量规划与趋势评估
- 关键服务运行状态检查
"""

from langchain_core.messages import HumanMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import SPECIALIST_SRE_PROMPT
from ..tools.base import ToolRegistry
from ..utils.logger import get_logger

logger = get_logger("specialist_sre", agent_name="sre")


class SpecialistSRE:
    """SRE 专家 Agent - 服务器巡检与资源分析"""

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "sre")

        # SRE 专家需要服务器指标与服务管理工具
        tool_names = ["system_metrics", "service_control", "ssh_execute"]
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
            prompt=SPECIALIST_SRE_PROMPT,
        )
        return self._agent

    async def run(self, task: str, **kwargs) -> dict:
        """执行 SRE 巡检/资源分析任务

        Args:
            task: 任务描述，如 "巡检服务器 192.168.1.10 的资源使用情况"
            **kwargs: 扩展参数（context 上下文等）

        Returns:
            包含巡检结果的字典
        """
        logger.info(f"SRE 专家开始处理任务: {task}")
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

            logger.info(f"SRE 专家任务完成，工具调用次数: {len(tool_calls)}")
            return {
                "agent": "sre",
                "task": task,
                "result": final_message,
                "tool_calls": tool_calls,
            }
        except Exception as e:
            logger.error(f"SRE 专家任务失败: {e}")
            return {
                "agent": "sre",
                "task": task,
                "result": f"SRE 巡检失败: {type(e).__name__}: {e}",
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
