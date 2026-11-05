"""Distinct public tool projections; canonical audit evidence is kept server-side."""
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..services.trade_plan_service import RiskRewardResult


Interval = Literal['1h', '4h', '1day']
Metric = Literal['ma7', 'ma20', 'ma25', 'ma50', 'ma60', 'ma200', 'rsi', 'atr', 'relativeVolume', 'slopes', 'priceDistancePct', 'volume']
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Args(StrictModel):
    symbol: str = Field(min_length=1, max_length=20)
    asset_class: Literal['stock', 'gold'] = 'stock'
    interval: Interval = '1day'


class DataArgs(Args):
    limit: int = Field(5, ge=1, le=20)


class IndicatorArgs(Args):
    metrics: list[Metric] = Field(default_factory=lambda: ['ma20', 'ma50', 'ma200', 'rsi', 'atr'], min_length=1, max_length=12)

    @field_validator('metrics')
    @classmethod
    def distinct_metrics(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('metrics must be distinct')
        return value


class CompareArgs(StrictModel):
    symbol: str = Field(min_length=1, max_length=20)
    asset_class: Literal['stock', 'gold'] = 'stock'
    intervals: list[Interval] = Field(min_length=2, max_length=3)

    @field_validator('intervals')
    @classmethod
    def distinct_intervals(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('intervals must be distinct')
        return value


class TradePlanArgs(StrictModel):
    mode: Literal['evaluate', 'candidate'] = Field(description='evaluate only explicitly user-supplied numbers; candidate inspects real structure and may find no acceptable hypothesis')
    side: Literal['long', 'short'] = 'long'
    entry: Annotated[Number, Field(gt=0)] | None = None
    stop: Annotated[Number, Field(gt=0)] | None = None
    target: Annotated[Number, Field(gt=0)] | None = None
    commission_per_unit: Annotated[Number, Field(ge=0)] | None = Field(None, description='Round-trip commission in price currency per unit; omitted means unconfirmed')
    spread_per_unit: Annotated[Number, Field(ge=0)] | None = Field(None, description='Round-trip spread cost per unit; omitted means unconfirmed')
    slippage_per_unit: Annotated[Number, Field(ge=0)] | None = Field(None, description='Round-trip slippage cost per unit; omitted means unconfirmed')
    atr: Annotated[Number, Field(gt=0)] | None = Field(None, description='Only explicitly supplied by user in evaluate mode; candidate reads actual Python ATR')
    symbol: str | None = Field(None, min_length=1, max_length=20)
    asset_class: Literal['stock', 'gold'] = 'stock'
    interval: Interval = '1day'


class MarketMeta(StrictModel):
    symbol: str
    asset_class: Literal['stock', 'gold']
    interval: Interval
    source: str
    last_updated: str
    snapshot_id: str
    warnings: list[str]
    insufficient_history: bool


class OHLCV(StrictModel):
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float | None = None


class MarketDataResult(MarketMeta):
    kind: Literal['market_data'] = 'market_data'
    latest_close: float
    available_bars: int
    ohlcv: list[OHLCV]


class IndicatorResult(MarketMeta):
    kind: Literal['technical_indicators'] = 'technical_indicators'
    technical_indicators: dict[Metric, float | dict[str, float | None] | None]


class RollingLevel(StrictModel):
    price: float | None
    status: Literal['broken', 'crossed', 'potential', 'unconfirmed']
    evidence_type: Literal['rolling_extreme'] = 'rolling_extreme'
    confirmed_support_or_resistance: Literal[False] = False


class RollingLevels(StrictModel):
    lower: RollingLevel
    upper: RollingLevel
    lookback: Literal[20] = 20
    excludes_current_bar: Literal[True] = True
    method: str = '前20根K线（不含当前）的最低/最高价；不是多次测试确认的支撑阻力'


class EntryAssessment(StrictModel):
    long: Literal['not_supported', 'unconfirmed']
    short: Literal['not_supported', 'unconfirmed']
    reasons: list[str]


class StructureAnalysis(StrictModel):
    trend: Literal['up', 'down', 'flat', 'unknown']
    ma_alignment: Literal['bullish', 'bearish', 'mixed', 'unknown']
    price_positions: dict[str, Literal['above', 'below', 'at'] | None]
    rsi_state: Literal['oversold', 'overbought', 'neutral', 'unavailable']
    rsi_momentum: float | None
    rolling_levels: RollingLevels
    entry_assessment: EntryAssessment
    observations: list[str]
    confirmation: str = '最新K线完成状态未确认；结构信号不是交易入场确认'


class StructureResult(MarketMeta):
    kind: Literal['market_structure'] = 'market_structure'
    analysis: StructureAnalysis


class ComparisonResult(StrictModel):
    kind: Literal['timeframe_comparison'] = 'timeframe_comparison'
    symbol: str
    asset_class: Literal['stock', 'gold']
    timeframes: list[StructureResult]
    alignment: Literal['unknown', 'aligned', 'divergent']


class CalculationResult(StrictModel):
    kind: Literal['calculation'] = 'calculation'
    source: Literal['user_assumption'] = 'user_assumption'
    symbol: str | None = None
    asset_class: Literal['stock', 'gold'] | None = None
    interval: None = None
    calculation_id: str
    calculation: RiskRewardResult


class CandidateAssessment(StrictModel):
    available: bool
    hypothetical: Literal[True] = True
    reasons: list[str]
    method: str
    structural_confirmation: Literal['unconfirmed'] = 'unconfirmed'
    historical_win_rate: None = None
    historical_win_rate_status: Literal['unverified'] = 'unverified'
    anchors: list[dict[str, Any]] = Field(default_factory=list)
    calculation_id: str | None = None
    calculation: RiskRewardResult | None = None


class CandidateResult(MarketMeta):
    kind: Literal['trade_plan'] = 'trade_plan'
    trade_plan: CandidateAssessment
