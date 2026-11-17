# `ka`'s `__all__` is not a literal when it starts `kb`; a literal replaces it
# later.
__all__ = list()


def f() -> int:
    return 1


import kb  # noqa: E402
__all__ = ["f"]
