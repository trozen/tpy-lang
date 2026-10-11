# The warned divergence of an owning element slot fed a live source: the `Own`
# loop variable / callback parameter is a COPY, so a write through it does not
# reach the caller's element where CPython (which aliases) writes it -- also
# when the body only writes it, never moving it. No CPython run: the outputs
# differ by design.
from __future__ import annotations
from tpy import int32, Own
from typing import Iterable


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


main()
