from __future__ import annotations

from smart_contracts.lmsr_math import lmsr_prices

from .market_app_test_utils import buy_one, make_active_lp_market


def test_layer_c_tracks_reference_on_neutral_late_lp() -> None:
    market = make_active_lp_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    buy_one(market, sender="buyer", outcome_index=1, now=2)
    reference_prices = lmsr_prices(market.q, market.b)

    market.enter_lp_active(
        sender="late_lp",
        target_delta_b=25_000_000,
        max_deposit=100_000_000,
        expected_prices=list(reference_prices),
        now=3,
    )
    layer_c_prices = lmsr_prices(market.q, market.b)

    assert max(abs(a - b) for a, b in zip(reference_prices, layer_c_prices)) <= 2
    assert market.user_lp_shares["late_lp"] == 25_000_000
