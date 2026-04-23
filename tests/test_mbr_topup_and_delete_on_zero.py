"""Phase 1 fix tests for MBR-drain DoS (bug/mbr-drain-dos).

Covers the new contract behavior:

1. MBR top-up enforcement on buy. Strict-equality, sender, and receiver
   validation; rekey/close-remainder-to are intentionally tolerated because
   they only affect the trader's own payment account.

2. No-delete-on-zero behavior: sell / claim / refund leave zero-valued
   us:/uc: boxes in place because the approval program is at the AVM page
   limit. Trader-funded MBR stays in the app account until app deletion.

3. Attack resistance: many fresh wallets each first-buying a fresh outcome
   never drains the market app's prefund, because each trader funds their
   own MBR.

The pure-Python `MarketAppModel` cannot simulate boxes or inner transactions,
so these tests run against the actual `QuestionMarket` contract via
`algopy_testing_context()`, matching the pattern in
`tests/test_market_app_contract_runtime.py`.
"""
from __future__ import annotations

import algosdk.account
import algosdk.logic
import pytest
from algopy import Account, Application, Asset, Global, UInt64, arc4, op
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    BOX_KEY_USER_COST_BASIS,
    BOX_KEY_USER_SHARES,
    COST_BOX_MBR,
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    PRICE_TOLERANCE_BASE,
    QuestionMarket,
    SHARE_BOX_MBR,
    SHARE_UNIT,
)
from smart_contracts.lmsr_math import lmsr_prices
from smart_contracts.protocol_config.contract import (
    KEY_CHALLENGE_BOND,
    KEY_CHALLENGE_BOND_BPS,
    KEY_CHALLENGE_BOND_CAP,
    KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    KEY_MARKET_FACTORY_ID,
    KEY_MAX_ACTIVE_LP_V4_OUTCOMES,
    KEY_MIN_CHALLENGE_WINDOW_SECS,
    KEY_PROPOSAL_BOND,
    KEY_PROPOSAL_BOND_BPS,
    KEY_PROPOSAL_BOND_CAP,
    KEY_PROPOSER_FEE_BPS,
    KEY_PROPOSER_FEE_FLOOR_BPS,
    KEY_PROTOCOL_FEE_BPS,
    KEY_PROTOCOL_TREASURY,
)

CURRENCY_ASA = 31_566_704
PROTOCOL_CONFIG_APP_ID = 77
DEFAULT_FACTORY_APP_ID = 8_001


# ───────────────────────── fixture helpers ─────────────────────────


def _addr() -> str:
    return algosdk.account.generate_account()[1]


def _app_addr(contract: QuestionMarket) -> str:
    return algosdk.logic.get_application_address(contract.__app_id__)


def _usdc_payment(context, contract, sender, amount):
    return context.any.txn.asset_transfer(
        sender=Account(sender),
        asset_receiver=Account(_app_addr(contract)),
        xfer_asset=Asset(CURRENCY_ASA),
        asset_amount=UInt64(amount),
    )


def _mbr_payment(context, contract, sender, amount, *, receiver=None, rekey_to=None, close_remainder_to=None):
    zero = Global.zero_address
    return context.any.txn.payment(
        sender=Account(sender),
        receiver=Account(receiver) if receiver is not None else Account(_app_addr(contract)),
        amount=UInt64(amount),
        rekey_to=Account(rekey_to) if rekey_to is not None else zero,
        close_remainder_to=Account(close_remainder_to) if close_remainder_to is not None else zero,
    )


def _call_as(context, sender, method, *args, latest_timestamp=None):
    if latest_timestamp is not None:
        context.ledger.patch_global_fields(latest_timestamp=latest_timestamp)
    context._default_sender = Account(sender)
    deferred = context.txn.defer_app_call(method, *args)
    with context.txn.create_group([deferred]):
        return method(*args)


