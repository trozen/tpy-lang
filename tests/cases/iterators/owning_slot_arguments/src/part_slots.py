# Owning slot types beyond `Iterable[Own[R]]`: an iteration-only sub-protocol,
# a sub-protocol with `__len__`, `Iterator[Own[R]]`, a callable-fed `map`; a
# protocol that does not iterate (`Sequence[Own[R]]`) takes nothing over. A
# lent section's warned copy prints only what CPython agrees on.
from typing import Iterable, Iterator, Protocol, Sequence
from tpy import Own, int32, copy, copy_iter, dynamic


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class S:
    def __init__(self, s: str) -> None:
        self.s = s


class OwnIt[T](Iterable[T], Protocol):
    pass


class Rows(Iterable[Own[R]], Protocol):
    pass


@dynamic
class Bag[T](Protocol):
    def get(self) -> T: ...
    def size(self) -> int32: ...


class OneBag:
    def __init__(self, r: Own[R]) -> None:
        self.r = r

    def get(self) -> R:
        return self.r

    def size(self) -> int32:
        return self.r.v


def take_bag(b: Bag[Own[R]]) -> int32:
    # A `@dynamic` protocol is no owning element slot: an ordinary call.
    return b.size()


class Counted[T](Iterable[T], Protocol):
    def __len__(self) -> int32: ...


def take_it(xs: OwnIt[Own[R]]) -> int32:
    t = 0
    for x in xs:
        x.v += 10
        t += x.v
    return t


def take_counted(xs: Counted[Own[R]]) -> int32:
    # A member read before the one traversal.
    n = len(xs)
    t = 0
    for x in xs:
        x.v += 10
        t += x.v
    return t * 10 + n


def take_rows(xs: Rows) -> int32:
    # The `Own` step comes from the ancestor; the protocol names none itself.
    t = 0
    for x in xs:
        x.v += 10
        t += x.v
    return t


def take_pairs(xs: Iterable[Own[tuple[R, int32]]]) -> int32:
    t = 0
    for p in xs:
        p[0].v += 10
        t += p[0].v + p[1]
    return t


def count[T](xs: Iterable[Own[T]]) -> int32:
    n = 0
    for _ in xs:
        n += 1
    return n


def take_iter(xs: Iterator[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def take_seq(xs: Sequence[Own[R]]) -> int32:
    return len(xs)


def alias(xs: Iterable[Own[R]]) -> int32:
    # A second name carries the one traversal.
    b = xs  # tpyc: ok
    t = 0
    for x in b:
        t += x.v
    return t


def relist(xs: Counted[Own[R]]) -> int32:
    # `list()` infers its element through the sub-protocol.
    ys = list(xs)  # tpyc: ok
    return len(ys)


def keep(r: Own[R]) -> int32:
    rs = [r]
    return rs[0].v


def keep_s(s: Own[S]) -> Own[S]:
    return s


def mk_s() -> Own[list[S]]:
    return [S("p"), S("q")]


def gen_r() -> Iterator[Own[R]]:
    yield R(1)
    yield R(2)


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def main() -> None:
    d = [R(1), R(2)]
    print("subprotocol dead", take_it(d))  # tpyc: ok
    live = [R(1), R(2)]
    print("subprotocol live", take_it(live), len(live))  # tpyc: warning(/copies R elements/)
    ci = [R(1), R(2)]
    print("subprotocol copy_iter", take_it(copy_iter(ci)), ci[0].v)  # tpyc: ok
    print("counted fresh", take_counted(mk()))  # tpyc: ok
    c = [R(3)]
    print("counted dead", take_counted(c))  # tpyc: ok
    cl = [R(3)]
    print("counted live", take_counted(cl), len(cl))  # tpyc: warning(/copies R elements; use copy\(\) to make this explicit/)
    print("iterator", take_iter(gen_r()))  # tpyc: ok
    s = [R(1), R(2)]
    print("sequence", take_seq(s), len(s))  # tpyc: ok
    md = [R(1), R(2)]
    print("map dead", list(map(keep, md)))  # tpyc: ok
    print("map fresh", list(map(keep, mk())))  # tpyc: ok
    print("alias", alias(mk()))
    print("relist", relist(mk()))
    rd = [R(1), R(2)]
    print("rows dead", take_rows(rd))  # tpyc: ok
    rl = [R(1), R(2)]
    print("rows live", take_rows(rl), len(rl))  # tpyc: warning(/copies R elements/)
    ri = [R(1), R(2)]
    # An iterator object passes as itself at the sub-protocol, as at
    # `Iterable[Own[R]]`.
    print("rows iter", take_rows(iter(ri)), len(ri))  # tpyc: warning(/copies R elements/)
    print("rows iter fresh", take_rows(iter(mk())))  # tpyc: ok
    print("dynamic bag", take_bag(OneBag(R(4))))  # tpyc: ok
    cc = [R(3)]
    print("counted copy", take_counted(copy(cc)), cc[0].v)  # tpyc: ok
    ps = [(R(1), 1)]
    ps.append((R(2), 2))
    print("tuple dead", take_pairs(ps))  # tpyc: ok
    lp = [(R(1), 1)]
    lp.append((R(2), 2))
    print("tuple live", take_pairs(lp), len(lp))  # tpyc: warning(/copies tuple\[R, int32\] elements/)
    gc = [R(1), R(2)]
    gc.append(R(3))
    print("generic counted", count(gc))  # tpyc: ok
    # A callable taking each element over, fed a lent iterator: the elements
    # are copied (warned), so the caller's list keeps its own.
    ns = [S("a"), S("b")]
    ms = list(map(keep_s, iter(ns)))  # tpyc: warning(/copies S elements/)
    print("map lent", ms[1].s, ns[0].s, ns[1].s, len(ns))
    rs = [S("c"), S("d")]
    mr = list(map(keep_s, reversed(rs)))  # tpyc: warning(/copies S elements/)
    print("map reversed lent", mr[0].s, rs[0].s, len(rs))
    # An iterator holding a fresh list owns its elements: moved.
    print("map iter fresh", [x.s for x in map(keep_s, iter(mk_s()))])  # tpyc: ok
    print("map reversed fresh", [x.s for x in map(keep_s, reversed(mk_s()))])  # tpyc: ok
    # Fed a container it holds, the elements are moved.
    print("map fresh moved", [x.s for x in map(keep_s, mk_s())])  # tpyc: ok
    # A caller in a comprehension hands a fresh argument over each iteration.
    print("comprehension caller", [take_it(mk()) for _ in range(2)])  # tpyc: ok
