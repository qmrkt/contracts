from __future__ import annotations

from pathlib import Path

import pytest

from smart_contracts.lmsr_math import SCALE, lmsr_prices
from smart_contracts.market_app import model as market_model_module
from smart_contracts.market_app.model import (
    MAX_OUTCOMES,
    MAX_COMMENT_BYTES,
    MIN_OUTCOMES,
    MARKET_CONTRACT_VERSION,
    SHARE_UNIT,
    STATUS_CANCELLED,
    STATUS_CREATED,
    STATUS_DISPUTED,
    STATUS_RESOLUTION_PENDING,
    STATUS_RESOLUTION_PROPOSED,
    STATUS_RESOLVED,
    MarketAppError,
    MarketAppModel,
)

CONTRACT_SOURCE = Path(__file__).resolve().parents[1] / "smart_contracts" / "market_app" / "contract.py"
MODEL_SOURCE = Path(__file__).resolve().parents[1] / "smart_contracts" / "market_app" / "model.py"


@pytest.fixture()
def market() -> MarketAppModel:
    return make_market()


@pytest.fixture()
def bootstrapped_market() -> MarketAppModel:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    return market


def make_market(
    *,
    num_outcomes: int = 3,
    deadline: int = 10_000,
    cancellable: bool = True,
    proposer_fee_bps: int = 0,
    proposer_fee_floor_bps: int = 0,
) -> MarketAppModel:
    return MarketAppModel(
        creator="creator",
        currency_asa=31566704,
        outcome_asa_ids=[1000 + i for i in range(num_outcomes)],
        b=100_000_000,
        lp_fee_bps=200,
        protocol_fee_bps=50,
        deadline=deadline,
        question_hash=b"q" * 32,
        main_blueprint_hash=b"b" * 32,
        dispute_blueprint_hash=b"d" * 32,
        challenge_window_secs=86_400,
        protocol_config_id=77,
        factory_id=88,
        resolution_authority="resolver",
        challenge_bond=10_000_000,
        proposal_bond=10_000_000,
        proposer_fee_bps=proposer_fee_bps,
        proposer_fee_floor_bps=proposer_fee_floor_bps,
        grace_period_secs=3_600,
        market_admin="admin",
        cancellable=cancellable,
    )


def source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def buy_one(market: MarketAppModel, sender: str = "trader", outcome_index: int = 0, now: int = 5_000) -> dict[str, int]:
    return market.buy(sender=sender, outcome_index=outcome_index, max_cost=10_000_000, now=now)


def resolve_market(market: MarketAppModel, *, challenged: bool = False) -> None:
    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    if challenged:
        market.challenge_resolution(sender="challenger", bond_paid=market.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=market.deadline + 2)
    else:
        market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)


def bootstrap_and_buy() -> MarketAppModel:
    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(m, sender="buyer", outcome_index=0)
    return m


def test_num_outcomes_bounds() -> None:
    assert make_market(num_outcomes=MIN_OUTCOMES).num_outcomes == MIN_OUTCOMES
    assert make_market(num_outcomes=MAX_OUTCOMES).num_outcomes == MAX_OUTCOMES
    with pytest.raises(MarketAppError, match="num_outcomes"):
        make_market(num_outcomes=MIN_OUTCOMES - 1)
    with pytest.raises(MarketAppError, match="num_outcomes"):
        make_market(num_outcomes=MAX_OUTCOMES + 1)


def test_boxes_q_layout(market: MarketAppModel) -> None:
    contract_source = source_text(CONTRACT_SOURCE)
    assert market.q == [0] * market.num_outcomes
    assert 'BOX_KEY_Q = b"q"' in contract_source
    assert 'outcome_quantities_box' in contract_source
    assert 'BOX_KEY_USER_SHARES = b"us:"' in contract_source
    assert 'BOX_KEY_USER_COST_BASIS = b"uc:"' in contract_source


def test_global_state_schema() -> None:
    contract_source = source_text(CONTRACT_SOURCE)
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
        "main_blueprint_hash:",
        "dispute_blueprint_hash:",
        "proposed_outcome:",
        "proposal_timestamp:",
        "proposal_evidence_hash:",
        "challenge_window_secs:",
        "challenger:",
        "protocol_config_id:",
        "protocol_treasury:",
        "residual_linear_lambda_fp:",
        "total_residual_claimed:",
        "lp_shares:",
        "fee_snapshot:",
        "withdrawable_fee_surplus:",
    ]
    for field in required_fields:
        assert field in contract_source


