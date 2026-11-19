# A local holding an open `T` result is read the same way at every binding
# position -- plain, rebound, branch- or try-predeclared, a generator's
# frame, a generic method's -- and a yield or a store of it is an owning sink:
# at `T = P` it lends the caller's object, as the hand-written twin does.
from typing import Iterator
from tpy import Fn, Own, int32, readonly


class Q:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n, n]


def ident(q: Q) -> Q:
    return q


def concat(a: str, b: str) -> str:
    return a + b


def branch[T](f: Fn[[T], T], x: T, c: bool) -> T:
    if c:
        y = f(x)
    else:
        y = f(x)
    # the branch-predeclared local points at `x` itself
    return y  # tpyc: ok


def branch_store[T](f: Fn[[T], T], x: T, c: bool, out: list[T]) -> None:
    if c:
        y = f(x)
    else:
        y = f(x)
    # a store copies the lent object (the twin's hedge); `x` keeps its items
    out.append(y)  # tpyc: warning(/may copy T into owned storage/)


def tried[T](f: Fn[[T], T], x: T) -> T:
    try:
        y = f(x)
    except ValueError:
        y = x
    # the try-predeclared local lends as the branch one does
    return y  # tpyc: ok


def gen_y[T](f: Fn[[T], T], x: T) -> Iterator[T]:
    y = f(x)
    # a frame local's yield lends the object at `T = Q`
    yield y  # tpyc: ok
    yield y  # tpyc: ok


def stored[T](x: T, d: dict[int32, T]) -> None:
    # a store of `x: T` into a dict element: the twin's hedged copy
    d[1] = x  # tpyc: warning(/may copy T into container/)


def red[T](func: Fn[[T, T], T], xs: list[T]) -> T:
    acc = xs[0]
    for i in range(1, len(xs)):
        acc = func(acc, xs[i])
    # at `T = str` the local's own slot is returned, as the twin's `acc`
    return acc  # tpyc: ok


def count[T](x: readonly[T]) -> int32:
    return 1


def fwd[T](x: T) -> int32:
    # an inferred nested call at a `readonly[T]` parameter borrows
    return count(x)  # tpyc: ok


def each[T](xs: list[T]) -> Iterator[T]:
    for x in xs:
        yield x


def walk(ys: list[Own[Q]]) -> int32:
    # a `list[Own[Q]]` argument infers `T = Q` (an element is held either
    # way): the generic lends the elements
    n = 0
    for x in each(ys):  # tpyc: ok
        x.items.append(1)
        n += len(x.items)
    return n


class Ops:
    def via[T](self, f: Fn[[T], T], x: T) -> T:
        y = f(x)
        # a generic method's rebound open-result local lends too
        y = f(y)  # tpyc: ok
        return y


def main() -> None:
    # free function, branch-predeclared local: the result is `q` itself
    q = Q(1)
    branch(ident, q, True).items.append(9)
    branch(ident, q, False).items.append(8)
    print("bind-branch", len(q.items))
    # the store copies, never moves out of `q`
    out: list[Q] = []
    branch_store(ident, q, False, out)  # tpyc: ok
    print("bind-branch-store", len(q.items), len(out))
    # try-predeclared local
    tried(ident, q).items.append(7)
    print("bind-try", len(q.items))
    # generator frame local, yielded twice
    for r in gen_y(ident, q):
        r.items.append(6)
    print("bind-gen", len(q.items))
    # dict element store: `q` keeps its list
    d: dict[int32, Q] = {}
    stored(q, d)  # tpyc: ok
    print("bind-dict", len(q.items), len(d[1].items))
    # value instantiation: the snapshot local is returned
    print("bind-red-str", red(concat, ["a", "b", "c"]))
    print("bind-fwd", fwd(q))
    # method position
    Ops().via(ident, q).items.append(5)
    print("bind-method", len(q.items))
    print("bind-own-list", walk([Q(1), Q(2)]))
