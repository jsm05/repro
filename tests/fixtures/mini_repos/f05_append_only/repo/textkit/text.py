"""Small text helpers."""


def truncate(text, width, marker="..."):
    """Shorten `text` so the result, marker included, is at most `width` chars."""
    if len(text) <= width:
        return text
    return text[:width] + marker


def word_count(text):
    return len(text.split())
