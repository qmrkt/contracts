# This file is auto-generated, do not modify
# flake8: noqa
# fmt: off
import typing

import algopy


class ProtocolConfig(algopy.arc4.ARC4Client, typing.Protocol):
    """
    Protocol-wide governance/configuration state stored in global state.
    """
    @algopy.arc4.abimethod(create='require')
    def create(
        self,
        admin: algopy.arc4.Address,
        min_bootstrap_deposit: algopy.arc4.UIntN[typing.Literal[64]],
        challenge_bond: algopy.arc4.UIntN[typing.Literal[64]],
        proposal_bond: algopy.arc4.UIntN[typing.Literal[64]],
        default_b: algopy.arc4.UIntN[typing.Literal[64]],
        protocol_fee_ceiling_bps: algopy.arc4.UIntN[typing.Literal[64]],
        protocol_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
        protocol_treasury: algopy.arc4.Address,
        market_factory_id: algopy.arc4.UIntN[typing.Literal[64]],
        max_outcomes: algopy.arc4.UIntN[typing.Literal[64]],
        min_challenge_window_secs: algopy.arc4.UIntN[typing.Literal[64]],
        min_grace_period_secs: algopy.arc4.UIntN[typing.Literal[64]],
        max_lp_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_admin(
        self,
        admin: algopy.arc4.Address,
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_min_bootstrap_deposit(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_challenge_bond(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_proposal_bond(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_default_b(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_protocol_fee_bps(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_protocol_fee_ceiling_bps(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_protocol_treasury(
        self,
        value: algopy.arc4.Address,
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_market_factory_id(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_max_outcomes(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_min_challenge_window_secs(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_min_grace_period_secs(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def update_max_lp_fee_bps(
        self,
        value: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def op_up(
        self,
        count: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def noop(
        self,
    ) -> None: ...
