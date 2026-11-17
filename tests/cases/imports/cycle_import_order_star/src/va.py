# `va` extends `__all__` after its cycle import, but nothing star-imports it
# while it is suspended: the star import in main.py runs once it finished.
__all__ = ["A"]


def A() -> int:
    return 1


import vb  # noqa: E402

__all__ += ["B"]  # tpyc: ok


def B() -> int:
    return vb.two()
