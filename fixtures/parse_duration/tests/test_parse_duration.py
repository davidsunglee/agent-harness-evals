from parse_duration import parse_duration


def test_parse_seconds():
    assert parse_duration("5s") == 5
