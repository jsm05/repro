# get_int ignores its default

Asking `get_int` for a key that is not in the config raises `KeyError` instead of
returning the default I passed in.
