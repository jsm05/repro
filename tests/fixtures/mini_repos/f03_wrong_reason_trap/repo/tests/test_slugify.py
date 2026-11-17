from slugs.slugify import slugify


def test_basic():
    assert slugify("Hello World") == "hello-world"


def test_punctuation_dropped():
    assert slugify("Hi!") == "hi"


def test_custom_sep():
    assert slugify("A B", sep="_") == "a_b"
