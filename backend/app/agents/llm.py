from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

class ScriptedLLM:
    """Offline test double: consumes supplied AIMessage plans, never parses user keywords."""
    def __init__(self,responses=None):
        self.responses=list(responses or [])
        self.seen=[]
    def bind_tools(self,tools): return self
    def bind_report(self): return self
    async def ainvoke(self,messages):
        self.seen.append(messages)
        if self.responses: return self.responses.pop(0)
        # Explicit fixed demo, not natural-language understanding.
        last_user=max(i for i,m in enumerate(messages) if isinstance(m,HumanMessage))
        step=sum(isinstance(m,AIMessage) for m in messages[last_user+1:])
        if step==0:
            return AIMessage(content='',tool_calls=[
                {'id':'demo_plan','name':'plan_research','args':{'questions':[{'id':'q1','kind':'trend','question':'演示日线结构'}],
                    'primary_timeframe':'1day','proposed_tools':['analyze_market_structure']}},
                {'id':'demo_structure','name':'analyze_market_structure','args':{'symbol':'QQQ','interval':'1day'}}])
        return AIMessage(content='{"protocol_version":2,"evidence_ids":["demo_structure"],"primary_timeframe":"1day","answer_blocks":[{"kind":"direct_answer","title":"模拟结构演示","text":"这是模拟历史行情，当前均线结构为{{0.maAlignment}}；不能据此判断真实市场的买入时机。","question_ids":["q1"],"evidence_ids":["demo_structure"]}]}')

def model(settings):
    if settings.agent_mode=='mock': return ScriptedLLM()
    if not settings.deepseek_api_key: return None
    return ChatOpenAI(api_key=settings.deepseek_api_key,base_url=settings.deepseek_base_url,
                      model=settings.deepseek_model,timeout=settings.deepseek_timeout,max_retries=0,
                      max_tokens=2300,extra_body={'thinking':{'type':'disabled'}})
