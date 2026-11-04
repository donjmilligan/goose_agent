from toycalc import add


def test_add_returns_sum() -> None:
    assert add(2, 3) == 5


def test_add_handles_negatives() -> None:
    assert add(-4, 1) == -3
