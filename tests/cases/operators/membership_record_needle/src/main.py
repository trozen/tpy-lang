# `x in xs` with a user-RECORD needle. Containment is CPython's
# `x is e or x == e`, so the render is the identity-aware
# `::tpy::seq_contains` helper rather than `std::ranges::contains`, which
# compares with `==` alone. Over a CONTAINER haystack it is the one spelling:
# an open-`T` needle takes it too, so the identity leg is picked per
# instantiation. The TUPLE-LITERAL haystack is a different render (an `==`
# OR-chain) and a record needle against one still rejects at
# `binop.shape.in.record`, so there is no section for it here.
#
# The NaN sections are the witness AND the value-vs-reference check: `Pt`
# compares its float field, so a NaN instance is not equal to itself and is
# found only by the identity leg -- a needle copied across the call boundary
# would print False where CPython prints True.
import asyncio
from typing import Iterator

from tpy import Equatable, Own, float64, int32, readonly


class Pt:
    v: float64

    def __init__(self, v: float64) -> None:
        self.v = v

    def __eq__(self, other: "Pt") -> bool:
        return self.v == other.v


class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: "Tag") -> bool:
        return self.n == other.n


class Bag:
    tags: list[Tag]

    def __init__(self) -> None:
        self.tags = [Tag(1), Tag(2)]

    # method: the same membership test inside a record method body.
    def has(self, t: Tag) -> bool:
        return t in self.tags  # tpyc: ok


# free function: a reflexive `__eq__` needle over a container PARAM haystack.
def reflexive(tags: list[Tag], t: Tag) -> bool:
    return t in tags  # tpyc: ok


# free function: the needle is the element itself, and its `__eq__` is NOT
# reflexive -- only the identity leg can find it.
def holds(pts: list[Pt], p: Pt) -> bool:
    return p in pts  # tpyc: ok


# free function: `not in` over the same shape.
def lacks(pts: list[Pt], p: Pt) -> bool:
    return p not in pts  # tpyc: ok


# free function: a LOCAL needle equal to an element but not identical to it.
def equal_not_identical(tags: list[Tag]) -> bool:
    q = Tag(2)
    return q in tags  # tpyc: ok


# free function: the test nested in an f-string, over a container FIELD.
def in_fstring(bag: Bag, t: Tag) -> str:
    return f"{t in bag.tags}"  # tpyc: ok


# free function: a slice haystack (a span over the same buffer, so identity
# still holds through it).
def in_slice(pts: list[Pt], p: Pt) -> bool:
    return p in pts[0:2]  # tpyc: ok


# generator: the membership test in a yielding body. Both params only read,
# so the frame captures them const -- the spelling the non-yielding twin
# infers.
def gen_found(pts: list[Pt], probes: list[Pt]) -> Iterator[bool]:
    for q in probes:
        yield q in pts  # tpyc: ok


# async: the membership test in a coroutine body.
async def async_holds(pts: list[Pt], p: Pt) -> bool:
    return p in pts  # tpyc: ok


# closure: the membership test inside a nested def, over the captured
# haystack.
def via_closure(tags: list[Tag], t: Tag) -> bool:
    def look() -> bool:
        return t in tags  # tpyc: ok
    return look()


# generic body: an OPEN-T needle. The same helper renders here, so the
# identity leg is decided at each instantiation -- it fires at `Pt` and not
# at `int32`. The needle is spelled `readonly[T]` because a plain `T`
# parameter is a NON-const reference at a record instantiation, which no
# read-only caller can bind (BUGS.md#generic-ref-param-drops-const).
def has[T: Equatable](xs: list[T], x: readonly[T]) -> bool:
    return x in xs  # tpyc: ok


# comprehension: the membership test as the comprehension element.
def comp_flags(tags: list[Tag], probes: list[Tag]) -> Own[list[bool]]:
    return [t in tags for t in probes]  # tpyc: ok


def main() -> None:
    tags = [Tag(1), Tag(2)]
    print("reflexive", reflexive(tags, Tag(1)), reflexive(tags, Tag(9)))
    print("equal-not-identical", equal_not_identical(tags))
    print("method", Bag().has(Tag(2)), Bag().has(Tag(9)))
    print("fstring", in_fstring(Bag(), Tag(1)))
    print("comp", comp_flags(tags, [Tag(2), Tag(7)]))
    print("closure", via_closure(tags, Tag(1)), via_closure(tags, Tag(9)))

    pts = [Pt(float64("nan")), Pt(1.0)]
    # Each `p` is the element itself, so containment must hold for the NaN one
    # too -- that is the identity leg, and it must survive the call boundary.
    i = 0
    for p in pts:
        print("holds", i, holds(pts, p), lacks(pts, p))
        i += 1
    # The same NaN element reached by subscript rather than by the loop var.
    print("holds-nan", holds(pts, pts[0]))
    print("slice", in_slice(pts, pts[0]))
    gflags = []
    for r in gen_found(pts, pts):
        gflags.append(r)
    print("generator", gflags)
    print("async", asyncio.run(async_holds(pts, pts[0])))
    # The generic body at a record instantiation: each `q` IS an element, so
    # the NaN one is found only through the helper's identity leg.
    j = 0
    for q in pts:
        print("generic-record", j, has(pts, q))
        j += 1
    ns = [1, 2, 3]
    print("generic-int", has(ns, 2), has(ns, 9))
    # A DISTINCT NaN is neither identical nor equal, so it stays absent.
    print("distinct-nan", holds(pts, Pt(float64("nan"))))


main()
