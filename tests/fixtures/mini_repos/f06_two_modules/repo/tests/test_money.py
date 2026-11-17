from money.format import format_cents
from money.parse import to_cents


def test_two_digit_fraction():
    assert to_cents("12.50") == 1250


def test_thousands_separator():
    assert to_cents("$1,234") == 123400


def test_format_negative():
    assert format_cents(-1999) == "-$19.99"