def _seed_protocol(context):
    app = context.any.application(id=PROTOCOL_CONFIG_APP_ID)
    context.ledger.set_global_state(app, KEY_MIN_CHALLENGE_WINDOW_SECS, 86_400)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND, 10_000_000)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND, 10_000_000)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND_BPS, 500)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND_BPS, 500)
    context.ledger.set_global_state(app, KEY_CHALLENGE_BOND_CAP, 100_000_000)
    context.ledger.set_global_state(app, KEY_PROPOSAL_BOND_CAP, 100_000_000)
    context.ledger.set_global_state(app, KEY_PROPOSER_FEE_BPS, 0)
    context.ledger.set_global_state(app, KEY_PROPOSER_FEE_FLOOR_BPS, 0)
    context.ledger.set_global_state(app, KEY_PROTOCOL_FEE_BPS, 50)
    context.ledger.set_global_state(app, KEY_PROTOCOL_TREASURY, Account(_addr()).bytes.value)
    context.ledger.set_global_state(app, KEY_DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP, 150_000)
    context.ledger.set_global_state(app, KEY_MAX_ACTIVE_LP_V4_OUTCOMES, 8)
    context.ledger.set_global_state(app, KEY_MARKET_FACTORY_ID, DEFAULT_FACTORY_APP_ID)
    return app


def _create(context, contract, creator):
    protocol_app = _seed_protocol(context)
    args = dict(
        creator=arc4.Address(creator),
        currency_asa=arc4.UInt64(CURRENCY_ASA),
        num_outcomes=arc4.UInt64(3),
        initial_b=arc4.UInt64(100_000_000),
        lp_fee_bps=arc4.UInt64(200),
        deadline=arc4.UInt64(100_000),
        question_hash=arc4.DynamicBytes(b"q" * 32),
        blueprint_cid=arc4.DynamicBytes(b"ipfs://blueprint-cid"),
        challenge_window_secs=arc4.UInt64(86_400),
        resolution_authority=arc4.Address(creator),
        grace_period_secs=arc4.UInt64(3_600),
        market_admin=arc4.Address(creator),
        protocol_config_id=arc4.UInt64(PROTOCOL_CONFIG_APP_ID),
        cancellable=arc4.Bool(True),
        lp_entry_max_price_fp=arc4.UInt64(DEFAULT_LP_ENTRY_MAX_PRICE_FP),
    )
    app_data = context.ledger._app_data[contract.__app_id__]
    app_data.fields["creator"] = Account(algosdk.logic.get_application_address(DEFAULT_FACTORY_APP_ID))
    context.ledger.patch_global_fields(latest_timestamp=1)
    context._default_sender = Account(creator)
    deferred = context.txn.defer_app_call(contract.create, **args)
    deferred._txns[-1].fields["apps"] = (protocol_app,)
    with context.txn.create_group([deferred]):
        contract.create(**args)


def _bootstrap(context, contract, creator, deposit=200_000_000):
    pmt = _usdc_payment(context, contract, creator, deposit)
    _call_as(context, creator, contract.bootstrap, arc4.UInt64(deposit), pmt, latest_timestamp=1)


def _ready(context, creator):
    contract = QuestionMarket()
    _create(context, contract, creator)
    _bootstrap(context, contract, creator)
    return contract


@pytest.fixture()
def disable_emit(monkeypatch):
    monkeypatch.setattr(contract_module.arc4, "emit", lambda *a, **k: None)


# ───────────────────────── 1) MBR top-up enforcement ─────────────────────────


