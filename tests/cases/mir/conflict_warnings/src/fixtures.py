# Shared records and helpers of the conflict_warnings sections.
from tpy import Own, ReturnException, StrView, int32


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: StrView, n: int32) -> None:
        self.s = s
        self.n = n

    def adopt(self, v: StrView) -> None:
        self.s = v


class Bad(Exception, ReturnException):
    code: int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk(c: str) -> str:
    return c * 40


def mk_a(n: int32) -> Own[A]:
    return A(n)


def fail() -> int32:
    xs: list[int32] = []
    return xs[0]



class Hs:
    s: StrView

    def __init__(self) -> None:
        self.s = "-"

    def maybe(self, s: StrView, flag: bool) -> None:
        if flag:
            self.s = s

    def maybe_reset(self, flag: bool) -> None:
        if flag:
            self.s = "safe"
