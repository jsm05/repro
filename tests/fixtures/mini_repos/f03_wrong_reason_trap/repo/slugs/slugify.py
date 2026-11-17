"""URL slugs."""

import re


def slugify(text, *, sep="-"):
    """Lowercase `text` and join its words with `sep`."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return text.replace(" ", sep)
