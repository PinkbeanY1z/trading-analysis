"""A native Function Calling planning tool, not keyword-selected execution."""
from langchain_core.tools import StructuredTool
from .question_schema import ResearchPlan
from .tools import RUN


def planning_tool():
    async def plan_research(questions, proposed_tools, primary_timeframe=None):
        plan = ResearchPlan.model_validate(dict(questions=questions, proposed_tools=proposed_tools,
                                                primary_timeframe=primary_timeframe))
        RUN.get()['plan'] = plan
        return {'kind':'research_plan', **plan.model_dump()}
    return StructuredTool.from_function(coroutine=plan_research, name='plan_research', args_schema=ResearchPlan,
        description='每轮先明确用户子问题、主要周期和必要专项工具；可与首个研究工具同轮调用。只是计划，不是市场证据；不必调用所有工具。')
