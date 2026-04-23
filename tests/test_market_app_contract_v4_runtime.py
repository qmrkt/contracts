import pytest
from algopy import Account, UInt64, arc4
from algopy_testing import algopy_testing_context

import smart_contracts.market_app.contract as contract_module
from smart_contracts.market_app.contract import (
    COST_BOX_MBR,
    DEFAULT_LP_ENTRY_MAX_PRICE_FP,
    DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP,
    PRICE_TOLERANCE_BASE,
    QuestionMarket,
    SHARE_BOX_MBR,
    SHARE_UNIT,
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    STATUS_RESOLVED,
)
from smart_contracts.lmsr_math import SCALE, lmsr_prices

from .test_market_app_contract_runtime import (
    contract_q,
    contract_user_shares,
    create_contract,
    ensure_blueprint_cid,
    last_inner_asset_transfers,
    make_address,
    make_mbr_payment,
    make_usdc_payment,
    opt_in_market,
    seed_protocol_config_state,
    call_as,
)


def _price_array(values: list[int]) -> arc4.DynamicArray[arc4.UInt64]:
    return arc4.DynamicArray[arc4.UInt64](*(arc4.UInt64(value) for value in values))


def _withdrawable_fee_surplus(contract: QuestionMarket, sender: str) -> int:
    return int(contract.withdrawable_fee_surplus.get(Account(sender), default=UInt64(0)))


def _lp_weighted_entry_sum(contract: QuestionMarket, sender: str) -> int:
    return int(contract.lp_weighted_entry_sum.get(Account(sender), default=UInt64(0)))


def _normalized_residual_weight(contract: QuestionMarket, sender: str) -> int:
    shares = int(contract.lp_shares.get(Account(sender), default=UInt64(0)))
    if shares <= 0:
        return 0
    settlement_timestamp = int(contract.settlement_timestamp.value)
    activation_timestamp = int(contract.activation_timestamp.value)
    if settlement_timestamp <= activation_timestamp + 1:
        return shares
    window = settlement_timestamp - activation_timestamp - 1
    premium_units = max(0, (settlement_timestamp - 1) * shares - _lp_weighted_entry_sum(contract, sender))
    premium = (int(contract.residual_linear_lambda_fp.value) * premium_units) // (SCALE * window)
    return shares + premium


def _activate_v4_market(context, contract: QuestionMarket, *, creator: str, resolver: str, deadline: int = 10_000) -> None:
    create_contract(context, contract, creator=creator, resolver=resolver, deadline=deadline)
    seed_protocol_config_state(context, admin=creator, treasury=creator)
    ensure_blueprint_cid(contract)
    payment = make_usdc_payment(context, contract, creator, 200_000_000)
    call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), payment, latest_timestamp=1)
    opt_in_market(context, contract, creator, latest_timestamp=2)


@pytest.fixture()
def disable_arc4_emit(monkeypatch):
    monkeypatch.setattr(contract_module.arc4, "emit", lambda *args, **kwargs: None)


