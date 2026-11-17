"""Format integer cents as a dollar string."""


def format_cents(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return "{}${}.{}".format(sign, cents // 100, cents % 100)
