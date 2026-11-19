# Shared fixtures of the binding-path matrix: a reference class holding a
# list (so a copy or a move-out shows), and one callable per instantiation.
from tpy import Own, int32


class Q:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n]


def ident(q: Q) -> Q:
    return q


def sid(s: str) -> str:
    return s


def own_id(q: Own[Q]) -> Own[Q]:
    return q


def mk0() -> Own[Q]:
    return Q(3)
