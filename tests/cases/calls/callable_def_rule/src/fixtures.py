# Types and callable-taking helpers every part of the case shares.
from typing import Callable
from tpy import Fn, Own, int32, nocopy, ReturnException, ValueType


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class Pin:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Echo:
    def __init__(self) -> None:
        pass

    def __call__(self, x: P) -> P:
        return x


class Lender:
    p: P

    def __init__(self) -> None:
        self.p = P(70)

    def __call__(self) -> P:
        return self.p


class Holder:
    cb: Callable[[P], P]

    def __init__(self, cb: Callable[[P], P]) -> None:
        self.cb = cb


class Pair(ValueType):
    a: int32
    b: int32

    def __init__(self, a: int32, b: int32) -> None:
        self.a = a
        self.b = b


class Failed(Exception, ReturnException):
    pass


def ident(p: P) -> P:
    return p


def mk(p: P) -> Own[P]:
    return P(p.n + 1)


def make_cb() -> Callable[[P], P]:
    return lambda q: q


# free function: the result aliases the argument at Fn and at Callable
# (main.sec_free drives these; part_positions and part_capture reuse them).
def via(f: Fn[[P], P], x: P) -> None:
    r = f(x)  # tpyc: ok
    r.n += 1


def via_c(f: Callable[[P], P], x: P) -> None:
    r = f(x)  # tpyc: ok
    r.n += 1


def ret(f: Fn[[P], P], x: P) -> P:
    return f(x)  # tpyc: ok


def own(f: Fn[[P], P], x: P) -> Own[P]:
    return f(x)  # tpyc: warning(/copies P into owned storage/)


def own_slot(f: Fn[[P], Own[P]], x: P) -> Own[P]:
    return f(x)  # tpyc: ok


def via0(f: Fn[[], P]) -> None:
    r = f()  # tpyc: ok
    r.n += 1
