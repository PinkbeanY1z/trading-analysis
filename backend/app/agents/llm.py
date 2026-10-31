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
        names=['get_market_data','calculate_technical_indicators','analyze_market_structure']
        if step<3:
            return AIMessage(content='',tool_calls=[{'id':f'demo_{step}','name':names[step],'args':{'symbol':'QQQ','interval':'1day'}}])
        return AIMessage(content='{"evidence_ids":["demo_0","demo_1","demo_2"],"primary_timeframe":"1day","conclusion":"需要更多证据"}')

def model(settings):
    if settings.agent_mode=='mock': return ScriptedLLM()
    if not settings.deepseek_api_key: return None
    return ChatOpenAI(api_key=settings.deepseek_api_key,base_url=settings.deepseek_base_url,
                      model=settings.deepseek_model,timeout=settings.deepseek_timeout,max_retries=0,
                      max_tokens=1500,extra_body={'thinking':{'type':'disabled'}})