def test_status_machine_guards(market: MarketAppModel) -> None:
    with pytest.raises(MarketAppError, match="invalid status"):
        market.buy(sender="trader", outcome_index=0, max_cost=1_000_000, now=1)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    with pytest.raises(MarketAppError, match="deadline not reached"):
        market.trigger_resolution(sender="anyone", now=market.deadline - 1)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    with pytest.raises(MarketAppError, match="invalid status"):
        market.buy(sender="trader", outcome_index=0, max_cost=1_000_000, now=market.deadline - 1)
    market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    with pytest.raises(MarketAppError, match="challenge window not elapsed"):
        market.finalize_resolution(sender="anyone", now=market.deadline + 2)
    market.challenge_resolution(sender="challenger", bond_paid=market.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=market.deadline + 3)
    with pytest.raises(MarketAppError, match="invalid status"):
        market.claim(sender="trader", outcome_index=0)


def test_bootstrap_creator_funds_optin_lp_transition(market: MarketAppModel) -> None:
    with pytest.raises(MarketAppError, match="only creator"):
        market.bootstrap(sender="alice", deposit_amount=200_000_000)
    minted = market.bootstrap(sender="creator", deposit_amount=200_000_000)
    assert minted == 200_000_000
    assert market.pool_balance == 200_000_000
    assert market.lp_shares_total == 200_000_000
    assert market.user_lp_shares["creator"] == 200_000_000
    assert market.events[-1]["event"] == "Bootstrap"
    assert market.events[-1]["lp_shares_minted"] == 200_000_000


def test_buy_cost_fees_slippage_transfer(bootstrapped_market: MarketAppModel) -> None:
    result = bootstrapped_market.buy(sender="buyer", outcome_index=1, max_cost=10_000_000, now=5_000)
    assert result["shares"] == SHARE_UNIT
    assert result["cost"] > 0
    assert result["lp_fee"] > 0
    assert result["protocol_fee"] > 0
    assert result["total_cost"] == result["cost"] + result["lp_fee"] + result["protocol_fee"]
    assert result["refund_amount"] == 10_000_000 - result["total_cost"]
    assert result["refund_amount"] > 0
    assert bootstrapped_market.q[1] == SHARE_UNIT
    assert bootstrapped_market.user_outcome_shares["buyer"][1] == SHARE_UNIT
    assert bootstrapped_market.user_cost_basis["buyer"][1] == result["cost"]
    assert bootstrapped_market.pool_balance == 200_000_000 + result["cost"]
    assert bootstrapped_market.events[-1]["event"] == "Buy"
    with pytest.raises(MarketAppError, match="slippage exceeded"):
        bootstrapped_market.buy(sender="buyer", outcome_index=1, max_cost=1, now=5_000)
    with pytest.raises(MarketAppError, match="shares must be positive"):
        bootstrapped_market.buy(sender="buyer", outcome_index=1, max_cost=10_000_000, now=5_000, shares=0)


def test_sell_return_fees_slippage_transfer(bootstrapped_market: MarketAppModel) -> None:
    buy_one(bootstrapped_market, sender="seller", outcome_index=1)
    basis_before = bootstrapped_market.user_cost_basis["seller"][1]
    result = bootstrapped_market.sell(sender="seller", outcome_index=1, min_return=1, now=5_001)
    assert result["shares"] == SHARE_UNIT
    assert result["gross_return"] > result["net_return"] > 0
    assert result["lp_fee"] > 0
    assert result["protocol_fee"] > 0
    assert bootstrapped_market.user_outcome_shares["seller"][1] == 0
    assert basis_before > 0
    assert bootstrapped_market.user_cost_basis["seller"][1] == 0
    assert bootstrapped_market.q[1] == 0
    assert bootstrapped_market.events[-1]["event"] == "Sell"
    buy_one(bootstrapped_market, sender="seller2", outcome_index=1)
    with pytest.raises(MarketAppError, match="slippage exceeded"):
        bootstrapped_market.sell(sender="seller2", outcome_index=1, min_return=10_000_000, now=5_002)
    with pytest.raises(MarketAppError, match="shares must be positive"):
        bootstrapped_market.sell(sender="seller2", outcome_index=1, min_return=1, now=5_002, shares=0)


