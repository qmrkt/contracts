# This file is auto-generated, do not modify
# flake8: noqa
# fmt: off
import typing

import algopy


class MarketFactory(algopy.arc4.ARC4Client, typing.Protocol):
    """
    Factory for atomic market deployment.

        Stores QuestionMarket approval/clear programs in boxes.
        Creates markets via arc4_create with compiled bytecode from boxes.
    
    """
    @algopy.arc4.abimethod
    def noop(
        self,
    ) -> None:
        """
        No-op for box IO budget pooling in transaction groups.
        """

    @algopy.arc4.abimethod
    def create_program_box(
        self,
        box_key: algopy.arc4.DynamicBytes,
        size: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None:
        """
        Create or recreate a program box with the given size. Creator-only.
        """

    @algopy.arc4.abimethod
    def write_program_chunk(
        self,
        box_key: algopy.arc4.DynamicBytes,
        offset: algopy.arc4.UIntN[typing.Literal[64]],
        data: algopy.arc4.DynamicBytes,
    ) -> None:
        """
        Write a chunk of bytecode to a program box at the given offset. Creator-only.
        """

    @algopy.arc4.abimethod
    def opt_into_asset(
        self,
        asset: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None:
        """
        Opt factory into a currency ASA so it can receive and forward deposits. Creator-only.
        """

    @algopy.arc4.abimethod
    def create_market(
        self,
        currency_asa: algopy.arc4.UIntN[typing.Literal[64]],
        question_hash: algopy.arc4.DynamicBytes,
        num_outcomes: algopy.arc4.UIntN[typing.Literal[64]],
        initial_liquidity_b: algopy.arc4.UIntN[typing.Literal[64]],
        lp_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
        blueprint_cid: algopy.arc4.DynamicBytes,
        deadline: algopy.arc4.UIntN[typing.Literal[64]],
        challenge_window_secs: algopy.arc4.UIntN[typing.Literal[64]],
        market_admin: algopy.arc4.Address,
        grace_period_secs: algopy.arc4.UIntN[typing.Literal[64]],
        cancellable: algopy.arc4.Bool,
        lp_entry_max_price_fp: algopy.arc4.UIntN[typing.Literal[64]],
        deposit_amount: algopy.arc4.UIntN[typing.Literal[64]],
        algo_funding: algopy.gtxn.PaymentTransaction,
        usdc_funding: algopy.gtxn.AssetTransferTransaction,
    ) -> algopy.arc4.UIntN[typing.Literal[64]]: ...
