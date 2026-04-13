"""Minimal ABI stub for QuestionMarket methods called by the factory.

This stub defines ONLY the method signatures that the factory needs to call
via inner transactions. It does NOT import or reference the real QuestionMarket
contract, keeping the factory's compiled program small.
"""

from algopy import ARC4Contract, arc4


class MarketStub(ARC4Contract):
    """ABI-only stub: method signatures match QuestionMarket but no implementation."""

    @arc4.abimethod(create="require")
    def create(
        self,
        creator: arc4.Address,
        currency_asa: arc4.UInt64,
        num_outcomes: arc4.UInt64,
        initial_liquidity_b: arc4.UInt64,
        lp_fee_bps: arc4.UInt64,
        deadline: arc4.UInt64,
        question_hash: arc4.DynamicBytes,
        blueprint_cid: arc4.DynamicBytes,
        challenge_window_secs: arc4.UInt64,
        resolution_authority: arc4.Address,
        grace_period_secs: arc4.UInt64,
        market_admin: arc4.Address,
        protocol_config_id: arc4.UInt64,
        cancellable: arc4.Bool,
        lp_entry_max_price_fp: arc4.UInt64,
    ) -> None:
        pass

    @arc4.abimethod()
    def initialize(self) -> None:
        pass

    @arc4.abimethod()
    def bootstrap(
        self,
        deposit_amount: arc4.UInt64,
    ) -> None:
        pass