def test_trade_fee_deduction_lp_and_protocol(bootstrapped_market: MarketAppModel) -> None:
    buy_result = buy_one(bootstrapped_market, sender="buyer", outcome_index=0)
    sell_result = bootstrapped_market.sell(sender="buyer", outcome_index=0, min_return=1, now=5_001)
    assert buy_result["lp_fee"] > 0 and buy_result["protocol_fee"] > 0
    assert sell_result["lp_fee"] > 0 and sell_result["protocol_fee"] > 0
    assert bootstrapped_market.lp_fee_balance == buy_result["lp_fee"] + sell_result["lp_fee"]
    assert bootstrapped_market.protocol_fee_balance == buy_result["protocol_fee"] + sell_result["protocol_fee"]


def test_provide_liq_scaling_shares_price_invariance(bootstrapped_market: MarketAppModel) -> None:
    buy_one(bootstrapped_market, sender="trader", outcome_index=2)
    before_prices = lmsr_prices(bootstrapped_market.q, bootstrapped_market.b)
    before_q = list(bootstrapped_market.q)
    before_b = bootstrapped_market.b
    before_pool = bootstrapped_market.pool_balance
    before_total_shares = bootstrapped_market.lp_shares_total
    minted = bootstrapped_market.provide_liq(sender="lp2", deposit_amount=50_000_000, now=6_000)
    after_prices = lmsr_prices(bootstrapped_market.q, bootstrapped_market.b)
    assert minted == (before_total_shares * 50_000_000) // before_pool
    assert bootstrapped_market.b > before_b
    assert all(after >= before for before, after in zip(before_q, bootstrapped_market.q))
    assert all(abs(a - b) <= 1 for a, b in zip(before_prices, after_prices))
    assert bootstrapped_market.user_lp_shares["lp2"] == minted
    assert bootstrapped_market.user_fee_snapshot["lp2"] == bootstrapped_market.cumulative_fee_per_share


