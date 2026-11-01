from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from app.services.trade_plan_service import (
    RiskRewardCalculationError,
    RiskRewardRequest,
    RiskRewardResult,
    evaluate_risk_reward,
)


def example(**overrides):
    return {"side": "long", "entry": 690, "stop": 680, "target": 720, **overrides}


def test_explicit_long_example_is_calculated_without_market_claims():
    result = evaluate_risk_reward(example())
    assert (result.gross_risk, result.gross_reward, result.gross_reward_to_risk) == (10, 30, 3)
    assert (result.net_risk, result.net_reward, result.net_reward_to_risk) == (10, 30, 3)
    assert result.breakeven_win_rate_percent == 25
    assert result.breakeven_kind == "theoretical_required_rate"
    assert result.historical_win_rate is None
    assert result.historical_win_rate_status == "unverified"
    assert result.hypothetical is True
    assert result.structural_confirmation == "not_checked"


def test_short_reverses_price_order_and_uses_correct_formula():
    result = evaluate_risk_reward(example(side="short", stop=700, target=660))
    assert result.side == "short"
    assert (result.gross_risk, result.gross_reward, result.gross_reward_to_risk) == (10, 30, 3)


def test_round_trip_commission_spread_and_slippage_are_each_counted_once():
    result = evaluate_risk_reward(example(commission_per_unit=1, spread_per_unit=0.2, slippage_per_unit=0.3))
    assert result.costs.total_included_cost == 1.5
    assert result.costs.all_costs_confirmed is True
    assert result.costs.unconfirmed_components == []
    assert result.net_risk == 11.5
    assert result.net_reward == 28.5
    assert result.net_reward_to_risk == pytest.approx(28.5 / 11.5)
    assert result.breakeven_win_rate_percent == 28.75
    assert result.gross_reward_to_risk == 3


def test_short_costs_raise_risk_and_reduce_reward_instead_of_reversing_sign():
    result = evaluate_risk_reward(example(side="short", stop=700, target=660, commission_per_unit=2))
    assert result.net_risk == 12
    assert result.net_reward == 28
    assert result.net_reward_to_risk == pytest.approx(28 / 12)


def test_omitted_costs_remain_null_and_unconfirmed_not_claimed_as_free_execution():
    result = evaluate_risk_reward(example())
    assert result.costs.commission_per_unit is None
    assert result.costs.spread_per_unit is None
    assert result.costs.slippage_per_unit is None
    assert result.costs.included_components == []
    assert len(result.costs.unconfirmed_components) == 3
    assert result.costs.all_costs_confirmed is False
    assert any("并非完整执行成本" in warning for warning in result.warnings)


def test_explicit_zero_cost_is_distinct_from_missing_cost():
    result = evaluate_risk_reward(example(commission_per_unit=0, spread_per_unit=0, slippage_per_unit=0))
    assert result.costs.all_costs_confirmed is True
    assert len(result.costs.included_components) == 3
    assert result.costs.commission_per_unit == 0
    assert not any("并非完整执行成本" in warning for warning in result.warnings)


def test_partially_specified_costs_only_subtract_known_components():
    result = evaluate_risk_reward(example(spread_per_unit=0.4))
    assert result.costs.included_components == ["spread_per_unit"]
    assert result.costs.unconfirmed_components == ["commission_per_unit", "slippage_per_unit"]
    assert result.costs.total_included_cost == 0.4
    assert result.net_risk == 10.4


@pytest.mark.parametrize("side,stop,target", [
    ("long", 690, 720), ("long", 700, 720), ("long", 680, 690), ("long", 680, 670),
    ("short", 690, 660), ("short", 680, 660), ("short", 700, 690), ("short", 700, 720),
])
def test_invalid_price_order_is_explicitly_rejected(side, stop, target):
    with pytest.raises(ValidationError):
        evaluate_risk_reward(example(side=side, stop=stop, target=target))


@pytest.mark.parametrize("field,value", [
    ("entry", 0), ("stop", -1), ("target", None), ("entry", float("nan")),
    ("target", float("inf")), ("entry", True), ("entry", "690"),
    ("commission_per_unit", -0.1), ("spread_per_unit", float("nan")),
    ("slippage_per_unit", float("-inf")), ("commission_per_unit", False),
    ("spread_per_unit", "0.2"), ("atr", 0), ("atr", -1), ("atr", True),
])
def test_invalid_prices_and_costs_are_not_coerced_into_valid_inputs(field, value):
    with pytest.raises(ValidationError):
        evaluate_risk_reward(example(**{field: value}))


