"""数据库专家 Agent - 数据库性能、慢查询与连接池分析

本 Agent 遵循 inspect_agent.py / diagnose_agent.py 的模式，专注于数据库侧运维：
- 数据库运行状态检查（连接数、慢查询、主从复制）
- 慢查询分析
- 连接池与表空间使用分析
"""

from langchain_core.messages import HumanMessage

from ..models.llm_factory import LLMFactory
from ..models.prompts import SPECIALIST_DB_PROMPT
from ..tools.base import ToolRegistry
from ..utils.logger import get_logger

logger = get_logger("specialist_db", agent_name="db")


class SpecialistDB:
    """数据库专家 Agent - 数据库性能与慢查询分析"""

    def __init__(self, config):
        self.config = config
        self.llm = LLMFactory.create_for_agent(config, "db")

        # 数据库专家需要查询和状态工具
        tool_names = ["db_query", "db_status", "redis_info"]
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
            prompt=SPECIALIST_DB_PROMPT,
        )
        return self._agent

    async def run(self, task: str, **kwargs) -> dict:
        """执行数据库分析任务

        Args:
            task: 任务描述，如 "分析 MySQL 192.168.1.10 的慢查询"
            **kwargs: 扩展参数（context 上下文等）

        Returns:
            包含数据库分析结果的字典
        """
        logger.info(f"数据库专家开始处理任务: {task}")
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

            logger.info(f"数据库专家任务完成，工具调用次数: {len(tool_calls)}")
            return {
                "agent": "db",
                "task": task,
                "result": final_message,
                "tool_calls": tool_calls,
            }
        except Exception as e:
            logger.error(f"数据库专家任务失败: {e}")
            return {
                "agent": "db",
                "task": task,
                "result": f"数据库分析失败: {type(e).__name__}: {e}",
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