class TestMBRTopupEnforcement:
    """Strict-equality MBR validation on buy."""

    def test_buy_first_time_requires_share_plus_cost_exact(self, disable_emit):
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
            # Exact MBR — succeeds.
            mbr_exact = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt, mbr_exact, latest_timestamp=5_000,
            )
            assert int(c.user_outcome_shares_box[Account(buyer).bytes + op.itob(UInt64(0))]) == SHARE_UNIT

    def test_buy_underpay_mbr_by_one_reverts(self, disable_emit):
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
            short = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR - 1)
            with pytest.raises(AssertionError):
                _call_as(
                    ctx, buyer, c.buy,
                    arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                    pmt, short, latest_timestamp=5_000,
                )

    def test_buy_overpay_mbr_reverts_strict_equality(self, disable_emit):
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
            too_much = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR + 1)
            with pytest.raises(AssertionError):
                _call_as(
                    ctx, buyer, c.buy,
                    arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                    pmt, too_much, latest_timestamp=5_000,
                )

    def test_buy_repeat_also_requires_full_mbr(self, disable_emit):
        """Size-cap compromise: the contract doesn't check box existence, so
        every buy (first or repeat) must pay SHARE+COST. Overpayment on
        repeat buys accumulates as app-account slack."""
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt1 = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr1 = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt1, mbr1, latest_timestamp=5_000,
            )
            # Repeat buy on same (buyer, outcome) — boxes already exist but
            # contract still requires SHARE+COST.
            pmt2 = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr2 = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt2, mbr2, latest_timestamp=5_001,
            )
            assert int(c.user_outcome_shares_box[Account(buyer).bytes + op.itob(UInt64(0))]) == 2 * SHARE_UNIT

    def test_buy_repeat_passing_zero_mbr_reverts(self, disable_emit):
        """Repeat buy with MBR=0 reverts because contract demands SHARE+COST
        on every buy regardless of existing-box state."""
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt1 = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr1 = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt1, mbr1, latest_timestamp=5_000,
            )
            pmt2 = _usdc_payment(ctx, c, buyer, 50_000_000)
            wrong = _mbr_payment(ctx, c, buyer, 0)
            with pytest.raises(AssertionError):
                _call_as(
                    ctx, buyer, c.buy,
                    arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                    pmt2, wrong, latest_timestamp=5_001,
                )

    def test_buy_mbr_sender_must_equal_txn_sender(self, disable_emit):
        creator, buyer, imposter = _addr(), _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
            # MBR paid by someone other than the buyer — reverts.
            mbr = _mbr_payment(ctx, c, imposter, SHARE_BOX_MBR + COST_BOX_MBR)
            with pytest.raises(AssertionError):
                _call_as(
                    ctx, buyer, c.buy,
                    arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                    pmt, mbr, latest_timestamp=5_000,
                )

    def test_buy_mbr_receiver_must_be_app_account(self, disable_emit):
        creator, buyer, wrong_receiver = _addr(), _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr = _mbr_payment(
                ctx,
                c,
                buyer,
                SHARE_BOX_MBR + COST_BOX_MBR,
                receiver=wrong_receiver,
            )
            with pytest.raises(AssertionError):
                _call_as(
                    ctx, buyer, c.buy,
                    arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                    pmt, mbr, latest_timestamp=5_000,
                )

    # Rekey / close_remainder_to checks are omitted from the contract-side
    # validation to keep the approval program under the 4-page AVM cap. Setting
    # either field only affects the trader's own payment account.
    def test_buy_tolerates_rekey_on_mbr_payment(self, disable_emit):
        creator, buyer, anywhere = _addr(), _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr = _mbr_payment(
                ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR, rekey_to=anywhere
            )
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt, mbr, latest_timestamp=5_000,
            )


# ───────────────────────── 2) Delete-on-zero removed to fit cap ─────────────────────────


