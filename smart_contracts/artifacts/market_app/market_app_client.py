# This file is auto-generated, do not modify
# flake8: noqa
# fmt: off
import typing

import algopy


class QuestionMarket(algopy.arc4.ARC4Client, typing.Protocol):
    @algopy.arc4.abimethod
    def claim_lp_fees(
        self,
    ) -> None: ...

    @algopy.arc4.abimethod
    def withdraw_lp_fees(
        self,
        amount: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def claim_lp_residual(
        self,
    ) -> None: ...

    @algopy.arc4.abimethod(create='require')
    def create(
        self,
        creator: algopy.arc4.Address,
        currency_asa: algopy.arc4.UIntN[typing.Literal[64]],
        num_outcomes: algopy.arc4.UIntN[typing.Literal[64]]
    ) -> None: ...

    @algopy.arc4.abimethod
    def buy(
        self,
        amount: algopy.arc4.UIntN[typing.Literal[64]],
        outcome: algopy.arc4.UIntN[typing.Literal[64]]
    ) -> None: ...