from __future__ import annotations

import pytest

from smart_contracts.market_app.model import MAX_OUTCOMES, MIN_OUTCOMES, STATUS_CREATED, MarketAppError

from .market_app_test_utils import CONTRACT_SOURCE, make_market, source_text


def test_market_app_creation_state_and_boxes() -> None:
    low = make_market(num_outcomes=MIN_OUTCOMES)
    high = make_market(num_outcomes=MAX_OUTCOMES)

    assert low.num_outcomes == MIN_OUTCOMES
    assert high.num_outcomes == MAX_OUTCOMES
    assert low.status == STATUS_CREATED
    assert low.q == [0] * MIN_OUTCOMES
    assert low.pool_balance == 0
    assert low.lp_shares_total == 0
    assert low.cumulative_fee_per_share == 0
    assert low.proposed_outcome == -1
    assert low.proposal_timestamp == 0
    assert low.proposal_evidence_hash == b""

    contract_source = source_text(CONTRACT_SOURCE)
    assert "quantities_packed" in contract_source
    assert "total_shares_packed" in contract_source
    assert 'BOX_KEY_USER_SHARES = b"us:"' in contract_source
    assert 'BOX_KEY_USER_COST_BASIS = b"uc:"' in contract_source

    required_fields = [
        "creator:",
        "currency_asa:",
        "num_outcomes:",
        "b:",
        "pool_balance:",
        "lp_shares_total:",
        "lp_fee_bps:",
        "protocol_fee_bps:",
        "cumulative_fee_per_share:",
        "status:",
        "deadline:",
        "question_hash:",
        "blueprint_cid:",
        "proposed_outcome:",
        "proposal_timestamp:",
        "proposal_evidence_hash:",
        "challenge_window_secs:",
        "challenger:",
        "protocol_config_id:",
        "protocol_treasury:",
        "residual_linear_lambda_fp:",
        "lp_shares:",
        "fee_snapshot:",
    ]
    for field in required_fields:
        assert field in contract_source

    with pytest.raises(MarketAppError, match="num_outcomes"):
        make_market(num_outcomes=MIN_OUTCOMES - 1)
    with pytest.raises(MarketAppError, match="num_outcomes"):
        make_market(num_outcomes=MAX_OUTCOMES + 1)
