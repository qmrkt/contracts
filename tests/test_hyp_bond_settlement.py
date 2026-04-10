from __future__ import annotations

from hypothesis import given, strategies as st

from smart_contracts.market_app.model import BPS_DENOMINATOR, MarketAppModel
from tests.test_helpers import safe_bootstrap_deposit


@st.composite
def st_disputed_market(draw) -> MarketAppModel:
    winner_share_bps = draw(st.integers(min_value=0, max_value=BPS_DENOMINATOR))
    dispute_sink_share_bps = draw(st.integers(min_value=0, max_value=BPS_DENOMINATOR - winner_share_bps))
    proposal_bond = draw(st.integers(min_value=1, max_value=1_000_000_000))
    challenge_bond = draw(st.integers(min_value=1, max_value=1_000_000_000))

    market = MarketAppModel(
        creator="creator",
        currency_asa=31_566_704,
        outcome_asa_ids=[1000, 1001, 1002],
        b=100_000_000,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=10_000,
        question_hash=b"q" * 32,
        main_blueprint_hash=b"b" * 32,
        dispute_blueprint_hash=b"d" * 32,
        challenge_window_secs=86_400,
        protocol_config_id=77,
        factory_id=88,
        resolution_authority="resolver",
        challenge_bond=challenge_bond,
        proposal_bond=proposal_bond,
        challenge_bond_bps=0,
        proposal_bond_bps=0,
        challenge_bond_cap=challenge_bond,
        proposal_bond_cap=proposal_bond,
        grace_period_secs=3_600,
        market_admin="admin",
    )
    market.winner_share_bps = winner_share_bps
    market.dispute_sink_share_bps = dispute_sink_share_bps
    market.bootstrap(sender="creator", deposit_amount=safe_bootstrap_deposit(market.num_outcomes, market.b, minimum=200_000_000))
    market.buy(sender="alice", outcome_index=0, max_cost=10_000_000, now=5_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    proposal_required = market.proposal_bond
    market.propose_resolution(
        sender="open_proposer",
        outcome_index=0,
        evidence_hash=b"e" * 32,
        now=market.deadline + market.grace_period_secs + 1,
        bond_paid=proposal_required,
    )
    challenge_required = market.challenge_bond
    market.challenge_resolution(
        sender="challenger",
        bond_paid=challenge_required,
        reason_code=1,
        evidence_hash=b"c" * 32,
        now=market.deadline + market.grace_period_secs + 2,
    )
    return market


@given(st_disputed_market())
def test_confirmed_dispute_bonds_conserved(market: MarketAppModel) -> None:
    total_bonds = market.proposer_bond_held + market.challenger_bond_held
    settlement = market._settle_confirmed_dispute()

    assert settlement["proposer_payout"] + settlement["dispute_sink_capture"] == total_bonds
    assert market.proposer_bond_held == 0
    assert market.challenger_bond_held == 0


@given(st_disputed_market())
def test_overturned_dispute_bonds_conserved(market: MarketAppModel) -> None:
    total_bonds = market.proposer_bond_held + market.challenger_bond_held
    settlement = market._settle_overturned_dispute()

    assert settlement["challenger_payout"] + settlement["dispute_sink_capture"] == total_bonds
    assert market.proposer_bond_held == 0
    assert market.challenger_bond_held == 0


@given(st_disputed_market())
def test_cancelled_dispute_bonds_conserved(market: MarketAppModel) -> None:
    total_bonds = market.proposer_bond_held + market.challenger_bond_held
    settlement = market._settle_cancel_bonds()

    assert settlement["challenger_refund"] + settlement["dispute_sink_capture"] == total_bonds
    assert market.proposer_bond_held == 0
    assert market.challenger_bond_held == 0


@given(st_disputed_market())
def test_winner_bonus_bounded_by_losing_bond(market: MarketAppModel) -> None:
    loser_bond = market.challenger_bond_held
    bonus = market._winner_bonus_from_bond(loser_bond)

    assert 0 <= bonus <= loser_bond
    assert bonus + (loser_bond - bonus) == loser_bond
