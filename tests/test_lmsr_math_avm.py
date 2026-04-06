from algopy import UInt64

from smart_contracts.lmsr_math_avm import _mul_div_ceil, _mul_div_floor


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
