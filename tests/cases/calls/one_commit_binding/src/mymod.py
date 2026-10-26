from tpy import int32
from tpy.extern import cpp_template


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@cpp_template("{0}.push_back({1})")
def push(xs: list[int32], v: int32) -> None: ...


@cpp_template("{0}.clear()")
def wipe(xs: list[P]) -> None: ...
