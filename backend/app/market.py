import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from zoneinfo import ZoneInfo
from .models import MarketQuery
from .services.indicator_service import calculate

class MarketService:
    def __init__(self, provider, ttl=60):
        self.provider,self.ttl=provider,ttl
        self.cache={}
        self.lock=asyncio.Lock()
    async def candles(self,q):
        key=(q.symbol,q.asset,q.interval)
        # Single lock also coalesces concurrent requests; bounded cache avoids unbounded symbols.
        async with self.lock:
            cached=self.cache.get(key)
            if cached and monotonic()-cached[0]<self.ttl: return deepcopy(cached[1])
            rows=await self.provider.fetch(q)
            result=(rows,datetime.now(timezone.utc).isoformat())
            if len(self.cache)>=128: self.cache.pop(next(iter(self.cache)))
            self.cache[key]=(monotonic(),result)
            return deepcopy(result)
    async def get(self,q):
        rows,fetched=await self.candles(q)
        indicators=calculate(rows,q.asset)
        warnings=['最新K线可能尚未完成；信号包含该K线。']
        if self.provider.name=='mock': warnings.append('模拟历史数据，不是真实行情；未模拟交易所节假日。')
        if indicators['insufficient_history']: warnings.append('历史不足，MA200不可用。')
        if q.asset=='gold': warnings.append('黄金成交量不可用，不进行量能确认。')
        if (datetime.now(timezone.utc)-datetime.fromisoformat(rows[-1]['t'])).total_seconds()>4*86400:
            warnings.append('最新数据距今超过四天，请核对休市或数据延迟。')
        daily=rows
        if q.interval!='1day':
            try: daily,_=await self.candles(MarketQuery(symbol=q.symbol,asset=q.asset,interval='1day'))
            except Exception as exc:
                from .models import MarketError
                if not isinstance(exc,MarketError): raise
                daily=[]; warnings.append('日线不可用，PDH/PDL不可用。')
        zone=ZoneInfo('America/New_York') if q.asset=='stock' else timezone.utc
        target=datetime.fromisoformat(rows[-1]['t']).astimezone(zone).date()
        previous=[r for r in daily if datetime.fromisoformat(r['t']).astimezone(zone).date()<target]
        indicators['pdh']=previous[-1]['h'] if previous else None
        indicators['pdl']=previous[-1]['l'] if previous else None
        latest=indicators['latest']; rv=latest['relativeVolume']
        latest['volumeTrend']='unknown' if rv is None else 'increasing' if rv>1.1 else 'decreasing' if rv<.9 else 'flat'
        distances={k:'unknown' if v is None else 'far' if abs(v)>=3 else 'near' if abs(v)<=1 else 'normal' for k,v in latest['priceDistancePct'].items()}
        volatility='unknown' if latest['atr'] is None else 'high_volatility' if latest['atr']/latest['close']>.03 else 'normal'
        risk='high' if indicators['insufficient_history'] or volatility=='high_volatility' else 'medium' if latest['maAlignment']=='mixed' else 'low'
        rules=dict(trend=latest['trendDirection'],ma_structure=latest['maAlignment'],price_distance=distances,
                   volume_confirm=None if rv is None else rv>1.5,volatility=volatility,
                   key_levels={'pdh':indicators['pdh'],'pdl':indicators['pdl']},risk_level=risk,action_hint='wait' if risk=='high' else 'watch')
        return dict(symbol=q.symbol,assetClass=q.asset,interval=q.interval,source=self.provider.name,
                    last_updated=rows[-1]['t'],fetched_at=fetched,warnings=warnings,candles=rows,indicators=indicators,rules=rules)
