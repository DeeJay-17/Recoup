from decimal import Decimal

from recoup_case.service import compute_priority


def test_priority_bounds() -> None:
    assert compute_priority(Decimal("100"), 0) == 5
    assert compute_priority(Decimal("50000"), 90) == 1


def test_priority_monotonic_in_amount_and_age() -> None:
    assert compute_priority(Decimal("6000"), 10) < compute_priority(Decimal("500"), 10)
    assert compute_priority(Decimal("500"), 45) < compute_priority(Decimal("500"), 10)