class TestNoDeleteOnZero:
    """Size-cap compromise: delete-on-zero was removed from sell to fit
    within the 4-page AVM approval-program cap (8192 bytes). After a full
    exit the us:/uc: boxes stay in the app's storage as zero-valued stubs.

    This doesn't weaken the DoS fix:
      - Pay-per-box on buy ensures every new box is funded by the trader.
      - The market app's MBR headroom never depletes from trader activity.
      - Dead boxes accumulate but cost the protocol nothing (box MBR
        stays locked in the app account).

    The feature lost is *box-slot recycling*, which would let a fully-exited
    trader free up their MBR and make the slot reusable. That's a cost to
    traders (one-time ~0.05 ALGO fee per (trader, outcome) pair) not the
    protocol, and can be added back in a future contract version if the
    budget allows."""

    def test_sell_to_zero_does_not_delete_boxes(self, disable_emit):
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            # Buy 1 share (creates boxes).
            pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt, mbr, latest_timestamp=5_000,
            )
            key = Account(buyer).bytes + op.itob(UInt64(0))
            assert key in c.user_outcome_shares_box
            assert key in c.user_cost_basis_box

            # Sell the full position — boxes stay in storage (zero-valued stub).
            _call_as(
                ctx, buyer, c.sell,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(0),
                latest_timestamp=5_001,
            )
            assert key in c.user_outcome_shares_box
            assert key in c.user_cost_basis_box
            assert int(c.user_outcome_shares_box[key]) == 0

    def test_partial_sell_keeps_boxes(self, disable_emit):
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            # Buy 2 shares.
            pmt = _usdc_payment(ctx, c, buyer, 100_000_000)
            mbr = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT * 2), arc4.UInt64(100_000_000),
                pmt, mbr, latest_timestamp=5_000,
            )
            # Sell 1 share — position stays at 1.
            _call_as(
                ctx, buyer, c.sell,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(0),
                latest_timestamp=5_001,
            )
            key = Account(buyer).bytes + op.itob(UInt64(0))
            assert key in c.user_outcome_shares_box
            assert int(c.user_outcome_shares_box[key]) == SHARE_UNIT

    def test_sell_and_rebuy_both_require_full_mbr(self, disable_emit):
        """A trader who fully exits and then re-enters pays MBR twice:
        once on first buy (box created) and once more on re-buy (box was
        never deleted — stays as a zero-valued stub). This is the
        size-cap-compromise tradeoff."""
        creator, buyer = _addr(), _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            # First buy.
            pmt1 = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr1 = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt1, mbr1, latest_timestamp=5_000,
            )
            # Full exit; boxes stay as zero-valued stubs.
            _call_as(
                ctx, buyer, c.sell,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(0),
                latest_timestamp=5_001,
            )
            # Re-buy: contract still requires full MBR even though boxes exist.
            pmt2 = _usdc_payment(ctx, c, buyer, 50_000_000)
            mbr2 = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
            _call_as(
                ctx, buyer, c.buy,
                arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                pmt2, mbr2, latest_timestamp=5_002,
            )
            assert Account(buyer).bytes + op.itob(UInt64(0)) in c.user_outcome_shares_box


# ───────────────────────── 3) Attack resistance ─────────────────────────


class TestAttackResistance:
    """The old 1.616 ALGO prefund bricked after ~30 first-touch buys. With
    per-action MBR top-up, there is no finite cap — the market scales with
    participation."""

    def test_many_fresh_wallets_each_first_buy_never_bricks_market(self, disable_emit):
        """50 fresh wallets each first-buy outcome 0 on a minimally-funded market.

        In the pre-fix contract this would revert around the 30th buy as the
        app account's MBR headroom depletes. With the fix, each buy is
        self-funding, so 50 (and arbitrarily many beyond) succeed."""
        creator = _addr()
        with algopy_testing_context() as ctx:
            c = _ready(ctx, creator)
            # 50 distinct traders, each makes a first-touch buy → each creates
            # a new (us:, uc:) pair whose MBR is paid by the trader themselves.
            for i in range(50):
                buyer = _addr()
                pmt = _usdc_payment(ctx, c, buyer, 50_000_000)
                mbr = _mbr_payment(ctx, c, buyer, SHARE_BOX_MBR + COST_BOX_MBR)
                _call_as(
                    ctx, buyer, c.buy,
                    arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), arc4.UInt64(50_000_000),
                    pmt, mbr, latest_timestamp=5_000 + i,
                )
            # All 50 went through; boxes outnumber the old 21-slot cap.
            assert int(c.total_outstanding_cost_basis.value) > 0