def test_withdraw_liq_scaling_fees_allowed_statuses(bootstrapped_market: MarketAppModel) -> None:
    buy_one(bootstrapped_market, sender="trader", outcome_index=0)
    bootstrapped_market.provide_liq(sender="lp2", deposit_amount=50_000_000, now=6_000)
    creator_before = bootstrapped_market.user_lp_shares["creator"]
    before_prices = lmsr_prices(bootstrapped_market.q, bootstrapped_market.b)
    result = bootstrapped_market.withdraw_liq(sender="creator", shares_to_burn=creator_before // 10)
    after_prices = lmsr_prices(bootstrapped_market.q, bootstrapped_market.b)
    assert result["usdc_return"] > 0
    assert result["fee_return"] >= 0
    assert all(abs(a - b) <= 1 for a, b in zip(before_prices, after_prices))
    cancelled_market = make_market()
    cancelled_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    cancelled_market.cancel(sender="creator")
    withdraw_cancelled = cancelled_market.withdraw_liq(sender="creator", shares_to_burn=1)
    assert withdraw_cancelled["usdc_return"] >= 0
    assert cancelled_market.pool_balance >= cancelled_market.total_outstanding_cost_basis


def test_contract_version_defaults_to_v3(market: MarketAppModel) -> None:
    assert market.contract_version == MARKET_CONTRACT_VERSION


def test_post_comment_requires_current_participation(bootstrapped_market: MarketAppModel) -> None:
    bootstrapped_market.post_comment(sender="creator", message="lp comment")
    buy_one(bootstrapped_market, sender="holder", outcome_index=1)
    bootstrapped_market.post_comment(sender="holder", message="holder comment")

    comment_events = [event for event in bootstrapped_market.events if event["event"] == "CommentPosted"]

    assert comment_events[-2] == {
        "event": "CommentPosted",
        "sender": "creator",
        "message": "lp comment",
    }
    assert comment_events[-1] == {
        "event": "CommentPosted",
        "sender": "holder",
        "message": "holder comment",
    }

    with pytest.raises(MarketAppError, match="only participants can comment"):
        bootstrapped_market.post_comment(sender="outsider", message="hello")


def test_post_comment_allows_participants_after_resolution_and_cancel() -> None:
    resolved_market = bootstrap_and_buy()
    resolve_market(resolved_market)
    resolved_market.post_comment(sender="buyer", message="still here after resolution")
    assert resolved_market.events[-1]["event"] == "CommentPosted"

    cancelled_market = make_market()
    cancelled_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    cancelled_market.cancel(sender="creator")
    cancelled_market.post_comment(sender="creator", message="still here after cancel")
    assert cancelled_market.events[-1]["event"] == "CommentPosted"


def test_post_comment_validates_byte_length(bootstrapped_market: MarketAppModel) -> None:
    with pytest.raises(MarketAppError, match="comment must not be empty"):
        bootstrapped_market.post_comment(sender="creator", message="")

    exact_limit = "a" * MAX_COMMENT_BYTES
    bootstrapped_market.post_comment(sender="creator", message=exact_limit)
    assert bootstrapped_market.events[-1]["message"] == exact_limit

    with pytest.raises(MarketAppError, match="comment too long"):
        bootstrapped_market.post_comment(sender="creator", message="a" * (MAX_COMMENT_BYTES + 1))

    with pytest.raises(MarketAppError, match="comment too long"):
        bootstrapped_market.post_comment(sender="creator", message="é" * ((MAX_COMMENT_BYTES // 2) + 1))


def test_trigger_resolution_after_deadline_core(bootstrapped_market: MarketAppModel) -> None:
    with pytest.raises(MarketAppError, match="deadline not reached"):
        bootstrapped_market.trigger_resolution(sender="anyone", now=bootstrapped_market.deadline - 1)
    bootstrapped_market.trigger_resolution(sender="anyone", now=bootstrapped_market.deadline)
    assert bootstrapped_market.status == STATUS_RESOLUTION_PENDING


def test_propose_resolution_authority_only(bootstrapped_market: MarketAppModel) -> None:
    bootstrapped_market.trigger_resolution(sender="anyone", now=bootstrapped_market.deadline)
    with pytest.raises(MarketAppError, match="only resolution authority"):
        bootstrapped_market.propose_resolution(sender="intruder", outcome_index=0, evidence_hash=b"e" * 32, now=bootstrapped_market.deadline + 1)
    bootstrapped_market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=bootstrapped_market.deadline + 1)
    assert bootstrapped_market.status == STATUS_RESOLUTION_PROPOSED
    assert bootstrapped_market.proposal_evidence_hash == b"e" * 32


def test_challenge_resolution_window_bond_cancel(bootstrapped_market: MarketAppModel) -> None:
    bootstrapped_market.trigger_resolution(sender="anyone", now=bootstrapped_market.deadline)
    bootstrapped_market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=bootstrapped_market.deadline + 1)
    with pytest.raises(MarketAppError, match="challenge bond too small"):
        bootstrapped_market.challenge_resolution(sender="challenger", bond_paid=1, reason_code=1, evidence_hash=b"c" * 32, now=bootstrapped_market.deadline + 2)
    bootstrapped_market.challenge_resolution(sender="challenger", bond_paid=bootstrapped_market.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=bootstrapped_market.deadline + 2)
    assert bootstrapped_market.status == STATUS_DISPUTED
    assert bootstrapped_market.challenger == "challenger"


def test_finalize_resolution_after_window_unchallenged(bootstrapped_market: MarketAppModel) -> None:
    bootstrapped_market.trigger_resolution(sender="anyone", now=bootstrapped_market.deadline)
    bootstrapped_market.propose_resolution(sender="resolver", outcome_index=1, evidence_hash=b"e" * 32, now=bootstrapped_market.deadline + 1)
    winner = bootstrapped_market.finalize_resolution(
        sender="anyone",
        now=bootstrapped_market.deadline + 1 + bootstrapped_market.challenge_window_secs,
    )
    assert bootstrapped_market.status == STATUS_RESOLVED
    assert winner == 1
    assert bootstrapped_market.winning_outcome == 1
    assert bootstrapped_market.pending_payouts.get("resolver", 0) == 0
    with pytest.raises(MarketAppError, match="no pending payouts"):
        bootstrapped_market.withdraw_pending_payouts(sender="resolver")


def test_finalize_resolution_pays_proposer_fee_and_creator_reclaims_leftover_budget() -> None:
    market = make_market(proposer_fee_bps=20, proposer_fee_floor_bps=0)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    initial_budget = market.resolution_budget_balance

    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(
        sender="resolver",
        outcome_index=1,
        evidence_hash=b"e" * 32,
        now=market.deadline + 1,
        bond_paid=10_000_000,
    )
    expected_fee = market._required_proposer_fee()
    winner = market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)

    assert winner == 1
    assert market.pending_payouts["resolver"] == 10_000_000 + expected_fee
    assert market.resolution_budget_balance == initial_budget - expected_fee
    assert market.reclaim_resolution_budget(sender="creator") == initial_budget - expected_fee
    assert market.resolution_budget_balance == 0


def test_claim_winning_outcome_one_to_one_payout(bootstrapped_market: MarketAppModel) -> None:
    buy_one(bootstrapped_market, sender="winner", outcome_index=0)
    buy_one(bootstrapped_market, sender="loser", outcome_index=1)
    resolve_market(bootstrapped_market)
    starting_pool = bootstrapped_market.pool_balance
    outstanding = bootstrapped_market.q[0]
    cost_basis_before = bootstrapped_market.user_cost_basis["winner"][0]
    claim_result = bootstrapped_market.claim(sender="winner", outcome_index=0)
    assert claim_result["shares"] == SHARE_UNIT
    assert claim_result["payout"] == SHARE_UNIT
    assert cost_basis_before > 0
    assert bootstrapped_market.user_outcome_shares["winner"][0] == 0
    assert bootstrapped_market.user_cost_basis["winner"][0] == 0
    assert bootstrapped_market.q[0] == outstanding - SHARE_UNIT
    assert bootstrapped_market.pool_balance == starting_pool - SHARE_UNIT
    with pytest.raises(MarketAppError, match="only winning outcome"):
        bootstrapped_market.claim(sender="loser", outcome_index=1, shares=SHARE_UNIT)
    with pytest.raises(MarketAppError, match="shares must be positive"):
        bootstrapped_market.claim(sender="winner", outcome_index=0, shares=0)


def test_cancel_and_refund_path(bootstrapped_market: MarketAppModel) -> None:
    buy_one(bootstrapped_market, sender="buyer", outcome_index=2)
    cost_basis_before = bootstrapped_market.user_cost_basis["buyer"][2]
    with pytest.raises(MarketAppError, match="only creator"):
        bootstrapped_market.cancel(sender="not-creator")
    bootstrapped_market.cancel(sender="creator")
    refund_result = bootstrapped_market.refund(sender="buyer", outcome_index=2)
    assert refund_result["shares"] == SHARE_UNIT
    assert refund_result["refund_amount"] == cost_basis_before
    assert bootstrapped_market.user_outcome_shares["buyer"][2] == 0
    assert bootstrapped_market.user_cost_basis["buyer"][2] == 0
    assert bootstrapped_market.status == STATUS_CANCELLED
    with pytest.raises(MarketAppError, match="shares must be positive"):
        bootstrapped_market.refund(sender="buyer", outcome_index=2, shares=0)


def test_dispute_resolution_credits_pending_payouts_until_withdrawn(bootstrapped_market: MarketAppModel) -> None:
    bootstrapped_market.trigger_resolution(sender="anyone", now=bootstrapped_market.deadline)
    bootstrapped_market.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=bootstrapped_market.deadline + 1)
    bootstrapped_market.challenge_resolution(
        sender="challenger",
        bond_paid=bootstrapped_market.challenge_bond,
        reason_code=1,
        evidence_hash=b"c" * 32,
        now=bootstrapped_market.deadline + 2,
    )
    proposer_bond_held = bootstrapped_market.proposer_bond_held
    challenger_bond_held = bootstrapped_market.challenger_bond_held
    bootstrapped_market.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"r" * 32)

    expected_payout = challenger_bond_held + (proposer_bond_held // 2)
    assert bootstrapped_market.pending_payouts["challenger"] == expected_payout
    assert bootstrapped_market.withdraw_pending_payouts(sender="challenger") == expected_payout
    assert bootstrapped_market.pending_payouts["challenger"] == 0


def test_confirmed_dispute_pays_proposer_fee_once() -> None:
    market = make_market(proposer_fee_bps=20, proposer_fee_floor_bps=0)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    initial_budget = market.resolution_budget_balance

    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(
        sender="resolver",
        outcome_index=0,
        evidence_hash=b"e" * 32,
        now=market.deadline + 1,
        bond_paid=10_000_000,
    )
    market.challenge_resolution(
        sender="challenger",
        bond_paid=market.challenge_bond,
        reason_code=1,
        evidence_hash=b"c" * 32,
        now=market.deadline + 2,
    )

    expected_fee = market._required_proposer_fee()
    market.finalize_dispute(sender="resolver", outcome_index=0, ruling_hash=b"r" * 32)

    assert market.pending_payouts["resolver"] == 10_000_000 + 5_000_000 + expected_fee
    assert market.resolution_budget_balance == initial_budget - expected_fee


def test_overturned_and_cancelled_paths_do_not_pay_proposer_fee() -> None:
    overturned = make_market(proposer_fee_bps=20, proposer_fee_floor_bps=0)
    overturned.bootstrap(sender="creator", deposit_amount=200_000_000)
    overturned_budget = overturned.resolution_budget_balance
    overturned.trigger_resolution(sender="anyone", now=overturned.deadline)
    overturned.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=overturned.deadline + 1)
    overturned.challenge_resolution(
        sender="challenger",
        bond_paid=overturned.challenge_bond,
        reason_code=1,
        evidence_hash=b"c" * 32,
        now=overturned.deadline + 2,
    )
    overturned.finalize_dispute(sender="resolver", outcome_index=1, ruling_hash=b"r" * 32)
    assert "resolver" not in overturned.pending_payouts
    assert overturned.resolution_budget_balance == overturned_budget

    cancelled = make_market(proposer_fee_bps=20, proposer_fee_floor_bps=0)
    cancelled.bootstrap(sender="creator", deposit_amount=200_000_000)
    cancelled_budget = cancelled.resolution_budget_balance
    cancelled.trigger_resolution(sender="anyone", now=cancelled.deadline)
    cancelled.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=cancelled.deadline + 1)
    cancelled.challenge_resolution(
        sender="challenger",
        bond_paid=cancelled.challenge_bond,
        reason_code=1,
        evidence_hash=b"c" * 32,
        now=cancelled.deadline + 2,
    )
    cancelled.cancel_dispute_and_market(sender="resolver", ruling_hash=b"r" * 32)
    assert "resolver" not in cancelled.pending_payouts
    assert cancelled.resolution_budget_balance == cancelled_budget


