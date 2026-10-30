from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1]/'.env', extra='ignore')
    market_provider: Literal['mock','twelve_data'] = 'twelve_data'
    twelvedata_api_key: str = ''
    cors_origins: list[str] = ['http://localhost:5173','http://127.0.0.1:5173']
    cache_ttl: float = Field(60, ge=0)
    upstream_timeout: float = Field(20, gt=0)

class MarketQuery(BaseModel):
    symbol: str = Field(min_length=1, max_length=20, pattern=r'^[A-Z][A-Z0-9./-]*$')
    asset: Literal['stock','gold'] = 'stock'
    interval: Literal['1h','4h','1day'] = '1h'
    @field_validator('symbol', mode='before')
    @classmethod
    def normalize(cls, value):
        return value.strip().upper() if isinstance(value,str) else value
    @model_validator(mode='after')
    def check_asset(self):
        if self.symbol == 'XAUUSD': self.symbol = 'XAU/USD'
        if (self.symbol == 'XAU/USD') != (self.asset == 'gold'):
            raise ValueError('XAU/USD requires asset=gold; stock symbols require asset=stock')
        if self.asset == 'stock' and '/' in self.symbol:
            raise ValueError('unsupported stock symbol')
        return self

class Candle(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    t: str
    o: float = Field(gt=0)
    h: float = Field(gt=0)
    l: float = Field(gt=0)
    c: float = Field(gt=0)
    v: float | None = Field(default=None, ge=0)
    @model_validator(mode='after')
    def ohlc(self):
        if self.l > min(self.o,self.c) or self.h < max(self.o,self.c) or self.l > self.h:
            raise ValueError('invalid OHLC')
        return self

class MarketResponse(BaseModel):
    symbol: str
    assetClass: str
    interval: str
    timezone: str = 'UTC'
    currency: str = 'USD'
    source: str
    last_updated: str
    fetched_at: str
    warnings: list[str]
    candles: list[Candle]
    indicators: dict
    rules: dict

class ErrorResponse(BaseModel):
    error: str
    code: str

class MarketError(Exception):
    def __init__(self, code, message, status=502):
        self.code, self.message, self.status = code, message, status
