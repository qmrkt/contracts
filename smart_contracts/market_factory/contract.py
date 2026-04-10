from algopy import Application, ARC4Contract, Global, Txn, UInt64, arc4, gtxn, op

from smart_contracts.abi_types import Hash32
from smart_contracts.market_app.contract import QuestionMarket

MAX_ACTIVE_LP_OUTCOMES = 8
CREATE_MARKET_MIN_FUNDING = 1_716_400


class MarketFactory(ARC4Contract):
    """Factory for market app deployment."""

    @arc4.baremethod(create="require")
    def create(self) -> None:
        pass

    @arc4.abimethod()
    def create_market(
        self,
        currency_asa: arc4.UInt64,
        question_hash: arc4.DynamicBytes,
        num_outcomes: arc4.UInt64,
        initial_liquidity_b: arc4.UInt64,
        lp_fee_bps: arc4.UInt64,
        main_blueprint_hash: Hash32,
        dispute_blueprint_hash: Hash32,
        deadline: arc4.UInt64,
        challenge_window_secs: arc4.UInt64,
        market_admin: arc4.Address,
        grace_period_secs: arc4.UInt64,
        cancellable: arc4.Bool,
        lp_entry_max_price_fp: arc4.UInt64,
        funding: gtxn.PaymentTransaction,
    ) -> None:
        assert funding.sender == Txn.sender
        assert funding.receiver == Global.current_application_address
        assert funding.amount >= CREATE_MARKET_MIN_FUNDING
        assert funding.rekey_to == Global.zero_address
        assert funding.close_remainder_to == Global.zero_address

        assert num_outcomes.as_uint64() <= MAX_ACTIVE_LP_OUTCOMES
        protocol_config_id = Txn.applications(1).id
        linked_factory_id, linked_factory_exists = op.AppGlobal.get_ex_uint64(Application(protocol_config_id), b"mfi")
        assert linked_factory_id == Global.current_application_id.id

        arc4.arc4_create(
            QuestionMarket.create,
            arc4.Address(Txn.sender),
            currency_asa,
            num_outcomes,
            initial_liquidity_b,
            lp_fee_bps,
            deadline,
            question_hash,
            main_blueprint_hash,
            dispute_blueprint_hash,
            challenge_window_secs,
            arc4.Address(Global.creator_address),
            grace_period_secs,
            market_admin,
            arc4.UInt64(protocol_config_id),
            cancellable,
            lp_entry_max_price_fp,
        )
