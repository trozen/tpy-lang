# Container literal elements and the membership needle: each element lands
# in its own member, a tuple element's elements at their element paths, a
# recursive alias's nested literal's elements in theirs.
from typing import Iterator
from tpy import int16, int32, int64, uint8, uint32, uint64


type Tr = int16 | uint8 | list[Tr]
type Js = None | bool | int | str | list[Js] | dict[str, Js]


def tr_len(t: Tr) -> int:
    if isinstance(t, list):
        return len(t)
    return -1


def js_len(t: Js) -> int:
    if isinstance(t, list):
        return len(t)
    return -1


class Seq:
    def __init__(self) -> None:
        pass

    def __iter__(self) -> Iterator[int64 | uint64]:
        yield int64(6)


def main() -> None:
    # list literal elements: an int literal and a str literal
    ys: list[int16 | str] = [5, "a"]  # tpyc: ok
    print("containers-list", len(ys), isinstance(ys[1], str))
    # dict literal value
    d: dict[str, int16 | uint8] = {"k": 5}  # tpyc: ok
    print("containers-dict", len(d))
    # an argument appended to the list
    xs: list[int64 | uint64] = []
    xs.append(int64(6))  # tpyc: ok
    print("containers-append", isinstance(xs[0], int64))
    # membership needle: a uint32 compared as the int64 member
    c = uint32(6)
    print("containers-in", c in xs)  # tpyc: ok
    # membership through `__iter__`: the needle binds as the int64 member
    seq = Seq()
    print("containers-iter-in", c in seq)  # tpyc: ok
    # a tuple element of a list literal lands at its element path
    ts: list[tuple[int32 | int64, int32]] = [(int64(5), 1)]  # tpyc: ok
    for x, _ in ts:
        print("containers-tuple-elem", isinstance(x, int64))
    # ... and of a comprehension's element (a dict literal's tuple value is
    # BUGS.md#value-union-literal-and-comprehension-shapes-reject)
    cs: list[tuple[int32 | int64, int32]] = [(int64(5), i) for i in range(1)]  # tpyc: ok
    for x, _ in cs:
        print("containers-comp-elem", isinstance(x, int64))
    # a widened uint32 tuple element lands in int64 inside a container
    c32 = uint32(7)
    ws: list[tuple[int64 | uint64, str]] = [(c32, "a")]  # tpyc: ok
    for w, _ in ws:
        print("containers-tuple-widen", isinstance(w, uint64))
    # a recursive alias's nested literal: its element lands in int16 (one
    # element, not a list of five)
    b: Tr = [5]  # tpyc: ok
    print("containers-alias-nested", tr_len(b))
    # ... and nested in a dict value beside None
    dj: dict[str, Js] = {"k": 1, "n": None, "items": [1, None, "x"]}  # tpyc: ok
    print("containers-alias-dict", js_len(dj["items"]))
