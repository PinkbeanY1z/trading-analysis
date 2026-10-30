from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from app.main import create_app
from app.models import Settings
from app.agents.schemas import AgentSettings
from app.agents.llm import ScriptedLLM
import json

def test_multi_turn_api_chart_evidence():
    def call(id,name,args): return AIMessage(content='',tool_calls=[dict(id=id,name=name,args=args)])
    def final(id): return AIMessage(content=json.dumps({'evidence_ids':[id],'conclusion':'需要更多证据'}))
    llm=ScriptedLLM([call('a','compare_timeframes',{'symbol':'QQQ','intervals':['1day','1h']}),final('a'),call('b','analyze_market_structure',{'symbol':'QQQ','interval':'4h'}),final('b')])
    with TestClient(create_app(Settings(market_provider='mock'),agent_settings=AgentSettings(agent_mode='mock'),llm=llm)) as c:
        a=c.post('/api/agent/chat',json={'message':'比较QQQ日线与1小时'}).json()
        assert a['status']=='success' and a['timeframes']==['1day','1h']
        b=c.post('/api/agent/chat',json={'message':'那它的4小时呢？','thread_id':a['thread_id']}).json()
        assert b['thread_id']==a['thread_id'] and b['timeframes']==['4h']
        for report in (a,b):
            for e in report['report']['evidence']:
                d=c.get('/api/market/kline',params={'symbol':e['symbol'],'interval':e['interval']}).json()
                assert d['last_updated']==e['last_updated']
                assert d['indicators']['latest']==e['technical_indicators']
