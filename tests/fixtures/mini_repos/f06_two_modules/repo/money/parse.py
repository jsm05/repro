"""Parse amounts like "12.50" or "$1,234.5" into integer cents."""


def to_cents(text):
    text = text.strip().lstrip("$").replace(",", "")
    if "." in text:
        whole, frac = text.split(".", 1)
    else:
        whole, frac = text, "0"
    return int(whole or "0") * 100 + int(frac)
