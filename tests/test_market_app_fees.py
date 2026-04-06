from __future__ import annotations

from .market_app_test_utils import buy_one, make_market


def test_every_trade_deducts_lp_and_protocol_fees_and_tracks_lp_fee_distribution() -> None:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)

    buy_result = buy_one(market, sender="buyer", outcome_index=0)
    sell_result = market.sell(sender="buyer", outcome_index=0, min_return=1, now=5_001)

    assert buy_result["lp_fee"] > 0 and buy_result["protocol_fee"] > 0
    assert sell_result["lp_fee"] > 0 and sell_result["protocol_fee"] > 0
    assert market.lp_fee_balance == buy_result["lp_fee"] + sell_result["lp_fee"]
    assert market.protocol_fee_balance == buy_result["protocol_fee"] + sell_result["protocol_fee"]
    assert market.cumulative_fee_per_share > 0

    before_claimable = market.user_claimable_fees.get("creator", 0)
    withdraw_result = market.withdraw_liq(sender="creator", shares_to_burn=market.user_lp_shares["creator"] // 10)
    assert withdraw_result["fee_return"] >= before_claimable