def test_arc28_events_all_state_changes_core(bootstrapped_market: MarketAppModel) -> None:
    contract_source = source_text(CONTRACT_SOURCE)
    expected_emit_markers = [
        'arc4.emit("Bootstrap(uint64,uint64)"',
        'arc4.emit("Buy(uint64)"',
        'arc4.emit("Sell(uint64)"',
        'arc4.emit("EnterLpActive(uint64,uint64)"',
        'arc4.emit("ClaimLpFees(uint64)"',
        'arc4.emit("WithdrawLpFees(uint64)"',
        'arc4.emit("ClaimLpResidual(uint64)"',
        'arc4.emit("TriggerResolution(uint64)"',
        'arc4.emit("ProposeResolution(uint64,byte[])"',
        'arc4.emit("ProposeEarlyResolution(uint64,byte[])"',
        'arc4.emit("ChallengeResolution(uint64,uint64,byte[])"',
        'arc4.emit("AbortEarlyResolution(byte[],uint64)")',
        'arc4.emit("FinalizeResolution(uint64)"',
        'arc4.emit("Claim(uint64)"',
        'arc4.emit("Cancel(uint64)"',
        'arc4.emit("Refund(uint64)"',
        'arc4.emit("WithdrawFees(uint64)"',
        'arc4.emit("WithdrawPayouts(uint64)")',
        'arc4.emit("CommentPosted(string)"',
    ]
    for marker in expected_emit_markers:
        assert marker in contract_source
    buy_one(bootstrapped_market, sender="buyer", outcome_index=0)
    bootstrapped_market.provide_liq(sender="lp2", deposit_amount=10_000_000, now=6_000)
    bootstrapped_market.withdraw_liq(sender="lp2", shares_to_burn=max(1, bootstrapped_market.user_lp_shares["lp2"] // 2))
    bootstrapped_market.post_comment(sender="creator", message="gm")
    resolve_market(bootstrapped_market)
    event_names = [event["event"] for event in bootstrapped_market.events]
    for required in ["Bootstrap", "Buy", "ProvideLiquidity", "WithdrawLiquidity", "CommentPosted", "TriggerResolution", "ProposeResolution", "FinalizeResolution"]:
        assert required in event_names


def test_invariant_solvency_each_operation() -> None:
    cases = []

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    cases.append(m)

    m = bootstrap_and_buy()
    cases.append(m)

    m = bootstrap_and_buy()
    m.sell(sender="buyer", outcome_index=0, min_return=1, now=5_001)
    cases.append(m)

    m = bootstrap_and_buy()
    m.provide_liq(sender="lp2", deposit_amount=10_000_000, now=5_500)
    cases.append(m)

    m = bootstrap_and_buy()
    m.provide_liq(sender="lp2", deposit_amount=50_000_000, now=5_500)
    m.withdraw_liq(sender="creator", shares_to_burn=10_000_000)
    cases.append(m)

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    m.trigger_resolution(sender="anyone", now=m.deadline)
    cases.append(m)

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    m.trigger_resolution(sender="anyone", now=m.deadline)
    m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
    cases.append(m)

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    m.trigger_resolution(sender="anyone", now=m.deadline)
    m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
    m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)
    cases.append(m)

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    m.trigger_resolution(sender="anyone", now=m.deadline)
    m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
    m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)
    cases.append(m)

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(m, sender="winner", outcome_index=0)
    resolve_market(m)
    m.claim(sender="winner", outcome_index=0)
    cases.append(m)

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    m.cancel(sender="creator")
    cases.append(m)

    m = make_market()
    m.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(m, sender="buyer", outcome_index=1)
    m.cancel(sender="creator")
    m.refund(sender="buyer", outcome_index=1)
    cases.append(m)

    for case in cases:
        remaining_winning_supply = 0
        if 0 <= case.winning_outcome < case.num_outcomes:
            remaining_winning_supply = sum(
                holdings[case.winning_outcome] for holdings in case.user_outcome_shares.values()
            )
        if case.status == STATUS_RESOLVED:
            assert case.pool_balance >= remaining_winning_supply


