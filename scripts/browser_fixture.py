"""Loopback-only deterministic acceptance backend; never imported by production.

Run from the repository root:
    .venv/Scripts/python.exe -m uvicorn browser_fixture:app --app-dir scripts \
        --host 127.0.0.1 --port 8139

POST /__test/scenario selects an explicit scripted response, not language routing.
Only use synthetic market data. No real API configuration is read by this app.
"""
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
import asyncio
import json
import sys

from fastapi import Request
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agents.llm import ScriptedLLM
from app.agents.schemas import AgentSettings
from app.main import create_app
from app.models import MarketError, Settings
from app.providers import MockProvider


class Scenario(str, Enum):
    normal = "normal"
    multi = "multi"
    followup = "followup"
    market_timeout = "market_timeout"
    invalid_parameters = "invalid_parameters"
    insufficient_history = "insufficient_history"
    empty_data = "empty_data"
    agent_failed = "agent_failed"
    delayed = "delayed"


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Scenario
    reset_observations: bool = False


def plan(scenario):
    """An explicit test oracle: never inspect user input to select a tool."""
    if scenario == Scenario.multi:
        calls = [("compare", "compare_timeframes", {
            "symbol": "QQQ", "asset_class": "stock", "intervals": ["1day", "1h"],
        })]
    elif scenario == Scenario.followup:
        calls = [("followup", "analyze_market_structure", {
            "symbol": "QQQ", "asset_class": "stock", "interval": "4h",
        })]
    else:
        calls = [
            ("market", "get_market_data", {"symbol": "QQQ", "interval": "1day"}),
            ("indicators", "calculate_technical_indicators", {"symbol": "QQQ", "interval": "1day"}),
            ("structure", "analyze_market_structure", {"symbol": "QQQ", "interval": "1day"}),
        ]
    responses = [
        AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])
        for call_id, name, args in calls
    ]
    responses.append(AIMessage(content=json.dumps({
        "evidence_ids": [call_id for call_id, _, _ in calls],
        "primary_timeframe": "4h" if scenario == Scenario.followup else "1day",
        "conclusion": "需要更多证据",
    }, ensure_ascii=False)))
    return responses


def create_fixture_app():
    state = {"scenario": Scenario.normal, "chat_requests": [], "market_requests": []}

    class FixtureProvider(MockProvider):
        async def fetch(self, query):
            rows = await super().fetch(query)
            if state["scenario"] == Scenario.insufficient_history:
                return rows[-25:]
            if state["scenario"] == Scenario.empty_data:
                return []
            return rows

    class FixtureLLM(ScriptedLLM):
        async def ainvoke(self, messages):
            if state["scenario"] == Scenario.agent_failed:
                # Agent's actual model exception path should create a failed report.
                raise RuntimeError("Acceptance fixture: model unavailable")
            return await super().ainvoke(messages)

    llm = FixtureLLM(plan(Scenario.normal))
    app = create_app(
        settings=Settings(market_provider="mock", twelvedata_api_key="", cache_ttl=60),
        provider=FixtureProvider(),
        agent_settings=AgentSettings(agent_mode="mock", deepseek_api_key=""),
        llm=llm,
    )
    app.title = "Trading-Analysis deterministic browser acceptance fixture"
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            original_get = app.state.market.get

            async def fixture_get(query):
                scenario = state["scenario"]
                state["market_requests"].append({
                    "symbol": query.symbol, "asset": query.asset,
                    "interval": query.interval, "scenario": scenario.value,
                })
                if scenario == Scenario.market_timeout:
                    raise MarketError("upstream_timeout", "验收测试桩：行情服务超时", 504)
                if scenario == Scenario.invalid_parameters:
                    raise MarketError("invalid_parameters", "验收测试桩：请求参数无效", 422)
                if scenario == Scenario.delayed and query.symbol == "QQQ" and query.interval == "1day":
                    # Delay before capturing data, then retain the selected query.
                    await asyncio.sleep(0.7)
                return await original_get(query)

            app.state.market.get = fixture_get
            yield

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def observe_requests(request: Request, call_next):
        # Reject accidental non-loopback deployment of this control surface.
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse(status_code=403, content={"error": "Acceptance fixture is loopback-only"})
        if request.method == "POST" and request.url.path == "/api/agent/chat":
            try:
                payload = await request.json()
                state["chat_requests"].append({
                    "thread_id": payload.get("thread_id"),
                    "message": payload.get("message"),
                    "scenario": state["scenario"].value,
                })
            except (ValueError, AttributeError):
                pass
        return await call_next(request)

    @app.post("/__test/scenario", include_in_schema=False)
    async def select_scenario(body: ScenarioRequest):
        state["scenario"] = body.scenario
        llm.responses = plan(body.scenario)
        app.state.market.cache.clear()
        if body.reset_observations:
            state["chat_requests"].clear()
            state["market_requests"].clear()
        return {"fixture": True, "scenario": body.scenario.value,
                "warning": "Explicit deterministic script; not natural-language understanding."}

    @app.get("/__test/observations", include_in_schema=False)
    async def observations():
        return {"fixture": True, "scenario": state["scenario"].value,
                "chat_requests": state["chat_requests"],
                "market_requests": state["market_requests"]}

    return app


app = create_fixture_app()
