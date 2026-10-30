import asyncio,json
import pytest
from langchain_core.messages import AIMessage,HumanMessage
from app.agents.graph import Agent
from app.agents.llm import ScriptedLLM
from app.agents.schemas import AgentSettings,ChatRequest
from app.market import MarketService
from app.providers import MockProvider
from app.models import MarketError,Settings
from app.main import create_app
from fastapi.testclient import TestClient

def call(name='analyze_market_structure',id='a',**args):
    return AIMessage(content='',tool_calls=[dict(id=id,name=name,args={'symbol':'QQQ',**args})])
def final(*ids):
    return AIMessage(content=json.dumps({'evidence_ids':list(ids),'conclusion':'需要更多证据'}))
def run(plan,message='分析 QQQ 的日线趋势和 RSI14。',provider=None,**settings):
    async def execute():
        model=ScriptedLLM(plan)
        agent=Agent(MarketService(provider or MockProvider()),AgentSettings(agent_mode='mock',**settings),model)
        return await agent.chat(ChatRequest(message=message))
    return asyncio.run(execute())

@pytest.mark.parametrize('message,plan',[
 ('分析 QQQ 的日线趋势和 RSI14。',[call('get_market_data',id='a'),call('calculate_technical_indicators',id='b'),call(id='c'),final('a','b','c')]),
 ('比较 QQQ 日线与 1 小时的趋势。',[call('compare_timeframes',intervals=['1day','1h']),final('a')]),
 ('分析黄金目前的技术结构。',[call(symbol='XAU/USD',asset_class='gold'),final('a')])])
def test_scenarios(message,plan):
    response=run(plan,message)
    assert response.status=='success'
    calls=[tc for m in plan for tc in m.tool_calls]
    assert [c['id'] for c in calls]==[r.call_id for r in response.tool_executions]
    assert all(r.status=='success' for r in response.tool_executions)
    for evidence in response.report.evidence:
        original=next(r for r in response.tool_executions if r.call_id==evidence['call_id'])
        source=next((x for x in original.result.get('timeframes',[]) if x['interval']==evidence['interval']),original.result)
        assert evidence['technical_indicators']==source['technical_indicators']
        if evidence['symbol']=='XAU/USD': assert evidence['technical_indicators']['relativeVolume'] is None

def test_context():
    async def execute():
        model=ScriptedLLM([call(),final('a'),call(id='b',interval='4h'),final('b')])
        agent=Agent(MarketService(MockProvider()),AgentSettings(agent_mode='mock'),model)
        first=await agent.chat(ChatRequest(message='分析 QQQ 的日线'))
        second=await agent.chat(ChatRequest(message='它的4小时呢？',thread_id=first.thread_id))
        assert second.timeframes==['4h'] and second.symbol=='QQQ'
        assert [m.content for m in model.seen[2] if isinstance(m,HumanMessage)]==['分析 QQQ 的日线','它的4小时呢？']
        assert len(second.tool_executions)==1
    asyncio.run(execute())

@pytest.mark.parametrize('args',[{'symbol':'???'},{'interval':'2h'}])
def test_invalid_tool_arguments(args):
    response=run([call(**args),final('a')])
    assert response.status=='failed'
    assert response.tool_executions[0].status=='failed'

@pytest.mark.parametrize('mode',['empty','timeout','short'])
def test_data_failure(mode):
    class Bad(MockProvider):
        async def fetch(self,q):
            if mode=='empty': raise MarketError('empty_data','无数据',404)
            if mode=='timeout': await asyncio.sleep(.1)
            return (await super().fetch(q))[:10]
    response=run([call(),final('a')],provider=Bad(),agent_tool_timeout=.01 if mode=='timeout' else 2)
    assert response.status==('insufficient_data' if mode=='short' else 'failed')

def test_limit():
    response=run([call(id=str(i)) for i in range(5)],agent_max_rounds=2)
    assert response.status=='partial' and 'max_rounds' in response.warnings
    assert len(response.tool_executions)==2

def test_untrusted_report():
    response=run([call(),AIMessage(content='{"ma200":999999}')])
    assert response.status=='partial'
    assert response.report.interpretation is None
    assert response.report.evidence[0]['technical_indicators']['ma200'] != 999999

def test_no_evidence():
    assert run([final('invented')]).status=='failed'

def test_same_round_multiple_calls():
    calls=[dict(id='a',name='get_market_data',args={'symbol':'QQQ'}),dict(id='b',name='calculate_technical_indicators',args={'symbol':'QQQ'})]
    response=run([AIMessage(content='',tool_calls=calls),final('a','b')])
    assert [r.call_id for r in response.tool_executions]==['a','b']

def test_api():
    with TestClient(create_app(Settings(market_provider='mock'),agent_settings=AgentSettings(agent_mode='mock'))) as c:
        response=c.post('/api/agent/chat',json={'message':'分析 QQQ 的日线'})
        assert response.status_code==200
        assert response.json()['status']=='success'
        assert c.post('/api/agent/chat',json={'message':''}).status_code==422

def test_missing_key():
    with TestClient(create_app(Settings(market_provider='mock'),agent_settings=AgentSettings(agent_mode='deepseek',deepseek_api_key=''))) as c:
        response=c.post('/api/agent/chat',json={'message':'分析 QQQ'})
        assert response.json()['status']=='failed'
        assert 'missing_deepseek_key' in response.json()['warnings']

def test_per_request_computation_cache():
    class Counting(MarketService):
        calls=0
        async def get(self,q):
            self.calls+=1
            return await super().get(q)
    async def execute():
        market=Counting(MockProvider())
        model=ScriptedLLM([call('get_market_data'),call('calculate_technical_indicators',id='b'),call(id='c'),final('a','b','c')])
        agent=Agent(market,AgentSettings(agent_mode='mock'),model)
        result=await agent.chat(ChatRequest(message='分析QQQ'))
        assert result.status=='success' and market.calls==1
        evidence=result.report.evidence[0]
        data=await market.get(__import__('app.models',fromlist=['MarketQuery']).MarketQuery(symbol='QQQ',interval='1day'))
        prior=data['candles'][-21:-1]
        assert evidence['support_resistance']['support']==min(r['l'] for r in prior)
        assert evidence['support_resistance']['resistance']==max(r['h'] for r in prior)
    asyncio.run(execute())

def test_unknown_tool():
    response=run([call('arbitrary_shell'),final('a')])
    assert response.status=='failed' and response.tool_executions[0].status=='failed'

def test_model_failure():
    class Broken(ScriptedLLM):
        async def ainvoke(self,m): raise RuntimeError('secret-provider-message')
    async def execute():
        agent=Agent(MarketService(MockProvider()),AgentSettings(agent_mode='mock'),Broken())
        result=await agent.chat(ChatRequest(message='test'))
        assert result.status=='failed'
        assert 'secret' not in result.model_dump_json()
    asyncio.run(execute())

def test_entire_task_timeout():
    class Slow(ScriptedLLM):
        async def ainvoke(self,m):
            await asyncio.sleep(.1)
            return final('a')
    async def execute():
        agent=Agent(MarketService(MockProvider()),AgentSettings(agent_mode='mock',agent_timeout=.01),Slow())
        result=await agent.chat(ChatRequest(message='test'))
        assert result.status=='failed'
        assert 'agent_timeout_or_failure' in result.warnings
    asyncio.run(execute())

