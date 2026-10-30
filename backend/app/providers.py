import asyncio
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo
import httpx
from pydantic import ValidationError
from .models import Candle, MarketError

UTC = timezone.utc

class Provider(Protocol):
    name: str
    async def fetch(self, query) -> list[dict]: ...

class TwelveDataProvider:
    name = 'twelve_data'
    def __init__(self, client, key):
        self.client, self.key = client, key
    async def fetch(self, query):
        if not self.key:
            raise MarketError('missing_key','TWELVEDATA_API_KEY 未配置',503)
        for attempt in range(2):
            try:
                response = await self.client.get('https://api.twelvedata.com/time_series', params={
                    'symbol':query.symbol,'interval':query.interval,'outputsize':350,
                    'timezone':'UTC','prepost':'false','apikey':self.key})
                if response.status_code == 429:
                    raise MarketError('rate_limit','行情服务额度受限',429)
                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                if response.status_code != 200:
                    raise MarketError('upstream_http','行情服务请求失败')
                data = response.json()
                if data.get('status') == 'error':
                    code = data.get('code')
                    raise MarketError('rate_limit' if code == 429 else 'upstream_error',
                                      '行情服务拒绝请求，请检查品种、权限或额度',429 if code == 429 else 502)
                return self.normalize(data,query)
            except httpx.TimeoutException:
                if attempt: raise MarketError('upstream_timeout','行情服务超时',504) from None
            except httpx.RequestError:
                if attempt: raise MarketError('upstream_network','行情服务连接失败',502) from None
            except (ValueError, TypeError, KeyError, AttributeError):
                raise MarketError('invalid_data','行情服务返回无效数据') from None
        raise MarketError('upstream_error','行情服务不可用')

    @staticmethod
    def normalize(data,query):
        values = data.get('values')
        if not values:
            raise MarketError('empty_data','没有可用行情',404)
        zone = ZoneInfo(data.get('meta',{}).get('exchange_timezone') or ('America/New_York' if query.asset == 'stock' else 'UTC'))
        rows = {}
        for row in values:
            stamp = datetime.fromisoformat(row['datetime'])
            # Daily dates are exchange-local session dates, never arbitrary UTC dates.
            stamp = stamp.replace(tzinfo=zone if query.interval == '1day' else UTC) if stamp.tzinfo is None else stamp
            stamp = stamp.astimezone(UTC).isoformat()
            volume = row.get('volume')
            candle = Candle(t=stamp,o=row['open'],h=row['high'],l=row['low'],c=row['close'],
                            v=None if query.asset == 'gold' or volume in (None,'') else volume)
            value = candle.model_dump()
            if stamp in rows and rows[stamp] != value:
                raise ValueError('conflicting duplicate')
            rows[stamp] = value
        return [rows[t] for t in sorted(rows)]

class MockProvider:
    name = 'mock'
    async def fetch(self, query):
        rows=[]
        # Explicit synthetic weekday sessions; no claim of exchange holiday accuracy.
        day=datetime(2024,1,1,tzinfo=ZoneInfo('America/New_York'))
        while len(rows)<360:
            if day.weekday()<5:
                hours = [(0,0)] if query.interval=='1day' else [(9,30),(13,30)] if query.interval=='4h' else [(h,30) for h in range(9,16)]
                for h,m in hours:
                    n=len(rows); price=(2000 if query.asset=='gold' else 400)+n*.1
                    rows.append(dict(t=day.replace(hour=h,minute=m).astimezone(UTC).isoformat(),o=price,h=price+2,l=price-2,c=price+1,v=None if query.asset=='gold' else 1000+n))
            day+=timedelta(days=1)
        return rows[-350:]
