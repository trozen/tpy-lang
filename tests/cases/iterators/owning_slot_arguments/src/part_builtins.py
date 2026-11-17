# Builtin stubs taking elements over: `sorted`, `list.extend` and the
# `list()` / `set()` constructors hand a dead or fresh container over and
# copy a lent one's elements, warned (a lent section prints only what CPython
# agrees on).
from typing import Iterable, Iterator
from tpy import Own, int32, copy_iter


class R:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __lt__(self, o: "R") -> bool:
        return self.v < o.v


class H:
    rs: list[R]
    pairs: list[tuple[str, R]]

    def __init__(self) -> None:
        self.rs = [R(2), R(1)]
        self.pairs = [("z", R(9))]


def rkey(r: R) -> int32:
    return r.v


def mk() -> Own[list[R]]:
    return [R(2), R(1)]


def inst_rebound(h: H) -> Own[list[R]]:
    xs = mk()
    # A later binding makes `xs` non-owning: the instantiation is lent and
    # copies its elements, warned as at any owning slot.
    ys = list[R](xs)  # tpyc: warning(/copies R elements/)
    xs = h.rs
    xs[0].v = 6
    return ys


def mkpairs() -> Own[list[tuple[str, R]]]:
    return [("a", R(1))]


def inst_ternary() -> None:
    xs = mk()
    ys = mk()
    # A decided ternary is its live arm: `xs` at its last use is handed over.
    zs = list[R](xs if True else ys)  # tpyc: ok
    zs[0].v = 50
    print("inst ternary", zs[0].v, len(ys))


def dict_rebound(h: H) -> None:
    pairs = mkpairs()
    # A later binding makes `pairs` non-owning: the dict copies its elements.
    d = dict[str, R](pairs)  # tpyc: warning(/copies tuple\[str, R\] elements/)
    pairs = h.pairs
    d["a"].v = 7
    print("dict rebound", d["a"].v, pairs[0][1].v)


# Its `__iter__` hides `list.__iter__` wherever a list reference holds one,
# so the warning is right; the sections pin that a fresh one, handed over,
# still iterates through the override.
class Sevens(list[int32]):  # tpyc: warning(/hides 'list.__iter__'/)
    def __iter__(self) -> Iterator[int32]:
        yield 7
        yield 7


def mk_sevens() -> Own[Sevens]:
    r = Sevens()
    r.append(1)
    r.append(2)
    return r


def sorted_fwd(it: Iterable[Own[R]]) -> Own[list[R]]:
    return sorted(it, key=rkey)  # tpyc: ok


def main() -> None:
    xs = [R(2), R(1)]
    s0 = sorted(xs, key=rkey)  # tpyc: warning(/copies R elements/)
    s0[0].v = 100
    print("sorted live", s0[0].v, len(xs))
    xd = [R(2), R(1)]
    print("sorted dead", [r.v for r in sorted(xd, key=rkey)])  # tpyc: ok
    h = H()
    s1 = sorted(h.rs)  # tpyc: warning(/copies R elements/)
    s1[0].v = 100
    print("sorted field", s1[0].v, len(h.rs))
    print("sorted fresh", [r.v for r in sorted(mk())])  # tpyc: ok
    print("sorted literal", [r.v for r in sorted([R(3), R(1)], reverse=True)])  # tpyc: ok
    print("sorted comprehension", [r.v for r in sorted([R(i) for i in range(3)], key=rkey, reverse=True)])  # tpyc: ok
    ys = [R(2), R(1)]
    s2 = sorted(copy_iter(ys))  # tpyc: ok
    s2[0].v = 100
    print("sorted copy_iter", ys[1].v)
    print("sorted forwarded", [r.v for r in sorted_fwd(mk())])
    ws = [R(2), R(1)]
    s3 = sorted(iter(ws), key=rkey)  # tpyc: warning(/copies R elements/)
    s3[0].v = 100
    print("sorted iter", s3[0].v, len(ws))
    st = {3, 1, 2}
    print("sorted dead set", sorted(st))  # tpyc: ok
    dd = {"b": 1, "a": 2}
    print("sorted dead dict", sorted(dd))  # tpyc: ok
    out: list[R] = []
    e = [R(5)]
    out.extend(e)  # tpyc: ok
    out.extend(mk())  # tpyc: ok
    live = [R(7)]
    out.extend(live)  # tpyc: warning(/copies R elements/)
    out[3].v = 70
    print("extend", len(out), out[3].v, len(live))
    l2 = [R(1)]
    print("list dead", len(list(l2)))  # tpyc: ok
    s9 = {1, 2}
    print("set dead", len(set(s9)))  # tpyc: ok
    h4 = H()
    print("inst rebound", inst_rebound(h4)[0].v, h4.rs[0].v)
    inst_ternary()
    dict_rebound(H())
    # A subclass handed over iterates through its own `__iter__`, not the
    # list's consuming iterator.
    print("subclass list", list(mk_sevens()))  # tpyc: ok
    print("subclass sorted", sorted(mk_sevens()))  # tpyc: ok
