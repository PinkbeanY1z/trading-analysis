from fastapi.testclient import TestClient
from app.main import create_app
from app.models import Settings
from app.agents.schemas import AgentSettings


def client():
    return TestClient(create_app(Settings(_env_file=None, market_provider='mock'),
                                agent_settings=AgentSettings(_env_file=None, agent_mode='mock')))


def test_calculator_api_does_not_fetch_market_or_invent_actual_win_rate():
    with client() as c:
        async def forbidden(*args):
            raise AssertionError('Hypothetical arithmetic must not fetch market data')
        c.app.state.market.get = forbidden
        result = c.post('/api/analysis/risk-reward', json={'side':'long','entry':690,'stop':680,'target':720})
        assert result.status_code == 200
        data = result.json()
        assert data['gross_reward_to_risk'] == 3
        assert data['breakeven_win_rate_percent'] == 25
        assert data['historical_win_rate'] is None
        assert data['costs']['all_costs_confirmed'] is False


def test_invalid_prices_use_safe_unified_error_without_echoing_input():
    with client() as c:
        result = c.post('/api/analysis/risk-reward', json={'side':'long','entry':690,'stop':700,'target':720,'secret':'do-not-echo'})
        assert result.status_code == 422
        assert set(result.json()) == {'error','code'}
        assert result.json()['code'] == 'invalid_risk_reward'
        assert 'do-not-echo' not in result.text


def test_costs_and_short_plan_follow_the_same_server_contract():
    with client() as c:
        result = c.post('/api/analysis/risk-reward', json={'side':'short','entry':690,'stop':700,'target':660,
                                                        'commission_per_unit':1,'spread_per_unit':0,'slippage_per_unit':0})
        assert result.status_code == 200
        data = result.json()
        assert data['net_risk'] == 11 and data['net_reward'] == 29
        assert data['costs']['all_costs_confirmed']