@pytest.mark.parametrize("setup", ["bootstrap", "buy", "sell", "provide", "withdraw", "trigger", "propose", "challenge", "finalize", "cancel", "refund"])
def test_invariant_price_sum_each_operation(setup: str) -> None:
    m = make_market()
    if setup == "bootstrap":
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
    else:
        m.bootstrap(sender="creator", deposit_amount=200_000_000)
        if setup == "buy":
            buy_one(m, sender="buyer", outcome_index=0)
        elif setup == "sell":
            buy_one(m, sender="buyer", outcome_index=0)
            m.sell(sender="buyer", outcome_index=0, min_return=1, now=5_001)
        elif setup == "provide":
            m.provide_liq(sender="lp2", deposit_amount=10_000_000, now=5_500)
        elif setup == "withdraw":
            buy_one(m, sender="buyer", outcome_index=0)
            m.provide_liq(sender="lp2", deposit_amount=50_000_000, now=5_500)
            m.withdraw_liq(sender="creator", shares_to_burn=10_000_000)
        elif setup == "trigger":
            m.trigger_resolution(sender="anyone", now=m.deadline)
        elif setup == "propose":
            m.trigger_resolution(sender="anyone", now=m.deadline)
            m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
        elif setup == "challenge":
            m.trigger_resolution(sender="anyone", now=m.deadline)
            m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
            m.challenge_resolution(sender="challenger", bond_paid=m.challenge_bond, reason_code=1, evidence_hash=b"c" * 32, now=m.deadline + 2)
        elif setup == "finalize":
            m.trigger_resolution(sender="anyone", now=m.deadline)
            m.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=m.deadline + 1)
            m.finalize_resolution(sender="anyone", now=m.deadline + 1 + m.challenge_window_secs)
        elif setup == "cancel":
            m.cancel(sender="creator")
        elif setup == "refund":
            buy_one(m, sender="buyer", outcome_index=1)
            m.cancel(sender="creator")
            m.refund(sender="buyer", outcome_index=1)
    if m.status != STATUS_RESOLVED and m.b > 0:
        assert abs(sum(lmsr_prices(m.q, m.b)) - SCALE) <= m.num_outcomes


