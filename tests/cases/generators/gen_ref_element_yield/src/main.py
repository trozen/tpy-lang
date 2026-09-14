# A borrowed REFERENCE element yielded from a resumable generator frame.
# One admission ladder covers the whole reference axis, so every source shape
# with a borrow lvalue at the yield slot lands at BOTH halves -- records and
# containers. Every section mutates through the yielded element and prints the
# SOURCE back, so a silent copy shows up as a diverging count.
from typing import Iterator

from tpy import int32, readonly


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Rows:
    buf: list[int32]

    def __init__(self) -> None:
        self.buf = [1]

    # 3. generator METHOD, container element off a list PARAM.
    def each(self, xs: list[list[int32]]) -> Iterator[list[int32]]:
        for s in xs:
            yield s  # tpyc: ok
            yield s

    # 5. `self.<field>` at a container yield slot: the storage member binds
    #    the val_or_ref slot bare, the leg the record half already had.
    def field_twice(self) -> Iterator[list[int32]]:
        yield self.buf  # tpyc: ok
        yield self.buf


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


# 1. free generator, list-param loop var -- the container half of the axis,
#    the cell this case exists for.
def each_list(xs: list[list[int32]]) -> Iterator[list[int32]]:
    for s in xs:
        yield s  # tpyc: ok
        yield s


# 2. the record twin, an inverse guard: it compiled before the unification and
#    must keep the same emit through it.
def each_rec(xs: list[Box]) -> Iterator[Box]:
    for b in xs:
        yield b  # tpyc: ok
        yield b


# 4. `*args` pack element.
def each_pack(*xs: list[int32]) -> Iterator[list[int32]]:
    for s in xs:
        yield s  # tpyc: ok
        yield s


# 6. a FRAME-LOCAL list: the yielded borrow points into frame-owned storage,
#    which outlives every pull -- the frame reads its own view back after the
#    consumer mutated through it.
def each_local() -> Iterator[list[int32]]:
    own: list[list[int32]] = [[1], [2]]
    for s in own:
        yield s  # tpyc: ok
        yield s
    print("framelocal-inner", len(own[0]), len(own[1]))


# 7. an ALIAS local (`b = xs[0]`) at the record slot -- a pointer local that is
#    neither a frame slot nor a loop var.
def each_alias(xs: list[Box]) -> Iterator[Box]:
    b = xs[0]
    yield b  # tpyc: ok
    yield b


# 8. a TERNARY of two frame-slot records: the branch-picked borrow, the leg the
#    record half gains from the unification.
def each_ternary(flag: bool) -> Iterator[Box]:
    p = Box(1)
    q = Box(2)
    yield p if flag else q  # tpyc: ok
    yield p if flag else q
    print("ternary-inner", p.v, q.v)


# 9. the axis's other container members.
def each_dict(xs: list[dict[int32, int32]]) -> Iterator[dict[int32, int32]]:
    for d in xs:
        yield d  # tpyc: ok
        yield d


def each_set(xs: list[set[int32]]) -> Iterator[set[int32]]:
    for s in xs:
        yield s  # tpyc: ok
        yield s


# 10. position coverage: a `with` body and a `finally` body.
def each_with(xs: list[list[int32]]) -> Iterator[list[int32]]:
    with Guard():
        for s in xs:
            yield s  # tpyc: ok
            yield s


def each_finally(xs: list[list[int32]]) -> Iterator[list[int32]]:
    try:
        print("finally-try")
    finally:
        for s in xs:
            yield s  # tpyc: warning(/'yield' inside 'finally'/)
            yield s


# 11. a `readonly[T]` element yields a CONST borrow: the frame's `__next__`
#     returns `std::expected<val_or_ref<const std::vector<int32_t>>,
#     StopIteration>` and the consumer binds `const auto&`, so a pull hands out
#     a pointer instead of copying the whole vector out of the source.
class ROBag:
    buf: list[int32]

    def __init__(self) -> None:
        self.buf = [1]

    def twice(self) -> Iterator[readonly[list[int32]]]:
        yield self.buf  # tpyc: ok
        yield self.buf


