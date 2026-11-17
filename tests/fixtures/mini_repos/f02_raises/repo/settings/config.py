"""Typed access to a flat config dict."""


def get_int(config, key, default=None):
    """Return config[key] as an int, or `default` when the key is missing."""
    value = config[key]
    return int(value)


def get_bool(config, key, default=False):
    value = config.get(key)
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")