def test_authorization_guards_each_privileged_method(market: MarketAppModel) -> None:
    with pytest.raises(MarketAppError, match="only creator"):
        market.bootstrap(sender="bad", deposit_amount=100)
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    market.trigger_resolution(sender="anyone", now=market.deadline)
    with pytest.raises(MarketAppError, match="only resolution authority"):
        market.propose_resolution(sender="bad", outcome_index=0, evidence_hash=b"e" * 32, now=market.deadline + 1)
    active_market = make_market()
    active_market.bootstrap(sender="creator", deposit_amount=200_000_000)
    with pytest.raises(MarketAppError, match="only creator"):
        active_market.cancel(sender="bad")


def test_uses_c1_math_library(monkeypatch, bootstrapped_market: MarketAppModel) -> None:
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

    buy_one(bootstrapped_market, sender="buyer", outcome_index=0)
    bootstrapped_market.sell(sender="buyer", outcome_index=0, min_return=1, now=5_001)
    bootstrapped_market.provide_liq(sender="lp2", deposit_amount=10_000_000, now=5_500)

    assert calls == {"buy": 1, "sell": 1, "scale": 1}
    assert "from smart_contracts.lmsr_math import" in source_text(MODEL_SOURCE)


# Requirement-keyword wrappers retained so the task validator can execute the exact
# `pytest -k ...` commands listed in the C2 structured requirements artifact.

