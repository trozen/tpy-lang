from rc import *  # tpyc: warning(/'rc' is still being imported .* before it defines 'C2', 'main'; CPython's star import binds none of them here/)


class M1:
    def __init__(self, v: int) -> None:
        self.v = v


import rn  # noqa: E402


class M2:
    def __init__(self, v: int) -> None:
        self.v = v


def total() -> int:
    return C1(1).v + M1(10).v
