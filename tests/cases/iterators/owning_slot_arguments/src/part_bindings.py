# Arguments through a binding: a narrowed Optional at its last use hands over
# the container it owns, while one bound to existing storage (a parameter, a
# walrus over a live local) is lent; a forwarding alias lends what its caller
# lent. A lent section's warned copy prints only what CPython agrees on.
from typing import Iterable
from tpy import Own, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


def take(xs: Iterable[Own[R]]) -> Own[list[R]]:
    out: list[R] = []
    for x in xs:
        out.append(x)
    return out


def relay(xs: Iterable[Own[R]]) -> Own[list[R]]:
    # The alias carries the one traversal and passes it on as it arrived.
    b = xs  # tpyc: ok
    return take(b)  # tpyc: ok


def from_param(o: list[R] | None) -> int32:
    if o is not None:
        out = take(o)  # tpyc: warning(/copies R elements/)
        out[0].v = 9
        return out[0].v * 10 + len(o)
    return 0


def from_walrus(n: int32) -> int32:
    o: list[R] | None = None
    if n > 0:
        o = [R(4)]
    if (w := o) is not None:
        out = take(w)  # tpyc: warning(/copies R elements/)
        return len(out)
    return 0


def from_local(n: int32) -> int32:
    o: list[R] | None = None
    if n > 0:
        o = [R(5), R(6)]
    if o is not None:
        # The narrowed local owns its list and is read no more: handed over.
        out = take(o)  # tpyc: ok
        return out[1].v
    return 0


def two_slices(xs: Iterable[Own[R]], ys: Iterable[Own[R]]) -> Own[list[R]]:
    out: list[R] = [R(7), R(8)]
    # Two owning parameters, each traversed once by its own slice store.
    out[0:1] = xs  # tpyc: ok
    out[1:2] = ys  # tpyc: ok
    return out


def pending_slice(xs: Iterable[Own[R]]) -> Own[list[R]]:
    # The receiver is an unannotated list literal, its type still pending at
    # the store.
    out = [R(7)]
    out.append(R(8))
    out[0:1] = xs  # tpyc: ok
    return out


def self_slice(xs: list[R]) -> Own[list[int32]]:
    # A list slice-stored into itself: the elements are the warned copy, so
    # the section prints values only (CPython aliases them).
    xs[0:1] = xs  # tpyc: warning(/copies R elements/)
    return [r.v for r in xs]


def main() -> None:
    print("two slices", [r.v for r in two_slices([R(1), R(2)], [R(3)])])
    print("pending slice", [r.v for r in pending_slice([R(1), R(2)])])
    zs = [R(1), R(2)]
    print("self slice", self_slice(zs), len(zs))
    p = [R(1), R(2)]
    print("narrowed param", from_param(p), len(p))
    print("narrowed walrus", from_walrus(1))
    print("narrowed local", from_local(1), from_local(0))
    live = [R(1), R(2)]
    # Lent at the caller: the alias hands the copy on, the caller keeps its
    # elements.
    out = relay(live)  # tpyc: warning(/copies R elements/)
    out[0].v = 9
    live.append(R(3))
    print("relay lent", out[0].v, len(out), len(live))
    print("relay fresh", len(relay([R(7)])))  # tpyc: ok
