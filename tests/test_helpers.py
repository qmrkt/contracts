"""Shared test utilities for contract tests."""

DEFAULT_B = 100_000_000


def safe_bootstrap_deposit(num_outcomes: int, b: int = DEFAULT_B, minimum: int = 0) -> int:
    """Compute a safe bootstrap deposit that satisfies the LMSR multiplier floor."""
    if num_outcomes <= 2:
        required = b
    elif num_outcomes <= 7:
        required = 2 * b
    else:
        required = 3 * b
    return max(required, minimum)
