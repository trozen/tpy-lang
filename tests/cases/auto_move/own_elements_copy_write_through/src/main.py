# The warned divergence of an owning element slot fed a live source: the `Own`
# loop variable / callback parameter is a COPY, so a write through it does not
# reach the caller's element where CPython (which aliases) writes it -- also
# when the body only writes it, never moving it. Also the copy hooks a `set`
# runs: one per stored element, never one per element `set()` / `extend` is
# handed over. No CPython run: the outputs differ by design (and CPython
# cannot subscript a generic function, `each[Own[Item]]`).
from __future__ import annotations
from tpy import int32, Own
from typing import Iterable, Iterator, Self


class Item:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v


def bump_all(source: Iterable[Own[Item]]) -> Own[list[Item]]:
    out: list[Item] = []
    for x in source:
        x.value += 10  # writes the loop variable's own copy
        out.append(x)
    return out


def append_only(source: Iterable[Own[list[int32]]]) -> int32:
    n: int32 = 0
    for x in source:
        x.append(99)  # writes the loop variable's own copy, never moved on
        n += len(x)
    return n


def bump_only(source: Iterable[Own[Item]]) -> int32:
    t: int32 = 0
    for x in source:
        x.value += 1  # writes the loop variable's own copy, never moved on
        t += x.value
    return t


def closure_write(source: Iterable[Own[list[int32]]]) -> int32:
    n: int32 = 0
    for x in source:
        def bump_x() -> None:
            x.append(1)  # a closure writes the loop variable's own copy
        bump_x()
        n += len(x)
    return n


class H:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> Own[Self]:
        print("copy", self.n)
        return H(self.n)

    def __hash__(self) -> int:
        return self.n

    def __eq__(self, o: H) -> bool:
        return self.n == o.n


def make_hs() -> Own[list[H]]:
    out: list[H] = []
    out.append(H(1))
    return out


def make_h_set() -> Own[set[H]]:
    out: set[H] = set()
    out.add(H(3))  # the set's own copy of the element (BUGS.md#ordered-set-map-key-stored-twice)
    return out


def fresh_hs() -> Iterator[Own[H]]:
    yield H(2)


def each[T](xs: list[T]) -> Iterator[T]:
    for x in xs:
        yield x


class Each:
    def each[T](self, xs: list[T]) -> Iterator[T]:
        for x in xs:
            yield x


class GEach[U]:
    def each[T](self, xs: list[T]) -> Iterator[T]:
        for x in xs:
            yield x


# A type argument the call writes (`each[Own[Item]]`, free function, method
# and a generic record's method) counts as written: the loop variable owns the step and moves on,
# no copy. The step itself is an unwarned copy of the lent element
# (BUGS.md#lent-list-at-own-iterable-param-copies).
def explicit_type_arg(items: list[Own[Item]]) -> None:
    keep: list[Item] = []
    for x in each[Own[Item]](items):
        keep.append(x)  # tpyc: ok
    keep[0].value = 99
    print("explicit type arg", items[0].value, keep[0].value)
    mkeep: list[Item] = []
    for y in Each().each[Own[Item]](items):
        mkeep.append(y)  # tpyc: ok
    mkeep[0].value = 77
    print("explicit method type arg", items[0].value, mkeep[0].value)
    gkeep: list[Item] = []
    for z in GEach[int32]().each[Own[Item]](items):
        gkeep.append(z)  # tpyc: ok
    gkeep[0].value = 55
    print("explicit generic record method type arg", items[0].value,
          gkeep[0].value)


# The callback writes and returns a field, never the `Own` object itself.
def bump(k: int32, x: Own[Item]) -> int32:  # tpyc: warning(/Own\[Item\] param 'x' is never consumed/)
    x.value += k  # writes the callback parameter's own copy
    return x.value


def main() -> None:
    items: list[Item] = [Item(1)]
    out = bump_all(items)  # tpyc: warning(/copies Item elements/)
    print("loop", out[0].value, items[0].value)
    for v in map(bump, [10], items):  # tpyc: warning(/copies Item elements/)
        print("map", v)
    print("map after", items[0].value)
    ll: list[list[int32]] = [[1, 2]]
    print("append only", append_only(ll), ll)  # tpyc: warning(/copies list\[int32\] elements/)
    print("bump only", bump_only(items), items[0].value)  # tpyc: warning(/copies Item elements/)
    print("closure write", closure_write(ll), ll)  # tpyc: warning(/copies list\[int32\] elements/)
    # set() and extend move in what they are handed; the one copy per element
    # left is the set's own (BUGS.md#ordered-set-map-key-stored-twice).
    print("set of list")
    hs = set(make_hs())  # tpyc: ok
    print("set of gen")
    gen = fresh_hs()
    hs2 = set(gen)  # tpyc: ok
    print("extend of set")
    hl: list[H] = []
    hl.extend(make_h_set())  # tpyc: ok
    print("sets", len(hs), len(hs2), len(hl))
    # Regression guard: a lent duplicate is not copied before the set finds
    # it present, so two equal elements store (and copy) once.
    dup: list[H] = []
    dup.append(H(4))
    dup.append(H(4))
    print("set of lent duplicates")
    hs3 = set(dup)  # tpyc: warning(/copies H elements/)
    print("dups", len(hs3), len(dup))
    explicit_type_arg(items)


main()
