"""Tag helpers."""


def unique_tags(tags):
    """Return tags with duplicates removed, keeping first-seen order."""
    return list(set(tags))


def normalize(tag):
    return tag.strip().lower()
