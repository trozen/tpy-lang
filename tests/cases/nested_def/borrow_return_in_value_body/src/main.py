# A nested def returning a borrow inside a body whose own return is a value
# (or None): its return is checked against its own signature, not the
# enclosing one's, at a direct call and at a Callable slot.
from typing import Callable, Iterator
from tpy import Own, int32, copy


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


GP = P(3)
GL: list[int32] = [1, 2]


# free function: a record and a container returned from a module variable.
def sec_free() -> int32:
    def get() -> P:
        return GP  # tpyc: ok

    def get_l() -> list[int32]:
        return GL  # tpyc: ok
    get().n += 1
    ys = get_l()
    ys.append(9)
    return get().n + len(get_l())


# method: a direct call, and the def bound to a Callable slot.
class C:
    def direct(self) -> int32:
        def get(q: P) -> P:
            return q  # tpyc: ok
        p = P(1)
        get(p).n += 1
        return p.n

    def at_slot(self) -> int32:
        def get(q: P) -> P:
            return q  # tpyc: ok
        w: Callable[[P], P] = get
        p = P(1)
        w(p).n += 10
        return p.n


# constructor: a None return around the borrowing def.
class D:
    n: int32

    def __init__(self, p: P) -> None:
        def get(q: P) -> P:
            return q  # tpyc: ok
        get(p).n += 100
        self.n = p.n


# a Callable returned: the enclosing return is the Callable itself.
def make() -> Callable[[P], Own[P]]:
    def get(q: P) -> P:
        return q  # tpyc: ok

    def middle(q: P) -> Own[P]:
        return copy(get(q))
    g: Callable[[P], P] = get
    p = P(1)
    g(p).n += 1
    print("make", p.n)
    h: Callable[[P], Own[P]] = middle
    return h


# generator: the def is a frame member.
def gen(p: P) -> Iterator[int32]:
    def get(q: P) -> P:
        return q  # tpyc: ok
    get(p).n += 1000
    yield p.n


# neighbour: an enclosing body that itself returns a borrow.
def lend(p: P) -> P:
    def get(q: P) -> P:
        return q  # tpyc: ok
    return get(p)


def main() -> None:
    print("free", sec_free(), GP.n, GL)
    print("method", C().direct(), C().at_slot())
    d = D(P(1))
    print("ctor", d.n)
    f = make()
    q = P(4)
    r = f(q)
    r.n += 1
    print("callable", r.n, q.n)
    for v in gen(P(2)):
        print("gen", v)
    p = P(5)
    lend(p).n += 1
    print("lend", p.n)


main()
