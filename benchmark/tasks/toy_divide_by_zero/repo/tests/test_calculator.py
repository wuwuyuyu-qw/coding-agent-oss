from calculator import divide


def test_divide_by_zero():
    assert divide(10, 0) is None


def test_divide_regular_numbers():
    assert divide(10, 2) == 5
