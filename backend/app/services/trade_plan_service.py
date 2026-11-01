"""Deterministic evaluation of explicit, hypothetical trade prices.

All costs are round-trip amounts per unit in the same quote currency as price.
This calculator does not choose price levels, confirm a support/stop, retrieve
market data, or estimate a historical win rate.
"""
from decimal import Decimal, localcontext
from math import isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator, model_validator


PositiveNumber = Annotated[Decimal, Field(gt=0, allow_inf_nan=False),
                           WithJsonSchema({"type": "number", "exclusiveMinimum": 0})]
NonNegativeNumber = Annotated[Decimal, Field(ge=0, allow_inf_nan=False),
                              WithJsonSchema({"type": "number", "minimum": 0})]


class RiskRewardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    side: Literal["long", "short"]
    entry: PositiveNumber
    stop: PositiveNumber
    target: PositiveNumber
    commission_per_unit: NonNegativeNumber | None = None
    spread_per_unit: NonNegativeNumber | None = None
    slippage_per_unit: NonNegativeNumber | None = None
    atr: PositiveNumber | None = None
    origin: Literal["user_assumption", "structural_hypothesis"] = "user_assumption"

    @field_validator("entry", "stop", "target", "commission_per_unit", "spread_per_unit", "slippage_per_unit", "atr", mode="before")
    @classmethod
    def strict_number(cls, value):
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("价格和成本必须是有效数值，不能使用字符串或布尔值")
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("价格和成本必须是有限数值")
        as_float = float(number)
        if not isfinite(as_float) or (number != 0 and as_float == 0):
            raise ValueError("数值超出接口能够可靠表示的范围")
        return number

    @model_validator(mode="after")
    def ordered_prices(self):
        if self.side == "long" and not self.stop < self.entry < self.target:
            raise ValueError("多头方案必须满足止损价 < 入场价 < 目标价")
        if self.side == "short" and not self.target < self.entry < self.stop:
            raise ValueError("空头方案必须满足目标价 < 入场价 < 止损价")
        return self


class CostBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    basis: Literal["round_trip_per_unit"] = "round_trip_per_unit"
    commission_per_unit: float | None
    spread_per_unit: float | None
    slippage_per_unit: float | None
    included_components: list[str]
    unconfirmed_components: list[str]
    all_costs_confirmed: bool
    total_included_cost: float


class RiskRewardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    status: Literal["evaluated", "unfavorable_after_costs"]
    side: Literal["long", "short"]
    origin: Literal["user_assumption", "structural_hypothesis"]
    hypothetical: Literal[True] = True
    structural_confirmation: Literal["not_checked"] = "not_checked"
    monetary_unit: Literal["quote_currency_per_unit"] = "quote_currency_per_unit"
    entry: float
    stop: float
    target: float
    gross_risk: float
    gross_reward: float
    gross_reward_to_risk: float
    net_risk: float
    net_reward: float
    net_reward_to_risk: float
    costs: CostBreakdown
    atr: float | None
    stop_distance_atr: float | None
    breakeven_win_rate_percent: float | None
    breakeven_kind: Literal["theoretical_required_rate", "unavailable_non_positive_reward"]
    historical_win_rate: None = None
    historical_win_rate_status: Literal["unverified"] = "unverified"
    warnings: list[str]
    assumptions: list[str]


class RiskRewardCalculationError(ValueError):
    """Validated inputs produced a result outside the finite JSON number range."""


def _number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not isfinite(result) or (value != 0 and result == 0):
        raise RiskRewardCalculationError("测算结果超出接口能够可靠表示的范围")
    return result


def evaluate_risk_reward(request: RiskRewardRequest | dict) -> RiskRewardResult:
    """Evaluate only the supplied prices; reject invalid inputs explicitly.

    Decimal(str(input)) retains input precision through all arithmetic. No price,
    cost or ratio is rounded before calculating. Conversion to JSON-compatible
    numbers occurs only when constructing the result.
    """
    plan = RiskRewardRequest.model_validate(request)
    components = {
        "commission_per_unit": plan.commission_per_unit,
        "spread_per_unit": plan.spread_per_unit,
        "slippage_per_unit": plan.slippage_per_unit,
    }
    included = [name for name, value in components.items() if value is not None]
    unconfirmed = [name for name, value in components.items() if value is None]
    numbers = [plan.entry, plan.stop, plan.target, *(value for value in components.values() if value is not None)]
    if plan.atr is not None:
        numbers.append(plan.atr)
    # Precision also accommodates unusually different magnitudes without first
    # rounding a small stop distance or a small specified cost away.
    precision = max(34, max(len(number.as_tuple().digits) for number in numbers) + 12,
                    max(number.adjusted() for number in numbers) - min(number.adjusted() for number in numbers) + 12)
    with localcontext() as context:
        context.prec = precision
        risk = plan.entry - plan.stop if plan.side == "long" else plan.stop - plan.entry
        reward = plan.target - plan.entry if plan.side == "long" else plan.entry - plan.target
        cost = sum((value for value in components.values() if value is not None), Decimal(0))
        net_risk, net_reward = risk + cost, reward - cost
        gross_ratio, net_ratio = reward / risk, net_reward / net_risk
        stop_atr = risk / plan.atr if plan.atr is not None else None
        breakeven = Decimal(100) * net_risk / (net_risk + net_reward) if net_reward > 0 else None

    warnings = []
    if unconfirmed:
        warnings.append("部分或全部手续费、点差、滑点未指定，未计入测算；净值仅扣除明确提供的成本，并非完整执行成本。")
    if net_reward <= 0:
        warnings.append("计入已指定成本后目标收益不为正，不能形成具有正目标收益的方案，也不提供盈亏平衡所需胜率。")
    if stop_atr is not None and stop_atr < 1:
        warnings.append("止损距离小于所提供的ATR，可能受到正常波动影响；ATR不能单独确认止损有效性。")
    if plan.atr is None:
        warnings.append("未提供ATR，未验证止损距离与近期波动的关系。")
    warnings.append("实际历史胜率未验证；盈亏平衡所需胜率只是由本方案盈亏计算的理论门槛，不是策略胜率。")

    return RiskRewardResult(
        status="evaluated" if net_reward > 0 else "unfavorable_after_costs",
        side=plan.side, origin=plan.origin,
        entry=_number(plan.entry), stop=_number(plan.stop), target=_number(plan.target),
        gross_risk=_number(risk), gross_reward=_number(reward), gross_reward_to_risk=_number(gross_ratio),
        net_risk=_number(net_risk), net_reward=_number(net_reward), net_reward_to_risk=_number(net_ratio),
        costs=CostBreakdown(**{name: _number(value) for name, value in components.items()},
                            included_components=included, unconfirmed_components=unconfirmed,
                            all_costs_confirmed=not unconfirmed, total_included_cost=_number(cost)),
        atr=_number(plan.atr), stop_distance_atr=_number(stop_atr),
        breakeven_win_rate_percent=_number(breakeven),
        breakeven_kind="theoretical_required_rate" if breakeven is not None else "unavailable_non_positive_reward",
        warnings=warnings,
        assumptions=[
            "价格由调用者明确提供，仅为测算假设，不等于当前可成交报价或交易建议。",
            "按同一报价货币每单位计算，所指定的费用、点差和滑点均为往返固定成本；未推断仓位、杠杆或按比例费用。",
            "假定按给定入场、止损或目标成交；跳空、流动性不足及超出已指定滑点的成交偏差可能改变结果。",
            "本服务不选择候选价位，不确认滚动区间极值、支撑或止损是否有效。",
        ],
    )
