# A comprehension gives its source up by the `for` loop's rule: a dead or fresh
# source with a consuming `__iter__` is consumed when the element moves the
# clause variable out (the source's elements are not value types); one whose
# element only reads it is lent. Each section beside its `for` twin. `@nocopy`
# elements make a silent copy a build error.
from typing import Iterator, Self
from tpy import Own, int32, dispatch, nocopy, own_iter


@nocopy
class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class Bag:
    items: list[R]

    def __init__(self) -> None:
        self.items = [R(1), R(2)]

    @dispatch
    def __iter__(self) -> Iterator[R]:
        for r in self.items:
            yield r

    @dispatch
    def __iter__(self: Own[Self]) -> Iterator[Own[R]]:
        while self.items:
            yield self.items.pop(0)


@nocopy
class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def finish(self: Own[Self]) -> Own[Self]:
        self.xs.append(0)
        return self


def keep(r: Own[R]) -> Own[R]:
    return r


def take(b: Own[Box]) -> Own[Box]:
    return b


def fresh() -> Own[list[Box]]:
    return [Box(7)]


def moved_out() -> None:
    # a dead record whose element moves the clause variable out is consumed
    bag = Bag()
    ks = [keep(r) for r in bag]  # tpyc: ok
    ks[0].v = 10
    print("comp_consuming arg", [k.v for k in ks])
    # the element itself is the clause variable
    bag2 = Bag()
    ks2 = [r for r in bag2]  # tpyc: ok
    print("comp_consuming sink", [k.v for k in ks2])
    # a dict comprehension's value moves it out
    bag3 = Bag()
    by_v = {r.v: r for r in bag3}  # tpyc: ok
    print("comp_consuming dict", sorted(by_v.keys()))
    # the `for` twin
    bag4 = Bag()
    out: list[R] = []
    for r4 in bag4:
        out.append(keep(r4))  # tpyc: ok
    print("comp_consuming for", [k.v for k in out])


def read_only() -> None:
    # an element that only reads the clause variable lends, dead or fresh
    bag = Bag()
    print("comp_consuming read dead", [r.v for r in bag])
    print("comp_consuming read fresh", [r.v * 2 for r in Bag()])
    # the `for` twin
    bag2 = Bag()
    t = 0
    for r2 in bag2:
        t += r2.v
    print("comp_consuming read for", t)


def comp_own(xs: Own[list[R]]) -> int32:  # tpyc: ok
    # the comprehension consumes the `Own` parameter (no "never consumed")
    out = [r for r in xs]
    return len(out)


def for_own(xs: Own[list[R]]) -> int32:  # tpyc: ok
    out: list[R] = []
    for r in xs:
        out.append(r)
    return len(out)


def comp_own_arg(xs: Own[list[R]]) -> int32:  # tpyc: ok
    out = [keep(r) for r in xs]
    return len(out)


def own_params() -> None:
    print("comp_consuming own param", comp_own([R(1)]), for_own([R(2)]),
          comp_own_arg([R(3)]))


def explicit() -> Own[list[Box]]:
    # the explicit spelling: own_iter() gives the list up, each Box moved
    # into its consuming receiver
    # annotated: the subject is a list, not the Array the literal would be
    ws: list[Box] = [Box(1), Box(2)]
    return [b.finish() for b in own_iter(ws)]  # tpyc: ok


def main() -> None:
    moved_out()
    read_only()
    own_params()
    done = explicit()
    done[0].xs.append(9)
    print("comp_consuming own_iter", done[0].xs, done[1].xs)
