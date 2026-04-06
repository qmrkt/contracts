# This file is auto-generated, do not modify
# flake8: noqa
# fmt: off
import typing

import algopy


class QuestionMarket(algopy.arc4.ARC4Client, typing.Protocol):
    @algopy.arc4.abimethod(create='require')
    def create(
        self,
        creator: algopy.arc4.Address,
        currency_asa: algopy.arc4.UIntN[typing.Literal[64]],
        num_outcomes: algopy.arc4.UIntN[typing.Literal[64]],
        initial_b: algopy.arc4.UIntN[typing.Literal[64]],
        lp_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
        protocol_fee_bps: algopy.arc4.UIntN[typing.Literal[64]],
        deadline: algopy.arc4.UIntN[typing.Literal[64]],
        question_hash: algopy.arc4.DynamicBytes,
        main_blueprint_hash: algopy.arc4.DynamicBytes,
        dispute_blueprint_hash: algopy.arc4.DynamicBytes,
        challenge_window_secs: algopy.arc4.UIntN[typing.Literal[64]],
        resolution_authority: algopy.arc4.Address,
        challenge_bond: algopy.arc4.UIntN[typing.Literal[64]],
        proposal_bond: algopy.arc4.UIntN[typing.Literal[64]],
        grace_period_secs: algopy.arc4.UIntN[typing.Literal[64]],
        market_admin: algopy.arc4.Address,
        protocol_config_id: algopy.arc4.UIntN[typing.Literal[64]],
        factory_id: algopy.arc4.UIntN[typing.Literal[64]],
        cancellable: algopy.arc4.Bool,
    ) -> None: ...

    @algopy.arc4.abimethod
    def post_comment(
        self,
        message: algopy.arc4.String,
    ) -> None: ...

    @algopy.arc4.abimethod
    def opt_in_to_asa(
        self,
        asset: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None:
        """
        Opt contract into an ASA. Called by creator before bootstrap for currency_asa
        and each outcome ASA. SDK calls this N+1 times.
        """

    @algopy.arc4.abimethod
    def register_outcome_asa(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        asset: algopy.arc4.UIntN[typing.Literal[64]],
    ) -> None:
        """
        Register an outcome ASA ID in box storage. Called by creator before bootstrap.
        """

    @algopy.arc4.abimethod
    def store_main_blueprint(
        self,
        data: algopy.arc4.DynamicBytes,
    ) -> None:
        """
        Store main resolution blueprint on-chain. Creator-only, CREATED status only.
        Must be called before bootstrap. Size capped by MAX_BLUEPRINT_SIZE.
        """

    @algopy.arc4.abimethod
    def store_dispute_blueprint(
        self,
        data: algopy.arc4.DynamicBytes,
    ) -> None:
        """
        Store dispute resolution blueprint on-chain. Creator-only, CREATED status only.
        Must be called before bootstrap. Size capped by MAX_BLUEPRINT_SIZE.
        """

    @algopy.arc4.abimethod
    def initialize(
        self,
    ) -> None:
        """
        Prepare a CREATED market for bootstrap in a single call.
        This opts the app into the currency ASA and creates outcome ASAs internally. Existing blueprint storage and bootstrap methods can then be grouped after it to activate the market atomically.
        """

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
        asa_payment: algopy.gtxn.AssetTransferTransaction,
    ) -> None: ...

    @algopy.arc4.abimethod
    def provide_liq(
        self,
        deposit_amount: algopy.arc4.UIntN[typing.Literal[64]],
        payment: algopy.gtxn.AssetTransferTransaction,
    ) -> None: ...

    @algopy.arc4.abimethod
    def withdraw_liq(
        self,
        shares_to_burn: algopy.arc4.UIntN[typing.Literal[64]],
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
    ) -> None:
        """
        Creator adjudicates the dispute. Creator-only, DISPUTED status only.
        """

    @algopy.arc4.abimethod
    def admin_resolve_dispute(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        ruling_hash: algopy.arc4.DynamicBytes,
    ) -> None:
        """
        Market admin adjudicates the dispute as final fallback. Admin-only, DISPUTED status only.
        """

    @algopy.arc4.abimethod
    def finalize_dispute(
        self,
        outcome_index: algopy.arc4.UIntN[typing.Literal[64]],
        ruling_hash: algopy.arc4.DynamicBytes,
    ) -> None:
        """
        Finalize a dispute with a ruling. Resolution-authority-only, DISPUTED status only.
        """

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
        Withdraw accumulated protocol fees to the governed protocol treasury. Admin-only.
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
