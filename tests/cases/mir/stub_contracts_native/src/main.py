# MIR verdicts for calls to user `@native` stubs: a free stub is admitted only with a
# declared contract over readonly leaf parameters, and a user container's methods and
# loops are modeled from its declared element storage (`elements=True`, `mutates=`).
# tpy: include("native_types.hpp")
from typing import Iterator
from tpy import int32, pure, readonly, Comparable, NativeIterable, Own, StrView, String
from tpy.extern import native


class Rec:
    n: int32

    def __init__(self, n: int32):
        self.n = n


@pure
@native("probe_view")
def probe_view(s: str) -> StrView: ...


@native("probe_plain")
def probe_plain(x: float) -> float: ...


@native("probe_tick")
def probe_tick() -> float: ...


@pure
@native("probe_text")
def probe_text(x: Comparable) -> str: ...


@native("probe_fill", transient=True)
def probe_fill(s: String) -> None: ...


@native("probe_bump", transient=True)
def probe_bump(r: Rec) -> None: ...


@native("::ProbeRing", elements=True)
class Ring[T](NativeIterable[T]):
    def __init__(self) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, i: int32) -> T: ...

    @native("put", mutates="elements")
    def put(self, i: int32, v: Own[T]) -> None: ...

    @native("push")
    def push(self, v: Own[T]) -> None: ...


# free function: a stub with neither @pure nor transient=True
def unmarked(x: float) -> float:  # tpyc: mir(uncovered /^stub declares no contract$/)
    return probe_plain(x)


# free function: lending no argument is no contract, an unmarked stub may reach any storage
def unmarked_nullary() -> float:  # tpyc: mir(uncovered /^stub declares no contract$/)
    return probe_tick()


# free function: the str result may borrow what the protocol parameter binds, and an int32 has no storage
def text_of_scalar(i: int32) -> int32:  # tpyc: mir(uncovered /^stub result may borrow a scalar argument$/)
    return len(probe_text(i))


# free function: a transient stub taking a record by mutable reference
def mut_ref(r: Rec) -> None:  # tpyc: mir(uncovered /^stub parameter is not a readonly leaf$/)
    probe_bump(r)


# free function: a transient stub declares no const verdict for its String
def mutable_leaf(s: String) -> None:  # tpyc: mir(uncovered /^stub parameter is not a readonly leaf$/)
    probe_fill(s)


# free function: a @pure stub returning a view borrows its lent argument
def view_result(s: str) -> int32:  # tpyc: mir(covered)
    v = probe_view(s)  # tpyc: mir_borrowed(v) mir_borrows(v, s)
    return 1


# user container: a loop borrows the element its stub declares
def ring_total(r: Ring[int32]) -> int32:  # tpyc: mir(covered)
    t = 0
    for v in r:
        t += v
    return t


# user container: a field read through an element
def ring_first(r: Ring[Rec]) -> int32:  # tpyc: mir(covered)
    return r[0].n


# user container: a mutating method that declares nothing writes the structure
def ring_grow(r: Ring[int32]) -> None:  # tpyc: mir(covered) mir_summary(known)
    r.push(4)  # tpyc: mir_write(r[structure])


# user container: an undeclared method under a live loop (never taken at runtime)
def ring_push_in_loop(r: Ring[int32], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in r:
        if flag:
            r.push(9)  # tpyc: warning(/'push' invalidates the iterator/)
        t += v
    return t


# user container: `mutates="elements"` replaces in place, so sema does not warn;
# MIR still reports the element write under the cursor (index-blind)
def ring_put_in_loop(r: Ring[int32], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in r:
        if flag:
            r.put(0, 9)  # tpyc: ok mir_write(r[elements])
        t += v
    return t


def main() -> None:
    ring = Ring[int32]()
    ring.push(1)
    ring.push(2)
    print("ring_total:", ring_total(ring))
    ring_grow(ring)
    print("ring_grow:", ring_total(ring))
    print("ring_push_in_loop:", ring_push_in_loop(ring, False))
    print("ring_put_in_loop:", ring_put_in_loop(ring, True), ring_total(ring))
    recs = Ring[Rec]()
    recs.push(Rec(5))
    print("ring_first:", ring_first(recs))
    print("unmarked:", unmarked(1.5))
    print("unmarked_nullary:", unmarked_nullary())
    print("text_of_scalar:", text_of_scalar(42))
    r = Rec(1)
    mut_ref(r)
    print("mut_ref:", r.n)
    s = String("ab")
    mutable_leaf(s)
    print("mutable_leaf:", s)
    print("view_result:", view_result("xyz"))


main()
