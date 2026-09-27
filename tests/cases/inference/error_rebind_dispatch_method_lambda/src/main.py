# Rejects valid (BUGS.md#dispatch-lambda-arg-untyped): a lambda into a @dispatch
# method under an inferred float local stays untyped, as at a declared x: float.
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
    x = m.run(r, lambda q: q + r.v)  # tpyc: error(/Lambda parameter types cannot be inferred without context/)
    print(x, r.v)


rebind(M())
