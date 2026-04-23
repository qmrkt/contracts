from __future__ import annotations

from pathlib import Path

from smart_contracts.market_app.active_lp_model import ActiveLpMarketAppModel
from smart_contracts.market_app.model import MarketAppModel

CONTRACT_SOURCE = Path(__file__).resolve().parents[1] / "smart_contracts" / "market_app" / "contract.py"
MODEL_SOURCE = Path(__file__).resolve().parents[1] / "smart_contracts" / "market_app" / "model.py"


def make_market(*, num_outcomes: int = 3, deadline: int = 10_000, cancellable: bool = True) -> MarketAppModel:
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
        proposer_fee_bps=0,
        proposer_fee_floor_bps=0,
        grace_period_secs=3_600,
        market_admin="admin",
        cancellable=cancellable,
    )


def make_active_lp_market(*, num_outcomes: int = 3, deadline: int = 10_000) -> ActiveLpMarketAppModel:
    return ActiveLpMarketAppModel(
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
        proposer_fee_bps=0,
        proposer_fee_floor_bps=0,
        grace_period_secs=3_600,
        market_admin="admin",
    )


def source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def buy_one(market: MarketAppModel, sender: str = "trader", outcome_index: int = 0, now: int = 5_000) -> dict[str, int]:
    return market.buy(sender=sender, outcome_index=outcome_index, max_cost=10_000_000, now=now)


def resolve_market(market: MarketAppModel, *, challenged: bool = False, outcome_index: int = 0) -> None:
    market.trigger_resolution(sender="anyone", now=market.deadline)
    market.propose_resolution(
        sender="resolver",
        outcome_index=outcome_index,
        evidence_hash=b"e" * 32,
        now=market.deadline + 1,
    )
    if challenged:
        market.challenge_resolution(
            sender="challenger",
            bond_paid=market.challenge_bond,
            reason_code=1,
            evidence_hash=b"c" * 32,
            now=market.deadline + 2,
        )
    else:
        market.finalize_resolution(sender="anyone", now=market.deadline + 1 + market.challenge_window_secs)


def bootstrap_and_buy() -> MarketAppModel:
    market = make_market()
    market.bootstrap(sender="creator", deposit_amount=200_000_000)
    buy_one(market, sender="buyer", outcome_index=0)
    return market
