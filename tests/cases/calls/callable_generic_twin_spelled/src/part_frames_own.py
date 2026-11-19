# A generator frame's open-result local at `T = Own[P]`, rebound after a
# suspension and first bound by a walrus: a read before its last use
# copies (warned at the call, the twin's copy), so each step is its own
# object where CPython yields the same one twice. A walrus as the only
# binding hands the result on, in a plain body and a frame.
from typing import Iterator
from tpy import Fn, Own, int32


class P:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n]


def own_id(p: Own[P]) -> Own[P]:
    return p


def gen_rebind[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    y = f(a)  # tpyc: ok
    yield y
    y = f(a)
    yield y


def gen_walrus[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    (y := f(a))  # tpyc: ok
    yield y
    y = f(y)
    yield y


def walrus_once[T](f: Fn[[T], T], a: T) -> T:
    (y := f(a))  # tpyc: ok
    return y


def gen_walrus_once[T](f: Fn[[T], T], a: T) -> Iterator[T]:
    (y := f(a))  # tpyc: ok
    yield y


def main() -> None:
    # the first `f(a)` copies the owned `a`
    for o in gen_rebind(own_id, P(9)):  # tpyc: warning(/copies P into owned storage/)
        o.items.append(10)
        print("frame-rebind-own", len(o.items))
    # the first `yield y` copies `y`, which `f(y)` reads after it
    for o in gen_walrus(own_id, P(9)):  # tpyc: warning(/copies P into owned storage/)
        o.items.append(10)
        print("frame-walrus-own", len(o.items))
    w = walrus_once(own_id, P(7))  # tpyc: ok
    w.items.append(8)
    print("walrus-once-own", w.items)
    for o in gen_walrus_once(own_id, P(9)):  # tpyc: ok
        o.items.append(10)
        print("frame-walrus-once-own", o.items)
