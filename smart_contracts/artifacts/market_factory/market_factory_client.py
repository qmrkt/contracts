# This file is auto-generated, do not modify
# flake8: noqa
# fmt: off
import typing

import algopy


class MarketFactory(algopy.arc4.ARC4Client, typing.Protocol):
    """
    Factory/registry for market app deployment.
    """
    @algopy.arc4.abimethod(create='require')
    def create(
        self,
        protocol_config_id: algopy.arc4.UIntN[typing.Literal[64]],
        resolution_authority: algopy.arc4.Address,
    ) -> None: ...

    @algopy.arc4.abimethod
    def create_market(
        self,
        creator: algopy.arc4.Address,
        currency_asa: algopy.arc4.UIntN[typing.Literal[64]],
        question_hash: algopy.arc4.DynamicBytes,
        num_outcomes: algopy.arc4.UIntN[typing.Literal[64]],
        initial_liquidity_b: algopy.arc4.UIntN[typing.Literal[64]],
        lp_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
        main_blueprint_hash: algopy.arc4.DynamicBytes,
        dispute_blueprint_hash: algopy.arc4.DynamicBytes,
        deadline: algopy.arc4.UIntN[typing.Literal[64]],
        challenge_window_secs: algopy.arc4.UIntN[typing.Literal[64]],
        market_admin: algopy.arc4.Address,
        proposal_bond: algopy.arc4.UIntN[typing.Literal[64]],
        grace_period_secs: algopy.arc4.UIntN[typing.Literal[64]],
        cancellable: algopy.arc4.Bool,
        bootstrap_deposit: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...
