# Member-typed record names / None into pointer-variant union arg slots lift
# inline; callees mutate through the lift and callers observe it (aliasing).
from tpy import Int32


class A:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def bump_via_union(self) -> None:
        bump_counter(self)


class Pair:
    a1: A
    a2: A

    def __init__(self, m: Int32, n: Int32) -> None:
        self.a1 = A(m)
        self.a2 = A(n)

    def bump_picked(self, flip: bool) -> None:
        p = self.a1
        if flip:
            p = self.a2
        bump(p)


def bump(u: A | B) -> None:
    if isinstance(u, A):
        u.x = u.x + 1
    else:
        u.y = u.y + 10


def bump_counter(u: Counter | A) -> None:
    if isinstance(u, Counter):
        u.n = u.n + 100


def describe(u: A | B | None) -> Int32:
    if u is None:
        return -1
    if isinstance(u, A):
        return u.x
    return u.y


def via_param(a: A) -> Int32:
    bump(a)
    return a.x


def main() -> None:
    a = A(5)
    print(via_param(a))
    print(a.x)  # the callee mutated the caller's object, not a copy
    print(describe(a))
    print(describe(None))

    c = Counter(1)
    c.bump_via_union()
    print(c.n)

    pair = Pair(3, 30)
    pair.bump_picked(False)
    pair.bump_picked(True)
    print(pair.a1.x, pair.a2.x)

    xs = [A(7), A(8)]
    for elem in xs:
        bump(elem)
    print(xs[0].x, xs[1].x)  # loop-var lifts alias the list elements


main()
