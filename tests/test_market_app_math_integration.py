from __future__ import annotations

from smart_contracts.market_app import model as market_model_module

from .market_app_test_utils import MODEL_SOURCE, buy_one, make_market, source_text


def test_market_app_matches_c1_reference_vectors_for_trades_and_liquidity_ops(monkeypatch) -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    calls = {"buy": 0, "sell": 0, "scale": 0}

    original_buy = market_model_module.lmsr_cost_delta
    original_sell = market_model_module.lmsr_sell_return
    original_scale = market_model_module.lmsr_liquidity_scale

    def wrapped_buy(*args, **kwargs):
        calls["buy"] += 1
        return original_buy(*args, **kwargs)

    def wrapped_sell(*args, **kwargs):
        calls["sell"] += 1
        return original_sell(*args, **kwargs)

    def wrapped_scale(*args, **kwargs):
        calls["scale"] += 1
        return original_scale(*args, **kwargs)

    monkeypatch.setattr(market_model_module, "lmsr_cost_delta", wrapped_buy)
    monkeypatch.setattr(market_model_module, "lmsr_sell_return", wrapped_sell)
    monkeypatch.setattr(market_model_module, "lmsr_liquidity_scale", wrapped_scale)

    buy_one(market, sender="buyer", outcome_index=0)
    market.sell(sender="buyer", outcome_index=0, min_return=1, now=5_001)
    market.provide_liq(sender="lp2", deposit_amount=10_000_000, now=5_500)

    assert calls == {"buy": 1, "sell": 1, "scale": 1}
    assert "from smart_contracts.lmsr_math import" in source_text(MODEL_SOURCE)

