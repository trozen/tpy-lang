# The literal twin of `ka`: `__all__` is a literal that lists nothing yet.
__all__ = []


def f() -> int:
    return 1


import lb  # noqa: E402
__all__ = ["f"]
