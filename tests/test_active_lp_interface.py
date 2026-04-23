from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.market_app.active_lp_model import ACTIVE_LP_MARKET_CONTRACT_VERSION
from smart_contracts.market_app.model import STATUS_ACTIVE

from .market_app_test_utils import make_active_lp_market


def test_active_lp_model_exposes_contract_surface_state() -> None:
    market = make_active_lp_market()

    minted = market.bootstrap(sender="creator", deposit_amount=200_000_000, now=1)
    prices = lmsr_prices(market.q, market.b)

    assert market.contract_version == ACTIVE_LP_MARKET_CONTRACT_VERSION
    assert market.status == STATUS_ACTIVE
    assert minted == market.b
    assert market.activation_timestamp == 1
    assert market.user_lp_shares["creator"] == market.b
    assert len(prices) == market.num_outcomes
