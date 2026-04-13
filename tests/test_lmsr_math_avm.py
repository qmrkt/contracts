from algopy import Array, UInt64

from smart_contracts.lmsr_math_avm import (
    _mul_div_ceil,
    _mul_div_floor,
    lmsr_collateral_required_from_prices,
    lmsr_gauge_alpha_from_prices,
    lmsr_normalized_q_from_prices,
    lmsr_prices,
    lmsr_q_from_prices_with_floor,
)


def test_mul_div_floor_supports_large_uint64_products() -> None:
    numerator_a = UInt64(18_446_744_073_710)
    numerator_b = UInt64(1_000_000)
    denominator = UInt64(1_000_000)

    result = _mul_div_floor(numerator_a, numerator_b, denominator)

    assert int(result) == 18_446_744_073_710


def test_mul_div_ceil_supports_large_uint64_products() -> None:
    numerator_a = UInt64(18_446_744_073_710)
    numerator_b = UInt64(1_000_000)
    denominator = UInt64(999_999)

    result = _mul_div_ceil(numerator_a, numerator_b, denominator)
    expected = (18_446_744_073_710 * 1_000_000 + 999_999 - 1) // 999_999

    assert int(result) == expected


def test_collateral_required_from_prices_matches_binary_uniform_case() -> None:
    prices = Array[UInt64]((UInt64(500_000), UInt64(500_000)))

    alpha = lmsr_gauge_alpha_from_prices(prices)
    collateral = lmsr_collateral_required_from_prices(UInt64(100_000_000), prices)

    assert int(alpha) >= 693_147 - 2
    assert abs(int(collateral) - 69_314_700) <= 500


def test_normalized_q_from_prices_round_trips_prices() -> None:
    prices = Array[UInt64]((UInt64(200_000), UInt64(300_000), UInt64(500_000)))

    q = lmsr_normalized_q_from_prices(prices, UInt64(100_000_000))
    reconstructed = lmsr_prices(q, UInt64(100_000_000))

    assert reconstructed.length == prices.length
    for idx in range(int(prices.length)):
        assert abs(int(reconstructed[UInt64(idx)]) - int(prices[UInt64(idx)])) <= 2


def test_q_from_prices_with_floor_preserves_prices_and_claim_coverage() -> None:
    prices = Array[UInt64]((UInt64(500_000), UInt64(500_000)))
    floor_q = Array[UInt64]((UInt64(1_000_000), UInt64(1_000_000)))

    q = lmsr_q_from_prices_with_floor(prices, UInt64(100_000_001), floor_q)
    reconstructed = lmsr_prices(q, UInt64(100_000_001))

    assert int(q[UInt64(0)]) == 1_000_000
    assert int(q[UInt64(1)]) == 1_000_000
    assert int(reconstructed[UInt64(0)]) == 500_000
    assert int(reconstructed[UInt64(1)]) == 500_000