def test_contract_create_and_bootstrap_use_delta_b_units(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        _activate_v4_market(context, contract, creator=creator, resolver=resolver)

        assert int(contract.status.value) == STATUS_ACTIVE
        assert int(contract.residual_linear_lambda_fp.value) == DEFAULT_RESIDUAL_LINEAR_LAMBDA_FP
        assert int(contract.activation_timestamp.value) == 1
        assert int(contract.pool_balance.value) == 200_000_000
        assert int(contract.lp_shares_total.value) == int(contract.b.value)
        assert int(contract.lp_shares.get(Account(creator), default=UInt64(0))) == int(contract.b.value)
        assert int(contract.total_lp_weighted_entry_sum.value) == int(contract.b.value)
        assert _lp_weighted_entry_sum(contract, creator) == int(contract.b.value)


def test_contract_active_lp_entry_preserves_prices_and_disables_legacy_lp_methods(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        _activate_v4_market(context, contract, creator=creator, resolver=resolver)

        buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(2),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            buy_payment,
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        before_prices = lmsr_prices(contract_q(contract), int(contract.b.value))

        lp_payment = make_usdc_payment(context, contract, lp2, 100_000_000)
        call_as(
            context,
            lp2,
            contract.enter_lp_active,
            arc4.UInt64(25_000_000),
            arc4.UInt64(100_000_000),
            _price_array(before_prices),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            lp_payment,
            latest_timestamp=6_000,
        )
        after_prices = lmsr_prices(contract_q(contract), int(contract.b.value))

        assert int(contract.lp_shares_total.value) == 125_000_000
        assert int(contract.lp_shares.get(Account(lp2), default=UInt64(0))) == 25_000_000
        assert _lp_weighted_entry_sum(contract, lp2) == 25_000_000 * 6_000
        assert max(abs(before - after) for before, after in zip(before_prices, after_prices)) <= 2


def test_contract_balanced_positions_remain_sellable_after_active_lp_entry(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        _activate_v4_market(context, contract, creator=creator, resolver=resolver)

        first_buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            first_buy_payment,
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        second_buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            second_buy_payment,
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_001,
        )
        before_prices = lmsr_prices(contract_q(contract), int(contract.b.value))

        lp_payment = make_usdc_payment(context, contract, lp2, 100_000_000)
        call_as(
            context,
            lp2,
            contract.enter_lp_active,
            arc4.UInt64(25_000_000),
            arc4.UInt64(100_000_000),
            _price_array(before_prices),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            lp_payment,
            latest_timestamp=6_000,
        )

        assert all(
            contract_q(contract)[idx] >= int(contract._get_total_user_shares(UInt64(idx)))
            for idx in range(int(contract.num_outcomes.value))
        )

        call_as(
            context,
            trader,
            contract.sell,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(0),
            latest_timestamp=6_001,
        )
        call_as(
            context,
            trader,
            contract.sell,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(0),
            latest_timestamp=6_002,
        )

        assert contract_user_shares(contract, trader, 0) == 0
        assert contract_user_shares(contract, trader, 1) == 0


def test_contract_active_lp_entry_rejects_market_above_skew_cap(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(context, contract, creator=creator, resolver=resolver, num_outcomes=2)
        seed_protocol_config_state(context, admin=creator, treasury=creator)
        ensure_blueprint_cid(contract)
        bootstrap_payment = make_usdc_payment(context, contract, creator, 200_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(200_000_000), bootstrap_payment, latest_timestamp=1)
        opt_in_market(context, contract, creator, latest_timestamp=2)

        buy_payment = make_usdc_payment(context, contract, trader, 2_000_000_000)
        call_as(
            context,
            trader,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(150 * SHARE_UNIT),
            arc4.UInt64(2_000_000_000),
            buy_payment,
            make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        current_prices = lmsr_prices(contract_q(contract), int(contract.b.value))
        assert max(current_prices) > DEFAULT_LP_ENTRY_MAX_PRICE_FP

        lp_payment = make_usdc_payment(context, contract, lp2, 100_000_000)
        with pytest.raises(AssertionError):
            call_as(
                context,
                lp2,
                contract.enter_lp_active,
                arc4.UInt64(25_000_000),
                arc4.UInt64(100_000_000),
                _price_array(current_prices),
                arc4.UInt64(PRICE_TOLERANCE_BASE),
                lp_payment,
                latest_timestamp=6_000,
            )


def test_contract_normalized_residual_weight_scales_with_market_lifetime(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    lp2 = make_address()

    with algopy_testing_context() as short_context:
        short_contract = QuestionMarket()
        _activate_v4_market(short_context, short_contract, creator=creator, resolver=resolver, deadline=20)
        short_payment = make_usdc_payment(short_context, short_contract, lp2, 200_000_000)
        call_as(
            short_context,
            lp2,
            short_contract.enter_lp_active,
            arc4.UInt64(100_000_000),
            arc4.UInt64(200_000_000),
            _price_array(lmsr_prices(contract_q(short_contract), int(short_contract.b.value))),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            short_payment,
            latest_timestamp=13,
        )
        call_as(short_context, creator, short_contract.cancel, latest_timestamp=20)
        short_creator_weight = _normalized_residual_weight(short_contract, creator)
        short_lp2_weight = _normalized_residual_weight(short_contract, lp2)

    with algopy_testing_context() as long_context:
        long_contract = QuestionMarket()
        _activate_v4_market(long_context, long_contract, creator=creator, resolver=resolver, deadline=200)
        long_payment = make_usdc_payment(long_context, long_contract, lp2, 200_000_000)
        call_as(
            long_context,
            lp2,
            long_contract.enter_lp_active,
            arc4.UInt64(100_000_000),
            arc4.UInt64(200_000_000),
            _price_array(lmsr_prices(contract_q(long_contract), int(long_contract.b.value))),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            long_payment,
            latest_timestamp=133,
        )
        call_as(long_context, creator, long_contract.cancel, latest_timestamp=200)
        long_creator_weight = _normalized_residual_weight(long_contract, creator)
        long_lp2_weight = _normalized_residual_weight(long_contract, lp2)

    assert short_creator_weight == long_creator_weight
    assert short_lp2_weight == long_lp2_weight


def test_contract_buy_rejects_sub_share_granularity(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    trader = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        _activate_v4_market(context, contract, creator=creator, resolver=resolver)

        buy_payment = make_usdc_payment(context, contract, trader, 10_000_000)
        with pytest.raises(AssertionError):
            call_as(
                context,
                trader,
                contract.buy,
                arc4.UInt64(0),
                arc4.UInt64(SHARE_UNIT - 1),
                arc4.UInt64(10_000_000),
                buy_payment,
                make_mbr_payment(context, contract, trader, SHARE_BOX_MBR + COST_BOX_MBR),
                latest_timestamp=5_000,
            )


def test_contract_lp_fees_are_strictly_prospective_and_withdrawable(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer1 = make_address()
    buyer2 = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        _activate_v4_market(context, contract, creator=creator, resolver=resolver)

        first_buy_payment = make_usdc_payment(context, contract, buyer1, 10_000_000)
        call_as(
            context,
            buyer1,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            first_buy_payment,
            make_mbr_payment(context, contract, buyer1, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        call_as(context, creator, contract.claim_lp_fees, latest_timestamp=5_001)
        creator_first_claim = _withdrawable_fee_surplus(contract, creator)
        assert creator_first_claim > 0

        current_prices = lmsr_prices(contract_q(contract), int(contract.b.value))
        lp_payment = make_usdc_payment(context, contract, lp2, 200_000_000)
        call_as(
            context,
            lp2,
            contract.enter_lp_active,
            arc4.UInt64(50_000_000),
            arc4.UInt64(200_000_000),
            _price_array(current_prices),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            lp_payment,
            latest_timestamp=6_000,
        )

        with pytest.raises(AssertionError):
            call_as(context, lp2, contract.claim_lp_fees, latest_timestamp=6_000)

        second_buy_payment = make_usdc_payment(context, contract, buyer2, 10_000_000)
        call_as(
            context,
            buyer2,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            second_buy_payment,
            make_mbr_payment(context, contract, buyer2, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=6_001,
        )
        call_as(context, creator, contract.claim_lp_fees, latest_timestamp=6_002)
        # lp2's earlier claim_lp_fees failed before settle ran, so no fees were claimed.
        call_as(context, lp2, contract.claim_lp_fees, latest_timestamp=6_002)

        creator_total_surplus = _withdrawable_fee_surplus(contract, creator)
        lp2_surplus = _withdrawable_fee_surplus(contract, lp2)
        creator_second_claim = creator_total_surplus - creator_first_claim

        assert creator_second_claim > 0
        assert lp2_surplus > 0
        assert creator_second_claim > lp2_surplus

        pool_before = int(contract.pool_balance.value)
        lp_fee_balance_before = int(contract.lp_fee_balance.value)
        withdraw_amount = lp2_surplus // 2
        call_as(context, lp2, contract.withdraw_lp_fees, arc4.UInt64(withdraw_amount), latest_timestamp=6_003)
        transfers = last_inner_asset_transfers(context)

        assert len(transfers) == 1
        assert int(transfers[0].asset_amount) == withdraw_amount
        assert transfers[0].asset_receiver == Account(lp2)
        assert _withdrawable_fee_surplus(contract, lp2) == lp2_surplus - withdraw_amount
        assert int(contract.pool_balance.value) == pool_before
        assert int(contract.lp_fee_balance.value) == lp_fee_balance_before - withdraw_amount


def test_contract_many_low_delta_lps_do_not_create_fee_boxes(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    buyer = make_address()
    open_proposer = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        create_contract(
            context,
            contract,
            creator=creator,
            resolver=resolver,
            num_outcomes=2,
            initial_b=50_000_000,
        )
        ensure_blueprint_cid(contract)
        payment = make_usdc_payment(context, contract, creator, 50_000_000)
        call_as(context, creator, contract.bootstrap, arc4.UInt64(50_000_000), payment, latest_timestamp=1)
        opt_in_market(context, contract, creator, latest_timestamp=2)

        lps = [make_address() for _ in range(30)]
        for idx, lp in enumerate(lps):
            opt_in_market(context, contract, lp, latest_timestamp=3 + idx)
            prices = lmsr_prices(contract_q(contract), int(contract.b.value))
            call_as(
                context,
                lp,
                contract.enter_lp_active,
                arc4.UInt64(5_000),
                arc4.UInt64(10_000),
                _price_array(prices),
                arc4.UInt64(PRICE_TOLERANCE_BASE),
                make_usdc_payment(context, contract, lp, 10_000),
                latest_timestamp=100 + idx,
            )

        call_as(
            context,
            buyer,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            make_usdc_payment(context, contract, buyer, 10_000_000),
            make_mbr_payment(context, contract, buyer, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=1_000,
        )

        for idx, lp in enumerate(lps):
            call_as(context, lp, contract.claim_lp_fees, latest_timestamp=1_100 + idx)
            assert _withdrawable_fee_surplus(contract, lp) > 0
        assert not hasattr(contract, "user_claimable_fees_box")

        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)
        propose_payment = make_usdc_payment(context, contract, open_proposer, 10_000_000)
        call_as(
            context,
            open_proposer,
            contract.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            propose_payment,
            latest_timestamp=13_601,
        )
        call_as(context, creator, contract.finalize_resolution, latest_timestamp=100_002)
        assert int(contract.pending_payouts_box.get(Account(open_proposer).bytes, default=UInt64(0))) == 10_000_000


def test_contract_residual_claim_respects_winner_reserve_and_tracks_settlement(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    winner = make_address()
    loser = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        _activate_v4_market(context, contract, creator=creator, resolver=resolver)

        winner_buy_payment = make_usdc_payment(context, contract, winner, 10_000_000)
        call_as(
            context,
            winner,
            contract.buy,
            arc4.UInt64(0),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            winner_buy_payment,
            make_mbr_payment(context, contract, winner, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_000,
        )
        loser_buy_payment = make_usdc_payment(context, contract, loser, 10_000_000)
        call_as(
            context,
            loser,
            contract.buy,
            arc4.UInt64(1),
            arc4.UInt64(SHARE_UNIT),
            arc4.UInt64(10_000_000),
            loser_buy_payment,
            make_mbr_payment(context, contract, loser, SHARE_BOX_MBR + COST_BOX_MBR),
            latest_timestamp=5_001,
        )

        call_as(context, creator, contract.trigger_resolution, latest_timestamp=10_000)
        propose_payment = make_usdc_payment(context, contract, resolver, 10_000_000)
        call_as(
            context,
            resolver,
            contract.propose_resolution,
            arc4.UInt64(0),
            arc4.DynamicBytes(b"e" * 32),
            propose_payment,
            latest_timestamp=10_001,
        )
        call_as(context, creator, contract.finalize_resolution, latest_timestamp=96_401)

        reserve_before = int(contract._get_total_user_shares(UInt64(0)))
        pool_before = int(contract.pool_balance.value)
        call_as(context, creator, contract.claim_lp_residual, latest_timestamp=96_402)
        first_residual_claim = int(contract.total_residual_claimed.value)
        residual_transfers = last_inner_asset_transfers(context)

        assert int(contract.status.value) == STATUS_RESOLVED
        assert int(contract.settlement_timestamp.value) == 96_401
        assert first_residual_claim > 0
        assert len(residual_transfers) == 1
        assert int(residual_transfers[0].asset_amount) == first_residual_claim
        assert int(contract.pool_balance.value) == pool_before - first_residual_claim
        assert int(contract.pool_balance.value) >= reserve_before

        call_as(context, winner, contract.claim, arc4.UInt64(0), arc4.UInt64(SHARE_UNIT), latest_timestamp=96_403)
        assert int(contract.pool_balance.value) >= int(contract._get_total_user_shares(UInt64(0)))

        with pytest.raises(AssertionError):
            call_as(context, creator, contract.claim_lp_residual, latest_timestamp=96_404)


def test_contract_cancel_sets_settlement_timestamp_and_allows_residual_claim(disable_arc4_emit) -> None:
    creator = make_address()
    resolver = make_address()
    lp2 = make_address()

    with algopy_testing_context() as context:
        contract = QuestionMarket()
        _activate_v4_market(context, contract, creator=creator, resolver=resolver)

        lp_payment = make_usdc_payment(context, contract, lp2, 200_000_000)
        call_as(
            context,
            lp2,
            contract.enter_lp_active,
            arc4.UInt64(100_000_000),
            arc4.UInt64(200_000_000),
            _price_array(lmsr_prices(contract_q(contract), int(contract.b.value))),
            arc4.UInt64(PRICE_TOLERANCE_BASE),
            lp_payment,
            latest_timestamp=5_000,
        )

        call_as(context, creator, contract.cancel, latest_timestamp=5_001)
        call_as(context, creator, contract.claim_lp_residual, latest_timestamp=5_002)
        creator_transfers = last_inner_asset_transfers(context)
        creator_payout = int(creator_transfers[0].asset_amount)
        claimed_after_creator = int(contract.total_residual_claimed.value)
        call_as(context, lp2, contract.claim_lp_residual, latest_timestamp=5_003)
        lp2_transfers = last_inner_asset_transfers(context)
        lp2_payout = int(lp2_transfers[0].asset_amount)

        assert int(contract.status.value) == STATUS_CANCELLED
        assert int(contract.settlement_timestamp.value) == int(contract.deadline.value)
        assert claimed_after_creator == creator_payout
        assert creator_payout > 0
        assert lp2_payout > 0
        assert creator_payout > lp2_payout
