from calculator import divide


def test_divide_by_zero_returns_zero():
    assert divide(1, 0) == 0
