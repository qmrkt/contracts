# This file is auto-generated, do not modify
# flake8: noqa
# fmt: off
import typing

import algopy


class MarketFactory(algopy.arc4.ARC4Client, typing.Protocol):
    """
    Factory for market app deployment.
    """
    @algopy.arc4.abimethod
    def create_market(
        self,
        currency_asa: algopy.arc4.UIntN[typing.Literal[64]],
        question_hash: algopy.arc4.DynamicBytes,
        num_outcomes: algopy.arc4.UIntN[typing.Literal[64]],
        initial_liquidity_b: algopy.arc4.UIntN[typing.Literal[64]],
        lp_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
        main_blueprint_hash: algopy.arc4.StaticArray[algopy.arc4.Byte, typing.Literal[32]],
        dispute_blueprint_hash: algopy.arc4.StaticArray[algopy.arc4.Byte, typing.Literal[32]],
        deadline: algopy.arc4.UIntN[typing.Literal[64]],
        challenge_window_secs: algopy.arc4.UIntN[typing.Literal[64]],
        market_admin: algopy.arc4.Address,
        grace_period_secs: algopy.arc4.UIntN[typing.Literal[64]],
        cancellable: algopy.arc4.Bool,
        lp_entry_max_price_fp: algopy.arc4.UIntN[typing.Literal[64]],
        funding: algopy.gtxn.PaymentTransaction,
    ) -> None: ...
