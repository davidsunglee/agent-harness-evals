from parse_duration import parse_duration


def test_parse_hours():
    assert parse_duration("1h") == 3600


def test_parse_minutes_still_works():
    assert parse_duration("10m") == 600
