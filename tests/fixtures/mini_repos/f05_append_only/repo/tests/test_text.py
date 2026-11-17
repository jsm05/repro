from textkit.text import truncate, word_count


def test_short_text_unchanged():
    assert truncate("abc", 5) == "abc"


def test_word_count():
    assert word_count("one two  three") == 3
