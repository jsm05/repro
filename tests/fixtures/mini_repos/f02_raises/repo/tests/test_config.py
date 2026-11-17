from settings.config import get_bool, get_int


def test_get_int_parses_strings():
    assert get_int({"port": "80"}, "port") == 80


def test_get_bool_default():
    assert get_bool({}, "debug") is False


def test_get_bool_true_values():
    assert get_bool({"debug": "Yes"}, "debug") is True
