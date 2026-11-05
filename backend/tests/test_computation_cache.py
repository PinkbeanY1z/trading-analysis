"""Market derivations share raw/daily dependencies without leaking mutable state."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

import app.market as market_module
from app.market import MarketService
from app.models import MarketError, MarketQuery
from app.providers import MockProvider


class MutableProvider(MockProvider):
    def __init__(self):
        self.requests = []
        self.revision = 0
        self.daily_revision = 0
        self.fail_market = False
        self.fail_daily = False

    async def fetch(self, query):
        self.requests.append(query.interval)
        await asyncio.sleep(0)
        if self.fail_market or (self.fail_daily and query.interval == '1day'):
            raise MarketError('upstream_timeout', '测试上游超时', 504)
        rows = await super().fetch(query)
        revision = self.daily_revision if query.interval == '1day' else self.revision
        for row in rows:
            row['h'] += revision
            row['l'] -= revision
        return rows


@pytest.fixture
def computations(monkeypatch):
    counts = {'calculate': 0, 'structure': 0}
    original_calculate, original_structure = market_module.calculate, market_module.structure

    def calculate(*arguments, **keywords):
        counts['calculate'] += 1
        return original_calculate(*arguments, **keywords)

    def structure(*arguments, **keywords):
        counts['structure'] += 1
        return original_structure(*arguments, **keywords)

    monkeypatch.setattr(market_module, 'calculate', calculate)
    monkeypatch.setattr(market_module, 'structure', structure)
    return counts


def test_agent_then_chart_request_reuses_actual_calculations(computations):
    async def run():
        provider = MutableProvider()
        market = MarketService(provider)
        query = MarketQuery(symbol='QQQ', interval='4h')
        agent = await market.get(query)
        chart = await market.get(query)
        assert agent == chart and agent is not chart
        assert computations == {'calculate': 1, 'structure': 1}
        assert provider.requests == ['4h', '1day']
    asyncio.run(run())


def test_concurrent_requests_merge_one_derived_calculation(computations):
    async def run():
        provider = MutableProvider()
        market = MarketService(provider)
        responses = await asyncio.gather(*[market.get(MarketQuery(symbol='QQQ', interval='1h')) for _ in range(12)])
        assert computations == {'calculate': 1, 'structure': 1}
        assert provider.requests == ['1h', '1day']
        assert all(response == responses[0] for response in responses)
        assert len({id(response) for response in responses}) == len(responses)
    asyncio.run(run())


def test_mutating_returned_graph_data_does_not_change_cached_response(computations):
    async def run():
        market = MarketService(MutableProvider())
        query = MarketQuery(symbol='QQQ', interval='1day')
        first = await market.get(query)
        original = deepcopy(first)
        first['candles'][-1]['c'] = 999999
        first['indicators']['latest']['rsi'] = -1
        first['indicators']['ma20'][-1] = -1
        first['rules']['key_levels']['pdh'] = -1
        first['warnings'].append('caller mutation')
        second = await market.get(query)
        assert second == original
        assert computations == {'calculate': 1, 'structure': 1}
    asyncio.run(run())


def test_expired_raw_snapshot_revision_invalidates_derived_cache(monkeypatch, computations):
    clock = [0]
    monkeypatch.setattr(market_module, 'monotonic', lambda: clock[0])

    async def run():
        provider = MutableProvider()
        market = MarketService(provider, ttl=60)
        query = MarketQuery(symbol='QQQ', interval='4h')
        first = await market.get(query)
        clock[0] = 61
        provider.revision = 3
        second = await market.get(query)
        assert first['last_updated'] == second['last_updated']
        assert first['snapshot_id'] != second['snapshot_id']
        assert computations == {'calculate': 2, 'structure': 2}
        assert provider.requests.count('4h') == 2
    asyncio.run(run())


def test_daily_refresh_invalidates_intraday_pdh_without_refetching_intraday(computations):
    async def run():
        provider = MutableProvider()
        market = MarketService(provider)
        query = MarketQuery(symbol='QQQ', interval='4h')
        first = await market.get(query)
        daily_key = ('QQQ', 'stock', '1day')
        market.cache.pop(daily_key)
        provider.daily_revision = 4
        second = await market.get(query)
        assert first['candles'] == second['candles']
        assert first['fetched_at'] == second['fetched_at']
        assert second['indicators']['pdh'] == first['indicators']['pdh'] + 4
        assert second['indicators']['pdl'] == first['indicators']['pdl'] - 4
        assert first['snapshot_id'] != second['snapshot_id']
        assert computations == {'calculate': 2, 'structure': 2}
        assert provider.requests.count('4h') == 1 and provider.requests.count('1day') == 2
    asyncio.run(run())


def test_changed_daily_fetched_identity_invalidates_even_when_prices_match(computations):
    async def run():
        provider = MutableProvider()
        market = MarketService(provider)
        query = MarketQuery(symbol='QQQ', interval='4h')
        first = await market.get(query)
        market.cache.pop(('QQQ', 'stock', '1day'))
        second = await market.get(query)
        # Fetched identity is a dependency even if the provider returned the
        # exact same rows. Public snapshot IDs still depend only on their data.
        assert first == second
        assert computations == {'calculate': 2, 'structure': 2}
    asyncio.run(run())


@pytest.mark.parametrize('interval', ['4h', '1day'])
def test_source_content_revision_with_preserved_fetch_identity_invalidates(interval, computations):
    async def run():
        market = MarketService(MutableProvider())
        query = MarketQuery(symbol='QQQ', interval=interval)
        first = await market.get(query)
        key = ('QQQ', 'stock', interval)
        _, (rows, _) = market.cache[key]
        rows[-1]['h'] += 2
        second = await market.get(query)
        assert first['fetched_at'] == second['fetched_at']
        assert first['snapshot_id'] != second['snapshot_id']
        assert computations == {'calculate': 2, 'structure': 2}
    asyncio.run(run())


def test_source_cache_clear_does_not_reuse_old_derived_object(computations):
    async def run():
        provider = MutableProvider()
        market = MarketService(provider)
        query = MarketQuery(symbol='QQQ', interval='4h')
        first = await market.get(query)
        market.cache.clear()
        provider.revision = 1
        second = await market.get(query)
        assert first['snapshot_id'] != second['snapshot_id']
        assert computations == {'calculate': 2, 'structure': 2}
    asyncio.run(run())


def test_daily_content_revision_with_same_fetch_identity_updates_pdh(computations):
    async def run():
        market = MarketService(MutableProvider())
        query = MarketQuery(symbol='QQQ', interval='4h')
        first = await market.get(query)
        _, (daily, _) = market.cache[('QQQ', 'stock', '1day')]
        for row in daily:
            row['h'] += 3
        second = await market.get(query)
        assert first['fetched_at'] == second['fetched_at']
        assert first['candles'] == second['candles']
        assert second['indicators']['pdh'] == first['indicators']['pdh'] + 3
        assert first['snapshot_id'] != second['snapshot_id']
        assert computations == {'calculate': 2, 'structure': 2}
    asyncio.run(run())


def test_staleness_warning_updates_without_waiting_for_raw_ttl(monkeypatch, computations):
    current = [None]

    class ControlledDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]

    async def run():
        query = MarketQuery(symbol='QQQ', interval='1day')
        rows = await MockProvider().fetch(query)
        current[0] = datetime.fromisoformat(rows[-1]['t']) + timedelta(days=4, seconds=-1)
        monkeypatch.setattr(market_module, 'datetime', ControlledDateTime)
        market = MarketService(MutableProvider())
        first = await market.get(query)
        current[0] += timedelta(seconds=2)
        second = await market.get(query)
        warning = '最新数据距今超过四天，请核对休市或数据延迟。'
        assert warning not in first['warnings'] and warning in second['warnings']
        assert first['fetched_at'] == second['fetched_at']
        assert first['snapshot_id'] == second['snapshot_id']
        assert computations == {'calculate': 2, 'structure': 2}
    asyncio.run(run())


def test_raw_fetch_failure_does_not_return_previously_cached_derived_success(computations):
    async def run():
        provider = MutableProvider()
        market = MarketService(provider)
        query = MarketQuery(symbol='QQQ', interval='4h')
        await market.get(query)
        market.cache.clear()
        provider.fail_market = True
        with pytest.raises(MarketError, match='测试上游超时'):
            await market.get(query)
        assert computations == {'calculate': 1, 'structure': 1}
    asyncio.run(run())


def test_daily_dependency_failure_is_not_cached_and_recovers(computations):
    async def run():
        provider = MutableProvider()
        provider.fail_daily = True
        market = MarketService(provider)
        query = MarketQuery(symbol='QQQ', interval='4h')
        first = await market.get(query)
        second = await market.get(query)
        assert first['indicators']['pdh'] is None and second['indicators']['pdl'] is None
        assert '日线不可用，PDH/PDL不可用。' in second['warnings']
        assert not market.derived_cache
        assert computations == {'calculate': 2, 'structure': 2}
        provider.fail_daily = False
        recovered = await market.get(query)
        again = await market.get(query)
        assert recovered == again and recovered['indicators']['pdh'] is not None
        assert '日线不可用，PDH/PDL不可用。' not in recovered['warnings']
        assert computations == {'calculate': 3, 'structure': 3}
    asyncio.run(run())


def test_calculation_failure_is_not_inserted_into_cache(monkeypatch, computations):
    original = market_module.structure
    failure = [True]

    def structure(*arguments, **keywords):
        if failure[0]:
            raise ValueError('controlled structure failure')
        return original(*arguments, **keywords)

    monkeypatch.setattr(market_module, 'structure', structure)

    async def run():
        market = MarketService(MutableProvider())
        query = MarketQuery(symbol='QQQ', interval='1day')
        with pytest.raises(ValueError, match='controlled structure failure'):
            await market.get(query)
        assert not market.derived_cache
        failure[0] = False
        assert (await market.get(query))['snapshot_id']
        assert computations['calculate'] == 2
    asyncio.run(run())


def test_disabled_ttl_never_reuses_derived_calculations(computations):
    async def run():
        provider = MutableProvider()
        market = MarketService(provider, ttl=0)
        query = MarketQuery(symbol='QQQ', interval='1day')
        await market.get(query)
        await market.get(query)
        assert computations == {'calculate': 2, 'structure': 2}
        assert provider.requests == ['1day', '1day']
        assert not market.derived_cache
    asyncio.run(run())


def test_derived_cache_has_same_bounded_capacity_as_raw_cache():
    async def run():
        market = MarketService(MutableProvider())
        for index in range(130):
            await market.get(MarketQuery(symbol=f'TEST{index}', interval='1day'))
        assert len(market.cache) == len(market.derived_cache) == 128
    asyncio.run(run())
