# A lambda into a @dispatch method rebinding an inferred float local: the
# other argument leaves one candidate, whose Callable parameter types it. (A
# lambda reading `r` would copy it: BUGS.md#callable-local-lambda-copies-capture-silently.)
from typing import Callable
from tpy import dispatch, int32


class Rec:
    def __init__(self, v: int32) -> None:
        self.v = v


class M:
    def __init__(self) -> None:
        pass

    @dispatch
    def run(self, r: Rec, f: Callable[[float], float]) -> float:
        r.v = 100
        return f(1.0)

    @dispatch
    def run(self, s: str) -> str:
        return s


def rebind(m: M) -> None:
    r = Rec(1)
    x = 0.5
    x = m.run(r, lambda q: q + 0.5)  # tpyc: ok
    print(x, r.v)


rebind(M())
