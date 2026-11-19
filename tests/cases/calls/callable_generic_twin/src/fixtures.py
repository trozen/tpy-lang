# Shared fixtures: a reference class and callables of each result form.
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def identity(p: P) -> P:
    return p


def mk(p: P) -> Own[P]:
    return P(p.n + 1)


def bigger(a: P, b: P) -> P:
    return a if a.n >= b.n else b


def add(a: int32, b: int32) -> int32:
    return a + b
