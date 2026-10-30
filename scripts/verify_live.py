"""Opt-in live verification; prints sanitized status, never credentials."""
import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx
from app.models import Settings,MarketQuery
from app.providers import TwelveDataProvider
from app.market import MarketService
from app.agents.schemas import AgentSettings,ChatRequest
from app.agents.graph import Agent

async def main():
    settings=Settings(); config=AgentSettings(agent_mode='deepseek')
    results={'twelve_data':'SKIPPED','deepseek':'SKIPPED','cases':[]}
    async with httpx.AsyncClient(timeout=20) as client:
        market=MarketService(TwelveDataProvider(client,settings.twelvedata_api_key))
        if settings.twelvedata_api_key:
            results['twelve_data']='PASSED'
            for symbol,asset in [('QQQ','stock'),('XAU/USD','gold'),('AAPL','stock')]:
                for interval in ['1h','4h','1day']:
                    try:
                        d=await market.get(MarketQuery(symbol=symbol,asset=asset,interval=interval))
                        assert d['indicators']['latest']['ma200'] is not None
                        results['cases'].append({'symbol':symbol,'interval':interval,'status':'PASSED','last_updated':d['last_updated']})
                    except Exception:
                        results['twelve_data']='FAILED'
                        results['cases'].append({'symbol':symbol,'interval':interval,'status':'FAILED'})
        if config.deepseek_api_key and settings.twelvedata_api_key:
            agent=Agent(market,config); thread=None;results['deepseek']='PASSED'
            for index,message in enumerate(['分析 QQQ 日线趋势和 MA20、MA50、MA200。','那它的4小时呢？','比较 QQQ 日线和1小时趋势。','分析黄金的日线技术结构和 RSI。','QQQ 当前 RSI14 是多少？']):
                r=await agent.chat(ChatRequest(message=message,thread_id=thread if index==1 else None))
                if index==0: thread=r.thread_id
                ok=r.status=='success' and bool(r.tool_executions) and all(t.status=='success' for t in r.tool_executions)
                if not ok: results['deepseek']='FAILED'
                results['cases'].append({'task':index,'status':'PASSED' if ok else 'FAILED','symbol':r.symbol,'timeframes':r.timeframes,'tools':[t.name for t in r.tool_executions]})
    print(json.dumps(results,ensure_ascii=False,indent=2))
    return 1 if 'FAILED' in (results['twelve_data'],results['deepseek']) else 0
if __name__=='__main__': sys.exit(asyncio.run(main()))
