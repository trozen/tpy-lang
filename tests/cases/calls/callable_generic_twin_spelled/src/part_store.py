# At `T = Own[P]` every storage position holds `P` (a copy, a `T` local, an
# element, a value-bound result), a yield and a nested call's return are
# owning sinks (the move at a last use, a warned copy elsewhere), and the
# calls whose output differs from CPython's aliasing by the twin's warned copy.
from typing import Iterator
from tpy import Fn, Own, ValueType, copy, int32, nocopy, readonly


class P:
    n: int32
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.items = [n]


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def identity(p: P) -> P:
    return p


def mk(p: P) -> Own[P]:
    return P(p.n + 1)


def mk0() -> Own[P]:
    return P(5)


def dup[T](x: T) -> Own[T]:
    # a value construction of `T`: `storage_t<T>(x)`
    y: T = copy(x)
    return y


def table[T](x: T) -> Own[dict[int32, T]]:
    d: dict[int32, T] = {}
    # the last use of `x` moves into the element at `T = Own[P]`
    d[1] = x  # tpyc: warning(/may copy T into container/)
    return d


def keyed[K: ValueType](k: K) -> K:
    # a value-bound result is its storage form at `K = Own[str]`
    return k  # tpyc: ok


def ident[T](x: T) -> T:
    return x


def relay[T](f: Fn[[], T]) -> T:
    # the fresh result is held for the nested call, whose lent result is
    # copied into `-> T` at `T = Own[P]`
    return ident(f())


def twice[A, T](f: Fn[[A], T], x: A) -> Iterator[T]:
    y = f(x)
    # the first yield copies at `T = Own[P]`, the last moves
    yield y
    yield y


def inspect[T](x: readonly[T]) -> int32:
    return 1


def fwd[T](x: T) -> int32:
    # a spelled `readonly[T]` at `T = Own[P]` is the twin's owned parameter
    return inspect[T](x)  # tpyc: ok


def touch[T](f: Fn[[], T], xs: list[P], ys: list[T]) -> T:
    acc = ys[0]
    xs[0].n += 1
    acc = f()
    return acc


def fill[T, U](f: Fn[[T], U], x: T, out: list[U]) -> None:
    out.append(f(x))  # tpyc: warning(/may copy U into owned storage/)


def get_or_make[T](make: Fn[[], T], d: dict[str, T], k: str) -> T:
    if k not in d:
        d[k] = make()  # tpyc: warning(/may copy T into container/)
    return d[k]


def peek[T](x: T) -> T:
    return x


class Ops:
    def take[T](self, x: T) -> T:
        return x


def main() -> None:
    a = dup[Own[P]](P(2))
    print("store-dup", a.n)
    d = table[Own[P]](P(4))
    print("store-dict", d[1].n)
    print("store-keyed", keyed[Own[str]]("hello"))
    # warned at this call: the nested call's lent result is copied
    r = relay[Own[P]](mk0)  # tpyc: warning(/copies P into owned storage/)
    print("store-relay", r.n)
    seed = P(1)
    for p in twice(mk, seed):  # tpyc: warning(/copies P into owned storage/)
        p.items.append(3)
        print("store-twice", len(p.items))
    print("store-fwd", fwd[Own[P]](P(9)))
    # method position: the live `q` copies into the owned parameter (warned)
    q = P(6)
    t = Ops().take[Own[P]](q)  # tpyc: warning(/copies P into owned storage/)
    t.n += 1
    print("store-method", q.n, t.n)
    # touch: `acc` borrowed `ys[0]` before its rebind; the return copies
    xs = [P(0)]
    u = touch(mk0, xs, [P(7)])  # tpyc: warning(/copies P into owned storage/)
    print("store-touch", xs[0].n, u.n)
    # fill with a lending callable: U = P, the append copies
    out: list[P] = []
    b = P(3)
    fill(identity, b, out)  # tpyc: ok
    out[0].n += 100
    print("store-fill", b.n, out[0].n)
    # get_or_make with a fresh lambda binds T = Own[P]: a warned copy on a
    # hit and on a miss, naming the signature that lends
    g: dict[str, P] = {}
    v = get_or_make(lambda: P(7), g, "k")  # tpyc: warning(/declare 'make: Fn\[\[\], Own\[T\]\]'/)
    v.n += 1
    print("store-gom", v.n, g["k"].n)
    # a @nocopy object at `T = N` is lent through, no copy
    n = N(3)
    peek(n).n += 1  # tpyc: ok
    print("store-nocopy", n.n)
