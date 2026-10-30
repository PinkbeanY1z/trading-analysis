from contextlib import asynccontextmanager
from typing import Annotated
import httpx
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from .models import Settings, MarketQuery, MarketResponse, ErrorResponse, MarketError
from .providers import TwelveDataProvider, MockProvider
from .market import MarketService

from .agents.graph import Agent
from .agents.schemas import ChatRequest, ChatResponse

def create_app(settings=None, provider=None, agent_settings=None, llm=None):
    settings=settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=settings.upstream_timeout) as client:
            selected=provider or (MockProvider() if settings.market_provider=='mock' else TwelveDataProvider(client,settings.twelvedata_api_key))
            app.state.market=MarketService(selected,settings.cache_ttl)
            app.state.agent=Agent(app.state.market,agent_settings,llm)
            yield
    app=FastAPI(title='Trading-Analysis 行情结构分析',lifespan=lifespan)
    app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_methods=['GET','POST'],allow_headers=['Content-Type'])
    @app.exception_handler(MarketError)
    async def market_error(request,exc):
        return JSONResponse(status_code=exc.status,content={'error':exc.message,'code':exc.code})
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return JSONResponse(status_code=422,content={'error':'请求参数无效，请检查 symbol、asset 和 interval','code':'invalid_parameters'})
    @app.exception_handler(Exception)
    async def unexpected_error(request,exc):
        return JSONResponse(status_code=500,content={'error':'服务内部错误','code':'internal_error'})
    @app.get('/health')
    async def health():
        return {'status':'ok','provider':app.state.market.provider.name}
    async def market(query: Annotated[MarketQuery,Query()]):
        return await app.state.market.get(query)
    for path in ['/api/market/kline','/api/market/indicators','/api/analysis/rules']:
        app.add_api_route(path,market,methods=['GET'],response_model=MarketResponse,
                         responses={s:{'model':ErrorResponse} for s in (404,422,429,500,502,503,504)})
    @app.post('/api/agent/chat',response_model=ChatResponse)
    async def chat(body: ChatRequest):
        return await app.state.agent.chat(body)
    return app

app=create_app()
