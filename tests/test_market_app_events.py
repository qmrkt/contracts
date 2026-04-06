from __future__ import annotations

from .market_app_test_utils import CONTRACT_SOURCE, buy_one, make_market, resolve_market, source_text


def test_arc28_events_emitted_for_all_state_changes() -> None:
    contract_source = source_text(CONTRACT_SOURCE)
    expected_emit_markers = [
        'arc4.emit("Bootstrap()")',
        'arc4.emit("Buy(uint64)"',
        'arc4.emit("Sell(uint64)"',
        'arc4.emit("ProvideLiquidity(uint64)"',
        'arc4.emit("WithdrawLiquidity(uint64)"',
        'arc4.emit("TriggerResolution()")',
        'arc4.emit("ProposeResolution(uint64,byte[])",',
        'arc4.emit("ProposeEarlyResolution(uint64,byte[])",',
        'arc4.emit("ChallengeResolution()")',
        'arc4.emit("AbortEarlyResolution(byte[],uint64)")',
        'arc4.emit("FinalizeResolution()")',
        'arc4.emit("Claim(uint64)"',
        'arc4.emit("Cancel()")',
        'arc4.emit("Refund(uint64)"',
        'arc4.emit("CommentPosted(string)"',
    ]
    for marker in expected_emit_markers:
        assert marker in contract_source

    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="buyer", outcome_index=0)
    market.provide_liq(sender="lp2", deposit_amount=10_000_000, now=6_000)
    market.withdraw_liq(sender="lp2", shares_to_burn=max(1, market.user_lp_shares["lp2"] // 2))
    market.post_comment(sender="creator", message="gm")
    resolve_market(market)
    event_names = [event["event"] for event in market.events]
    for required in [
        "Bootstrap",
        "Buy",
        "ProvideLiquidity",
        "WithdrawLiquidity",
        "CommentPosted",
        "TriggerResolution",
        "ProposeResolution",
        "FinalizeResolution",
    ]:
        assert required in event_names
