import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.models import Settings, MarketQuery, MarketError
from app.providers import MockProvider, TwelveDataProvider
from app.market import MarketService

@pytest.fixture
def client():
    with TestClient(create_app(Settings(market_provider='mock'))) as c: yield c

def test_health(client):
    assert client.get('/health').json()=={'status':'ok','provider':'mock'}

@pytest.mark.parametrize('path',['market/kline','market/indicators','analysis/rules'])
@pytest.mark.parametrize('interval',['1h','4h','1day'])
def test_endpoints(client,path,interval):
    response=client.get('/api/'+path,params={'symbol':'QQQ','interval':interval})
    assert response.status_code==200,response.text
    data=response.json()
    assert len(data['candles'])==350
    assert data['indicators']['latest']['ma200'] is not None
    assert len(data['indicators']['rsi'])==len(data['candles'])
    assert data['rules']['key_levels']['pdh'] is not None
    assert data['source']=='mock'

@pytest.mark.parametrize('params',[{'symbol':'???'},{'symbol':'QQQ','interval':'2h'},{'symbol':'QQQ','asset':'gold'},{'symbol':'XAU/USD'},{}])
def test_validation(client,params):
    response=client.get('/api/market/kline',params=params)
    assert response.status_code==422
    assert response.json()['code']=='invalid_parameters'

def test_gold(client):
    data=client.get('/api/market/kline',params={'symbol':'XAUUSD','asset':'gold'}).json()
    assert data['symbol']=='XAU/USD'
    assert all(r['v'] is None for r in data['candles'])
    assert data['rules']['volume_confirm'] is None

def test_no_key():
    with TestClient(create_app(Settings(market_provider='twelve_data',twelvedata_api_key=''))) as c:
        assert c.get('/api/market/kline?symbol=QQQ').status_code==503

def test_cache():
    class Counting(MockProvider):
        calls=0
        async def fetch(self,q):
            self.calls+=1
            return await super().fetch(q)
    async def run():
        p=Counting(); service=MarketService(p)
        q=MarketQuery(symbol='QQQ')
        results=await asyncio.gather(*(service.get(q) for _ in range(8)))
        assert p.calls==2 # one intraday and one daily across all endpoints
        results[0]['candles'][0]['c']=0
        assert (await service.get(q))['candles'][0]['c']>0
        service.ttl=0
        await service.get(q)
        assert p.calls==4
    asyncio.run(run())

def run_provider(handler,query=None,key='test'):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await TwelveDataProvider(c,key).fetch(query or MarketQuery(symbol='QQQ'))
    return asyncio.run(run())

def test_provider_native_period():
    def handler(request):
        assert request.url.params['interval']=='4h'
        assert request.url.params['prepost']=='false'
        return httpx.Response(200,json={'values':[
            {'datetime':t,'open':'10','high':'12','low':'9','close':'11'}
            for t in ['2026-03-09 17:30:00','2026-03-09 13:30:00','2026-03-09 13:30:00']]})
    rows=run_provider(handler,MarketQuery(symbol='QQQ',interval='4h'))
    assert len(rows)==2 and rows[0]['t']<rows[1]['t']
    assert rows[0]['v'] is None

@pytest.mark.parametrize('date,hour',[('2026-01-05','05:00'),('2026-07-06','04:00')])
def test_daily_timezone(date,hour):
    rows=run_provider(lambda r:httpx.Response(200,json={'values':[{'datetime':date,'open':10,'high':12,'low':9,'close':11}]}),MarketQuery(symbol='QQQ',interval='1day'))
    assert hour in rows[0]['t']

@pytest.mark.parametrize('case,status,code,count', [('timeout',504,'upstream_timeout',2),('rate',429,'rate_limit',1),('empty',404,'empty_data',1),('json',502,'invalid_data',1),('server',502,'upstream_http',2)])
def test_upstream_errors(case,status,code,count):
    calls=[]
    def handler(request):
        calls.append(1)
        if case=='timeout': raise httpx.ReadTimeout('secret-url',request=request)
        if case=='rate': return httpx.Response(429)
        if case=='server': return httpx.Response(500)
        if case=='json': return httpx.Response(200,text='bad json')
        return httpx.Response(200,json={'values':[]})
    with pytest.raises(MarketError) as exc: run_provider(handler)
    assert exc.value.status==status and exc.value.code==code
    assert len(calls)==count
    assert 'secret' not in exc.value.message

def test_api_timeout():
    class Failed:
        name='test'
        async def fetch(self,q): raise MarketError('upstream_timeout','行情服务超时',504)
    with TestClient(create_app(provider=Failed())) as c:
        response=c.get('/api/market/kline?symbol=QQQ')
        assert response.status_code==504
        assert response.json()=={'error':'行情服务超时','code':'upstream_timeout'}
