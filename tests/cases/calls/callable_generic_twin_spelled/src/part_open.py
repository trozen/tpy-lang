# Open results held in a local, and reads handed to a callee's open `T`, at
# `T = P` and at a spelled `T = Own[P]`: the twin's borrow and warned copy at
# `P`, its move at a last use and its warned copy elsewhere at `Own[P]`.
from tpy import Fn, Own, int32


class P:
    n: int32
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.items = [n]


def identity(p: P) -> P:
    return p


def own_id(p: Own[P]) -> Own[P]:
    return p


def add_o(a: Own[P], b: Own[P]) -> Own[P]:
    return P(a.n + b.n)


def keep[T](f: Fn[[T], T], a: T, b: T, out: list[T]) -> None:
    y = f(a)
    y = f(b)
    # the local holds `b` itself at `T = P` (a warned copy), an owned
    # result at `T = Own[P]` (its last use moves it)
    out.append(y)  # tpyc: warning(/may copy T into owned storage/)


def red[T](func: Fn[[T, T], T], xs: list[T]) -> T:
    acc = xs[0]
    for i in range(1, len(xs)):
        acc = func(acc, xs[i])
    return acc


def store1[T](x: T, out: list[T]) -> None:
    out.append(x)  # tpyc: warning(/may copy T into owned storage/)


def twice_own[T](x: T, out: list[T]) -> None:
    # the first hand-off copies at `T = Own[P]`, the last use moves
    store1[T](x, out)
    store1[T](x, out)


def sel[T](f: Fn[[T], T], xs: list[T]) -> T:
    # an element is never a last use: a copy at `T = Own[P]`
    return f(xs[0])


def each[T](f: Fn[[T], T], xs: list[T], out: list[T]) -> None:
    for x in xs:
        # a loop variable borrows its element: a copy at `T = Own[P]`
        out.append(f(x))  # tpyc: warning(/may copy T into owned storage/)


def fin[T](x: T) -> T:
    try:
        # moved after the finalizer at `T = Own[P]`, lent at `T = P`
        return x
    finally:
        print("open-fin finally")


def main() -> None:
    # free function, lending: `y` is `b`; the append copies, `b` keeps its list
    a = P(1)
    b = P(2)
    out: list[P] = []
    keep(identity, a, b, out)  # tpyc: ok
    out[0].n += 10
    print("open-keep-lend", b.n, len(b.items), out[0].n)
    # spelled `Own[P]`: the owned result moves into `out`
    keep[Own[P]](own_id, P(3), P(4), out)  # tpyc: ok
    print("open-keep-own", out[1].n, len(out[1].items))
    # the accumulator starts at a stored element: its return copies at
    # `Own[P]`, warned at this call
    ps = [P(1), P(5), P(3)]
    r = red[Own[P]](add_o, ps)  # tpyc: warning(/copies P into owned storage/)
    r.n += 100
    print("open-red-own", r.n, ps[0].n, len(ps[0].items))
    out2: list[P] = []
    twice_own[Own[P]](P(7), out2)  # tpyc: warning(/copies P into owned storage/)
    out2[0].n += 1
    print("open-twice", len(out2), out2[0].n, out2[1].n)
    s = sel(own_id, ps)  # tpyc: warning(/copies P into owned storage/)
    s.n += 100
    print("open-sel", s.n, ps[0].n)
    out3: list[P] = []
    each(own_id, ps, out3)  # tpyc: warning(/copies P into owned storage/)
    out3[0].n += 1000
    print("open-each", len(out3), out3[0].n, ps[0].n)
    p8 = P(8)
    # `p8`'s last use moves into the owned parameter and on into `f`
    f = fin[Own[P]](p8)  # tpyc: ok
    print("open-fin", f.n, len(f.items))
