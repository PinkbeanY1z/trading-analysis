import asyncio
from contextvars import ContextVar
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from langchain_core.tools import StructuredTool
from ..models import MarketQuery, MarketError

RUN=ContextVar('agent_run')
class Args(BaseModel):
    model_config=ConfigDict(extra='forbid')
    symbol: str=Field(min_length=1,max_length=20)
    asset_class: Literal['stock','gold']='stock'
    interval: Literal['1h','4h','1day']='1day'
class DataArgs(Args):
    limit: int=Field(5,ge=1,le=20)
class CompareArgs(BaseModel):
    model_config=ConfigDict(extra='forbid')
    symbol: str=Field(min_length=1,max_length=20)
    asset_class: Literal['stock','gold']='stock'
    intervals: list[Literal['1h','4h','1day']]=Field(min_length=2,max_length=3)

async def snapshot(symbol,asset_class,interval):
    run=RUN.get(); q=MarketQuery(symbol=symbol,asset=asset_class,interval=interval)
    key=(q.symbol,q.asset,q.interval)
    async with run['lock']:
        if key not in run['cache']:
            value=await run['market'].get(q)
            if not value['candles']: raise MarketError('empty_data','无行情',404)
            run['cache'][key]=value
        return run['cache'][key]

def compact(d):
    rows=d['candles']; prior=rows[-21:-1]
    return dict(symbol=d['symbol'],asset_class=d['assetClass'],interval=d['interval'],
                source=d['source'],last_updated=d['last_updated'],snapshot_id=d['snapshot_id'],
                warnings=d['warnings'],insufficient_history=d['indicators']['insufficient_history'],
                technical_indicators=d['indicators']['latest'],market_structure=d['rules'],
                support_resistance={'support':min(r['l'] for r in prior) if len(prior)==20 else None,
                                    'resistance':max(r['h'] for r in prior) if len(prior)==20 else None,
                                    'rule':'前20根K线（不含当前）最低/最高价'})

def build_tools():
    async def get_market_data(symbol,asset_class='stock',interval='1day',limit=5):
        d=await snapshot(symbol,asset_class,interval)
        return {**compact(d),'ohlcv':d['candles'][-limit:],'available_bars':len(d['candles'])}
    async def calculate_technical_indicators(symbol,asset_class='stock',interval='1day'):
        return compact(await snapshot(symbol,asset_class,interval))
    async def analyze_market_structure(symbol,asset_class='stock',interval='1day'):
        return compact(await snapshot(symbol,asset_class,interval))
    async def compare_timeframes(symbol,asset_class='stock',intervals=None):
        if len(set(intervals))<2: raise ValueError('distinct intervals required')
        values=[compact(await snapshot(symbol,asset_class,i)) for i in intervals]
        trends=[v['market_structure']['trend'] for v in values]
        return {'symbol':values[0]['symbol'],'timeframes':values,'alignment':'unknown' if 'unknown' in trends else 'aligned' if len(set(trends))==1 else 'divergent'}
    return [StructuredTool.from_function(coroutine=fn,name=fn.__name__,args_schema=schema,description=desc) for fn,schema,desc in [
        (get_market_data,DataArgs,'查询真实OHLCV及来源，最多返回20根，历史计算使用完整数据。'),
        (calculate_technical_indicators,Args,'确定性计算均线、RSI、ATR、相对成交量、斜率和偏离率。'),
        (analyze_market_structure,Args,'分析趋势、量价、前20根支撑阻力和风险，缺失数据保持null。'),
        (compare_timeframes,CompareArgs,'比较同一品种多个周期的确定性结构和分歧。')]]
