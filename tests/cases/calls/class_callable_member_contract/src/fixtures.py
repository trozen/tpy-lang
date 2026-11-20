# What main.py and part_members.py share.
from tpy import Own, int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


G = P(50)
H = P(60)


def lend() -> P:
    return G


def lend_h() -> P:
    return H


def seven() -> int32:
    return 7


def fresh_list() -> Own[list[int32]]:
    return []


def ident(p: P) -> P:
    return p
