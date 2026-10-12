# A fixed-length comprehension of a @nocopy element builds a stack Array by
# aggregate construction -- no copy, no default-construct, no assign. The
# elements are borrowed by iteration, subscript, and param passing; @nocopy
# turns any silent copy into a compile error. A write through the loop
# variable reaches the source; a costly value element is copied, so a
# write to the source does not reach the variable.
from tpy import int32, Array, nocopy


@nocopy
class Handle:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def bump_all(hs: Array[Handle, 4]) -> None:
    for h in hs:
        h.v += 10


def doubled(hs: Array[Handle, 4]) -> None:
    ys = [Handle(h.v * 2) for h in hs]  # tpyc: type(/Array\[Handle, 4\]/)
    print(ys[0].v, ys[3].v)


def main() -> None:
    xs = [Handle(i) for i in range(4)]  # tpyc: type(/Array\[Handle, 4\]/)
    total = 0
    for h in xs:
        total += h.v
    print(total)
    bump_all(xs)
    print(xs[0].v, xs[3].v)
    doubled(xs)


main()


# mutating_elem: the element expression writes through the loop variable,
# so the Array comprehension binds it as a mutable reference into the
# source, and the write is visible in the source afterwards.
@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> int32:
        self.n += 1
        return self.n


def mutating_elem() -> None:
    # Annotated: an inferred Array local takes the list comprehension route
    # (BUGS.md#unannotated-array-local-comprehension-list-route).
    cs: Array[Counter, 2] = [Counter(i) for i in range(2)]
    rs = [Counter(c.bump()) for c in cs]  # tpyc: type(/Array\[Counter, 2\]/)
    print("mutating_elem:", rs[0].n, rs[1].n)
    print("mutating_elem:", cs[0].n, cs[1].n)


mutating_elem()


# unannotated_source: the same comprehension over an INFERRED Array local.
def unannotated_source() -> None:
    cs = [Counter(i) for i in range(2)]  # tpyc: type(/Array\[Counter, 2\]/)
    # Builds a list where an Array is right
    # (BUGS.md#unannotated-array-local-comprehension-list-route).
    rs = [Counter(c.n + 5) for c in cs]  # tpyc: type(/list\[Counter\]/)
    print("unannotated_source:", rs[0].n, rs[1].n, len(rs))


unannotated_source()


# source_written: the element expression writes the source before it reads
# the loop variable; the variable keeps the element it was bound to, as in
# CPython.
def set_all(a: Array[int, 3], v: int) -> bool:
    for i in range(3):
        a[i] = v
    return True


def source_written() -> None:
    a: Array[int, 3] = [1, 2, 3]
    # The subject: `y` reads 1, not the 100 `set_all` wrote over it.
    m = [y if set_all(a, 100) else y for y in a]
    print("source_written:", m[0], m[1], m[2])


source_written()
