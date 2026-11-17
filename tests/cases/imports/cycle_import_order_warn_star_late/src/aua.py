# `__all__` is bound above the cycle import, F is defined and added to it
# only below: both keep F out of CPython's star import in `aub`.
__all__ = ["E"]


class E:
    pass


import aub  # noqa: E402
__all__ += ["F"]


class F:
    v: int

    def __init__(self) -> None:
        self.v = 3
