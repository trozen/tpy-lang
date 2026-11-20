# Forwarders hand an open `T` parameter on as the twin does: a generic record's
# `operator[]` / `operator()` / `+` shims and a `@dynamic` adapter move it at a
# value `T` (the caller's argument stays intact) and bind it at a class `T`
# (the conformer's write shows in the caller's object). Element shapes into a
# `list[T]`: the caller's element is untouched.
from typing import Protocol
from tpy import dynamic, int32
from fixtures import C, V


class Bag[T]:
    def __init__(self, v: T) -> None:
        # the hedged copy warning is for a reference `T`
        self.v = v  # tpyc: warning(/may copy T into field/)

    def __getitem__(self, k: T) -> bool:
        return True

    def __call__(self, x: T) -> bool:
        return False

    def __add__(self, o: T) -> int32:
        return 1


@dynamic
class Sink[T](Protocol):
    def put(self, x: T) -> int32: ...


class CSink:
    def put(self, x: C) -> int32:
        x.n += 10
        return x.n


def use(k: Sink[C], c: C) -> int32:
    return k.put(c)


# each append copies at a reference `T`, hence the hedged warnings
def first[T](xs: list[T], out: list[T]) -> None:
    out.append(xs[0])  # tpyc: warning(/may copy T into owned storage/)


def keep[T](x: T, out: list[T]) -> None:
    out.append(x)  # tpyc: warning(/may copy T into owned storage/)


def head[T](t: tuple[T, int32], out: list[T]) -> None:
    out.append(t[0])  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


def main() -> None:
    b = Bag(V("k"))
    k = V("key")
    # the shims' forwarding of their own parameter
    print("shim getitem", b[k], k.s)
    print("shim call", b(k), k.s)
    print("shim add", b + k, k.s)
    c = C(1)
    # a class `T` adapter binds the caller's object
    print("dynamic class", use(CSink(), c), c.n)
    c.n = 5
    print("dynamic class after", c.n)
    xs = [V("x0")]
    out: list[V] = []
    first(xs, out)
    print("elem first", xs[0].s, out[0].s)
    v = V("v1")
    keep(v, out)
    print("elem keep", v.s, out[1].s)
    t = (V("t0"), 1)
    head(t, out)
    print("elem head", t[0].s, out[2].s)
