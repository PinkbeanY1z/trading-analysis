import asyncio
from app.market import MarketService
from app.providers import MockProvider
from app.models import MarketQuery,MarketError
from app.agents.tools import compact
import pytest

def test_revision_changes_fingerprint_with_same_timestamp():
    async def run():
        p=MockProvider(); service=MarketService(p,ttl=0); q=MarketQuery(symbol='QQQ',interval='1day')
        first=await service.get(q)
        original=p.fetch
        async def revised(query):
            rows=await original(query); rows[-1]['h']+=1
            return rows
        p.fetch=revised
        second=await service.get(q)
        assert first['last_updated']==second['last_updated']
        assert first['snapshot_id']!=second['snapshot_id']
        assert compact(first)['snapshot_id']==first['snapshot_id']
        assert compact(first)['asset_class']=='stock'
    asyncio.run(run())

def test_empty_provider_returns_domain_error():
    class Empty(MockProvider):
        async def fetch(self,q): return []
    with pytest.raises(MarketError) as e: asyncio.run(MarketService(Empty()).get(MarketQuery(symbol='QQQ')))
    assert e.value.code=='empty_data'
