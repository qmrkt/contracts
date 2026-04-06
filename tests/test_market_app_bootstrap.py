from __future__ import annotations

import pytest

from smart_contracts.market_app.model import STATUS_ACTIVE, MarketAppError

from .market_app_test_utils import make_market


def test_bootstrap_creator_funds_opts_in_and_mints_initial_lp() -> None:
    market = make_market()

    with pytest.raises(MarketAppError, match="only creator"):
        market.bootstrap(sender="alice", deposit_amount=200_000_000)

    minted = market.bootstrap(sender="creator", deposit_amount=200_000_000)

    assert minted == 200_000_000
    assert market.status == STATUS_ACTIVE
    assert market.pool_balance == 200_000_000
    assert market.lp_shares_total == 200_000_000
    assert market.user_lp_shares["creator"] == 200_000_000
    assert market.user_fee_snapshot["creator"] == 0
    assert market.events[-1]["event"] == "Bootstrap"
    assert market.events[-1]["opted_in_asa_ids"] == market.asa