@pytest.mark.parametrize("cost,net_reward,net_ratio", [(30, 0, 0), (35, -5, -5 / 45)])
def test_costs_exceeding_target_reward_cannot_produce_attractive_plan(cost, net_reward, net_ratio):
    result = evaluate_risk_reward(example(commission_per_unit=cost))
    assert result.status == "unfavorable_after_costs"
    assert result.net_reward == net_reward
    assert result.net_reward_to_risk == pytest.approx(net_ratio)
    assert result.breakeven_win_rate_percent is None
    assert result.breakeven_kind == "unavailable_non_positive_reward"
    assert any("目标收益不为正" in warning for warning in result.warnings)


def test_atr_compares_distance_without_selecting_or_changing_a_stop():
    result = evaluate_risk_reward(example(atr=12.5))
    assert result.stop == 680
    assert result.stop_distance_atr == 0.8
    assert any("正常波动" in warning for warning in result.warnings)
    assert result.structural_confirmation == "not_checked"


def test_wider_stop_does_not_claim_atr_confirms_its_validity():
    result = evaluate_risk_reward(example(atr=5))
    assert result.stop_distance_atr == 2
    assert not any("正常波动" in warning for warning in result.warnings)
    assert any("不确认" in assumption for assumption in result.assumptions)
    assert result.structural_confirmation == "not_checked"


def test_missing_atr_is_not_replaced_by_zero_or_reported_as_checked():
    result = evaluate_risk_reward(example())
    assert result.atr is None
    assert result.stop_distance_atr is None
    assert any("未提供ATR" in warning for warning in result.warnings)


def test_calculation_does_not_round_input_prices_or_costs_first():
    result = evaluate_risk_reward(example(entry=0.1234567, stop=0.1234561, target=0.1234585, spread_per_unit=0.0000001))
    assert result.gross_risk == 0.0000006
    assert result.gross_reward == 0.0000018
    assert result.gross_reward_to_risk == 3
    assert result.net_risk == 0.0000007
    assert result.net_reward == 0.0000017
    assert result.net_reward_to_risk == pytest.approx(17 / 7)


def test_decimal_inputs_preserve_the_supplied_distance_through_arithmetic():
    result = evaluate_risk_reward(example(entry=Decimal("690.0000000000001"), stop=Decimal("690"), target=Decimal("690.0000000000004")))
    assert result.gross_risk == 1e-13
    assert result.gross_reward == 3e-13
    assert result.gross_reward_to_risk == 3


def test_json_result_retains_numeric_values_and_cannot_contain_historical_probability():
    result = evaluate_risk_reward(RiskRewardRequest(**example(origin="structural_hypothesis")))
    payload = json.loads(result.model_dump_json())
    assert payload["entry"] == 690
    assert isinstance(payload["net_reward_to_risk"], float)
    assert payload["historical_win_rate"] is None
    assert payload["origin"] == "structural_hypothesis"
    assert payload["structural_confirmation"] == "not_checked"
    with pytest.raises(ValidationError):
        RiskRewardResult.model_validate({**payload, "historical_win_rate": 0.75})


def test_unknown_fields_and_directions_are_rejected():
    with pytest.raises(ValidationError):
        evaluate_risk_reward({**example(), "win_rate": 80})
    with pytest.raises(ValidationError):
        evaluate_risk_reward(example(side="buy"))


def test_function_calling_schema_matches_the_actual_numeric_input_contract():
    properties = RiskRewardRequest.model_json_schema()["properties"]
    assert properties["entry"]["type"] == "number"
    assert properties["entry"]["exclusiveMinimum"] == 0
    assert {option["type"] for option in properties["commission_per_unit"]["anyOf"]} == {"number", "null"}
    assert properties["commission_per_unit"]["anyOf"][0]["minimum"] == 0


def test_finite_inputs_whose_output_overflows_are_explicitly_rejected():
    with pytest.raises(RiskRewardCalculationError):
        evaluate_risk_reward(example(entry=1e308, stop=1, target=1.5e308, commission_per_unit=1e308))
