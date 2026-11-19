# Open-result locals whose warned copies differ from CPython's aliasing: a
# non-last read into an owning sink copies at every T (the declaration's
# hedge) where CPython stores the object itself; at T = Own[Q] a read that
# is not the last use copies, warned at the instantiating call.
from typing import Iterator
from tpy import Fn, Own, int32


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


# the last of two appends hands the value over at Own[Q] and str
def twice[T](f: Fn[[T], T], a: T, out: list[T]) -> None:
    y = f(a)
    out.append(y)  # tpyc: warning(/may copy T into owned storage/)
    out.append(y)  # tpyc: warning(/may copy T into owned storage/)


# a frame local read again after its yield
def gen_again[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    y = f(a)
    yield y
    y = f(y)
    yield y


# a frame step reading the local before its last use: the first yield
# copies at Own[Q] (warned at the call), only the last one moves
def gen_twice[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    y = f(a)
    yield y
    yield y


def main() -> None:
    q = Q(1)
    out: list[Q] = []
    twice(ident, q, out)
    out[1].items.append(2)
    print("twice-ref", len(q.items), len(out[0].items), len(out[1].items))
    souts: list[str] = []
    twice(sid, "a", souts)
    print("twice-str", souts)
    oout: list[Q] = []
    twice(own_id, Q(3), oout)  # tpyc: ok
    oout[1].items.append(4)
    print("twice-own", len(oout[0].items), len(oout[1].items))
    for o in gen_again(own_id, Q(7)):  # tpyc: warning(/copies Q into owned storage/)
        o.items.append(8)
        print("gen-again-own", len(o.items))
    for o in gen_twice(own_id, Q(5)):  # tpyc: warning(/copies Q into owned storage/)
        o.items.append(6)
        print("gen-twice-own", len(o.items))
