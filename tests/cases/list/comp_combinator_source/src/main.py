# List/set/dict comprehensions whose iterable is a builtin iterator combinator
# (zip / map / filter / reversed / enumerate / iter), including the tuple-unpack
# head that zip and enumerate feed and a filter clause over the combinator.
# The zip/enumerate legs over a REFERENCE element type mutate through the loop
# var and print the source afterwards: the combinators lend the element, so the
# mutation must reach it. For reference elements that holds only when every
# `zip` argument is a NAMED begin()/end() container and every
# `enumerate`/`map`/`filter`/`iter` argument is such a container of either value
# category; `reversed()` (BUGS.md#reversed-yields-element-copies) and every other
# argument shape (BUGS.md#nested-combinator-yields-element-copies) are rejected
# there, so the legs here that use one stay on value elements.
from tpy import Int32, Own, Span


class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v

    def bump(self, d: Int32) -> Int32:
        self.v += d
        return self.v


def dbl(v: Int32) -> Int32:
    return v * 2


def odd(v: Int32) -> bool:
    return v % 2 == 1


def node_pos(n: Node) -> bool:
    return n.v > 0


def make_nodes() -> Own[list[Node]]:
    return [Node(1), Node(2)]


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    ys: list[Int32] = [10, 20, 30]
    ws = ["a", "bb"]
    # zip / enumerate reach the comp through the tuple-unpack head.
    print([a + b for a, b in zip(xs, ys)])  # tpyc: ok
    print([i * v for i, v in enumerate(xs)])  # tpyc: ok
    # ... the single-target combinators through the plain loop-var binding.
    print([v for v in map(dbl, xs)])  # tpyc: ok
    print([v for v in filter(odd, xs)])  # tpyc: ok
    print([v for v in reversed(xs)])  # tpyc: ok
    print([v for v in iter(xs)])  # tpyc: ok
    # A str element rides the same arm (the view binding off *__beg).
    print([w for w in reversed(ws)])  # tpyc: ok
    # The set and dict comps take the same source.
    print(sorted({a * b for a, b in zip(xs, ys)}))  # tpyc: ok
    print({k: v for k, v in zip(xs, ys)})  # tpyc: ok
    # A filter clause over a combinator source.
    print([v for v in map(dbl, xs) if v > 2])  # tpyc: ok
    # A REFERENCE element type through zip: the loop var must alias the source
    # element, so the bump lands on ps/qs and not on a copy.
    ps = [Node(1), Node(2)]
    qs = [Node(10), Node(20)]
    print([p.bump(q.v) for p, q in zip(ps, qs)])  # tpyc: ok
    print([p.v for p in ps], [q.v for q in qs])
    # ... and through enumerate, whose tuple pairs a value with the borrow.
    rs = [Node(5), Node(6)]
    print([r.bump(i) for i, r in enumerate(rs)])  # tpyc: ok
    print([r.v for r in rs])
    # ... and through the single-target `filter`, whose callable argument is not
    # a source, so the named container is still the only one that has to lend.
    ts = [Node(7), Node(8)]
    print([t.bump(2) for t in filter(node_pos, ts)])  # tpyc: ok
    print([t.v for t in ts])
    # `enumerate` has an OWNING direct flavor, so an owned-container rvalue
    # still lends -- the difference from zip, which has no such flavor.
    print([n.bump(i) for i, n in enumerate(make_nodes())])  # tpyc: ok
    # A named Span is a begin()/end() container too, and lends into the source
    # list it views.
    us = [Node(11), Node(12)]
    sp: Span[Node] = us
    print([u.bump(y) for u, y in zip(sp, ys)])  # tpyc: ok
    print([u.v for u in us])


main()