# 12. an `Iterator[T]` PARAM as a for-head source (no source struct in the
#     consumer's frame, so its `__for_r` slot is spelled from the element type
#     rather than read off the producer): the slot must be the same
#     `val_or_ref<T>` the producer's `__next__` returns.
def boxes(xs: list[Box]) -> Iterator[Box]:
    for b in xs:
        yield b  # tpyc: ok
        yield b


def relay(it: Iterator[Box]) -> Iterator[int32]:
    for b in it:
        # Mutating through the borrowed element reaches the ORIGINAL list.
        b.v += 100
        # `yield b.v` would copy an int32, but the ephemeral-borrow escape
        # check roots on `b` -- BUGS.md#ephemeral-value-read-escape.
        v = b.v
        yield v
    yield -1


def sec_freelist() -> None:
    # A bare `[[1], [2]]` would infer Array, not list
    # (BUGS.md#copy-array-literal-into-list-slot).
    a: list[list[int32]] = [[1], [2]]
    for s in each_list(a):
        s.append(9)
    print("freelist", len(a[0]), len(a[1]))


def sec_freerec() -> None:
    a = [Box(1), Box(2)]
    for b in each_rec(a):
        b.v += 10
    print("freerec", a[0].v, a[1].v)


def sec_method() -> None:
    a: list[list[int32]] = [[1], [2]]
    for s in Rows().each(a):
        s.append(9)
    print("method", len(a[0]), len(a[1]))


def sec_pack() -> None:
    a: list[int32] = [1]
    b: list[int32] = [2]
    for s in each_pack(a, b):
        s.append(9)
    print("pack", len(a), len(b))


def sec_selffield() -> None:
    r = Rows()
    for s in r.field_twice():
        s.append(9)
    print("selffield", len(r.buf))


def sec_framelocal() -> None:
    for s in each_local():
        s.append(9)
    print("framelocal", "done")


def sec_alias() -> None:
    a = [Box(1), Box(2)]
    for b in each_alias(a):
        b.v += 10
    print("alias", a[0].v, a[1].v)


def sec_ternary() -> None:
    for c in each_ternary(True):
        c.v += 10
    print("ternary", "done")


def sec_dict() -> None:
    a = [{1: 1}, {2: 2}]
    for d in each_dict(a):
        d[9] = 9
    print("dict", len(a[0]), len(a[1]))


def sec_set() -> None:
    a = [{1}, {2}]
    for s in each_set(a):
        s.add(9)
    print("set", len(a[0]), len(a[1]))


def sec_with() -> None:
    a: list[list[int32]] = [[1], [2]]
    for s in each_with(a):
        s.append(9)
    print("with", len(a[0]), len(a[1]))


def sec_finally() -> None:
    a: list[list[int32]] = [[1], [2]]
    for s in each_finally(a):
        s.append(9)
    print("finally", len(a[0]), len(a[1]))


def sec_readonly() -> None:
    h = ROBag()
    for s in h.twice():
        # Mutating the SOURCE between pulls is visible through the const
        # borrow, as in CPython; the value slot this used to spell copied at
        # the pull and printed the pre-mutation length.
        h.buf.append(7)
        print("readonly", len(s))


def sec_iterparam() -> None:
    a = [Box(1), Box(2)]
    for v in relay(boxes(a)):
        print("iterparam", v)
    print("iterparam-src", a[0].v, a[1].v)


def main() -> None:
    sec_freelist()
    sec_freerec()
    sec_method()
    sec_pack()
    sec_selffield()
    sec_framelocal()
    sec_alias()
    sec_ternary()
    sec_dict()
    sec_set()
    sec_with()
    sec_finally()
    sec_readonly()
    sec_iterparam()


main()