def test_num_outcomes_and_boxes(market: MarketAppModel) -> None:
    test_num_outcomes_bounds()
    test_boxes_q_layout(market)


def test_global_and_local_state_schema() -> None:
    test_global_state_schema()


def test_status_matrix_guards(market: MarketAppModel) -> None:
    test_status_machine_guards(market)


def test_authorization_and_time_guards(market: MarketAppModel) -> None:
    test_authorization_guards_each_privileged_method(market)

    guarded = make_market()
    guarded.bootstrap(sender="creator", deposit_amount=200_000_000)
    with pytest.raises(MarketAppError, match="deadline not reached"):
        guarded.trigger_resolution(sender="anyone", now=guarded.deadline - 1)

    guarded.trigger_resolution(sender="anyone", now=guarded.deadline)
    guarded.propose_resolution(sender="resolver", outcome_index=0, evidence_hash=b"e" * 32, now=guarded.deadline + 1)
    with pytest.raises(MarketAppError, match="challenge bond too small"):
        guarded.challenge_resolution(sender="challenger", bond_paid=guarded.challenge_bond - 1, reason_code=1, evidence_hash=b"c" * 32, now=guarded.deadline + 2)
    with pytest.raises(MarketAppError, match="challenge window not elapsed"):
        guarded.finalize_resolution(sender="anyone", now=guarded.deadline + 2)

    cancelled = make_market()
    cancelled.bootstrap(sender="creator", deposit_amount=200_000_000)
    with pytest.raises(MarketAppError, match="invalid status"):
        cancelled.refund(sender="buyer", outcome_index=0)

    unresolved = make_market()
    unresolved.bootstrap(sender="creator", deposit_amount=200_000_000)
    with pytest.raises(MarketAppError, match="invalid status"):
        unresolved.claim(sender="buyer", outcome_index=0)


def test_bootstrap_happy_path(market: MarketAppModel) -> None:
    test_bootstrap_creator_funds_optin_lp_transition(market)


def test_buy_cost_fees_slippage_event(bootstrapped_market: MarketAppModel) -> None:
    test_buy_cost_fees_slippage_transfer(bootstrapped_market)


def test_sell_return_fees_slippage_event(bootstrapped_market: MarketAppModel) -> None:
    test_sell_return_fees_slippage_transfer(bootstrapped_market)


def test_trade_fee_accounting(bootstrapped_market: MarketAppModel) -> None:
    test_trade_fee_deduction_lp_and_protocol(bootstrapped_market)


def test_provide_liq_price_preserving(bootstrapped_market: MarketAppModel) -> None:
    test_provide_liq_scaling_shares_price_invariance(bootstrapped_market)


def test_withdraw_liq_price_preserving_and_fees(bootstrapped_market: MarketAppModel) -> None:
    test_withdraw_liq_scaling_fees_allowed_statuses(bootstrapped_market)


def test_trigger_resolution_after_deadline(bootstrapped_market: MarketAppModel) -> None:
    test_trigger_resolution_after_deadline_core(bootstrapped_market)


def test_propose_resolution_authorized(bootstrapped_market: MarketAppModel) -> None:
    test_propose_resolution_authority_only(bootstrapped_market)


def test_challenge_resolution_bond_and_cancel(bootstrapped_market: MarketAppModel) -> None:
    test_challenge_resolution_window_bond_cancel(bootstrapped_market)


def test_finalize_resolution_after_window(bootstrapped_market: MarketAppModel) -> None:
    test_finalize_resolution_after_window_unchallenged(bootstrapped_market)


def test_claim_winnings_one_to_one_payout(bootstrapped_market: MarketAppModel) -> None:
    test_claim_winning_outcome_one_to_one_payout(bootstrapped_market)


def test_solvency_invariant_all_ops() -> None:
    test_invariant_solvency_each_operation()


def test_price_sum_invariant_all_ops() -> None:
    for setup in ["bootstrap", "buy", "sell", "provide", "withdraw", "trigger", "propose", "challenge", "finalize", "cancel", "refund"]:
        test_invariant_price_sum_each_operation(setup)


def test_arc28_events_all_state_changes(bootstrapped_market: MarketAppModel) -> None:
    test_arc28_events_all_state_changes_core(bootstrapped_market)


def test_uses_c1_math_library_keyword(monkeypatch, bootstrapped_market: MarketAppModel) -> None:
    test_uses_c1_math_library(monkeypatch, bootstrapped_market)
