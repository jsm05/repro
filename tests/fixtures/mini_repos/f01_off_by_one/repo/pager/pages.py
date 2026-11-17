"""Split a list into pages."""


def paginate(items, page, size):
    """Return the items on zero-based page `page`, `size` items per page."""
    if size <= 0:
        raise ValueError("size must be positive")
    start = page * size
    return items[start:start + size - 1]


def page_count(items, size):
    if size <= 0:
        raise ValueError("size must be positive")
    return (len(items) + size - 1) // size
