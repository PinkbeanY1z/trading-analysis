"""Exercise the exact deterministic fixture used during browser acceptance."""
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Settings
from app.agents.schemas import AgentSettings


def fixture_app():
    path = Path(__file__).resolve().parents[2] / "scripts" / "browser_fixture.py"
    spec = importlib.util.spec_from_file_location("browser_acceptance_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_fixture_app()


@pytest.fixture
def client():
    with TestClient(fixture_app()) as client:
        yield client


def scenario(client, name):
    response = client.post("/__test/scenario", json={"scenario": name})
    assert response.status_code == 200


def test_control_routes_absent_from_production():
    with TestClient(create_app(Settings(market_provider="mock"),
                               agent_settings=AgentSettings(agent_mode="mock"))) as client:
        assert client.get("/__test/observations").status_code == 404
        assert client.post("/__test/scenario", json={"scenario": "multi"}).status_code == 404


def test_multi_followup_and_observed_thread(client):
    scenario(client, "multi")
    first = client.post("/api/agent/chat", json={"message": "比较 QQQ 日线与1小时"}).json()
    assert first["status"] == "success"
    assert first["timeframes"] == ["1day", "1h"]
    assert first["tool_executions"][0]["name"] == "compare_timeframes"
    scenario(client, "followup")
    second = client.post("/api/agent/chat", json={
        "message": "那它的4小时呢？", "thread_id": first["thread_id"],
    }).json()
    assert second["thread_id"] == first["thread_id"]
    assert second["status"] == "success" and second["timeframes"] == ["4h"]
    observed = client.get("/__test/observations").json()
    assert observed["chat_requests"][-1]["thread_id"] == first["thread_id"]
    assert {item["interval"] for item in observed["market_requests"]} == {"1day", "1h", "4h"}


@pytest.mark.parametrize("name,status", [
    ("market_timeout", 504), ("invalid_parameters", 422), ("empty_data", 404),
])
def test_market_errors(client, name, status):
    scenario(client, name)
    response = client.get("/api/market/kline?symbol=QQQ&interval=1day")
    assert response.status_code == status
    assert "error" in response.json()


def test_insufficient_data_is_not_success(client):
    scenario(client, "insufficient_history")
    response = client.get("/api/market/kline?symbol=QQQ&interval=1day").json()
    assert len(response["candles"]) == 25
    assert response["indicators"]["latest"]["ma200"] is None
    result = client.post("/api/agent/chat", json={"message": "历史不足测试"}).json()
    assert result["status"] == "insufficient_data"


def test_agent_error_uses_real_exception_path(client):
    scenario(client, "agent_failed")
    response = client.post("/api/agent/chat", json={"message": "模型失败测试"})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["tool_executions"] == []


def test_scenario_is_validated(client):
    assert client.post("/__test/scenario", json={"scenario": "anything"}).status_code == 422


def test_gold_volume_is_unavailable(client):
    response = client.get("/api/market/kline?symbol=XAU%2FUSD&asset=gold&interval=4h").json()
    assert all(candle["v"] is None for candle in response["candles"])
    assert response["indicators"]["latest"]["relativeVolume"] is None
