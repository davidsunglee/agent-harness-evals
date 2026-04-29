from test_case_001 import add


def test_add_zero():
    assert add(0, 0) == 0


def test_add_negative_cancels():
    assert add(-1, 1) == 0
