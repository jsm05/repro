from tags.tags import normalize, unique_tags


def test_duplicates_removed():
    assert sorted(unique_tags(["a", "b", "a"])) == ["a", "b"]


def test_normalize():
    assert normalize("  Red ") == "red"
