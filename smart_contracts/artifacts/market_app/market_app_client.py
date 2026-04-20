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
        num_outcomes: algopy.arc4.UIntN[typing.Literal[64]],
        initial_b: algopy.arc4.UIntN[typing.Literal[64]],
        lp_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
        deadline: algopy.arc4.UIntN[typing.Literal[64]],
        question_hash: algopy.arc4.DynamicBytes,
        blueprint_cid: algopy.arc4.DynamicBytes,
        challenge_window_secs: algopy.arc4.UIntN[typing.Literal[64]],
        resolution_authority: algopy.arc4.Address,
        grace_period_secs: algopy.arc4.UIntN[typing.Literal[64]],
        market_admin: algopy.arc4.Address,
        protocol_config_id: algopy.arc4.UIntN[typing.Literal[64]],
        cancellable: algopy.arc4.Bool,
        lp_entry_max_price_fp: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def initialize(
        self,
    ) -> None:
        """
        Opt the app into the currency ASA. Called by the factory after funding.
        """

    @algopy.arc4.abimethod
    def post_comment(
        self,
        message: algopy.arc4.String,
    ) -> None: ...

    @algopy.arc4.abimethod
    def bootstrap(
        self,
        deposit_amount: algopy.arc4.UIntN[typing.Literal[64]],
        payment: algopy.gtxn.AssetTransferTransaction,
    ) -> None: ...

    @algopy.arc4.abimethod
    def buy(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        shares: algopy.arc4.UIntN[typing.Literal[64]],
        max_cost: algopy.arc4.UIntN[typing.Literal[64]],
        payment: algopy.gtxn.AssetTransferTransaction,
    ) -> None: ...

    @algopy.arc4.abimethod
    def sell(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        shares: algopy.arc4.UIntN[typing.Literal[64]],
        min_return: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def enter_lp_active(
        self,
        target_delta_b: algopy.arc4.UIntN[typing.Literal[64]],
        max_deposit: algopy.arc4.UIntN[typing.Literal[64]],
        expected_prices: algopy.arc4.DynamicArray[algopy.arc4.UIntN[typing.Literal[64]]],
        price_tolerance: algopy.arc4.UIntN[typing.Literal[64]],
        payment: algopy.gtxn.AssetTransferTransaction,
    ) -> None: ...

    @algopy.arc4.abimethod
    def trigger_resolution(
        self,
    ) -> None: ...

    @algopy.arc4.abimethod
    def propose_resolution(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        evidence_hash: algopy.arc4.DynamicBytes,
        payment: algopy.gtxn.AssetTransferTransaction,
    ) -> None: ...

    @algopy.arc4.abimethod
    def propose_early_resolution(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        evidence_hash: algopy.arc4.DynamicBytes,
        payment: algopy.gtxn.AssetTransferTransaction,
    ) -> None: ...

    @algopy.arc4.abimethod
    def challenge_resolution(
        self,
        payment: algopy.gtxn.AssetTransferTransaction,
        reason_code: algopy.arc4.UIntN[typing.Literal[64]],
        evidence_hash: algopy.arc4.DynamicBytes,
    ) -> None: ...

    @algopy.arc4.abimethod
    def register_dispute(
        self,
        dispute_ref_hash: algopy.arc4.DynamicBytes,
        backend_kind: algopy.arc4.UIntN[typing.Literal[64]],
        deadline: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None:
        """
        Register external dispute details. Resolution-authority-only, DISPUTED status only.
        """

    @algopy.arc4.abimethod
    def creator_resolve_dispute(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        ruling_hash: algopy.arc4.DynamicBytes,
    ) -> None: ...

    @algopy.arc4.abimethod
    def admin_resolve_dispute(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        ruling_hash: algopy.arc4.DynamicBytes,
    ) -> None: ...

    @algopy.arc4.abimethod
    def finalize_dispute(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        ruling_hash: algopy.arc4.DynamicBytes,
    ) -> None: ...

    @algopy.arc4.abimethod
    def abort_early_resolution(
        self,
        ruling_hash: algopy.arc4.DynamicBytes,
    ) -> None: ...

    @algopy.arc4.abimethod
    def cancel_dispute_and_market(
        self,
        ruling_hash: algopy.arc4.DynamicBytes,
    ) -> None:
        """
        Cancel a disputed market (irresolvable). Resolution-authority-only, DISPUTED status only.
        """

    @algopy.arc4.abimethod
    def finalize_resolution(
        self,
    ) -> None: ...

    @algopy.arc4.abimethod
    def claim(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        shares: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def cancel(
        self,
    ) -> None: ...

    @algopy.arc4.abimethod
    def withdraw_protocol_fees(
        self,
    ) -> None:
        """
        Withdraw accumulated protocol fees to the configured protocol treasury.
        """

    @algopy.arc4.abimethod
    def refund(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        shares: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None: ...

    @algopy.arc4.abimethod
    def withdraw_pending_payouts(
        self,
    ) -> None: ...

    @algopy.arc4.abimethod
    def reclaim_resolution_budget(
        self,
    ) -> None: ...
