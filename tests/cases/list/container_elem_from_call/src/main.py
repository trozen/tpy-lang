# A CONTAINER-typed element that comes from a CALL, at a comprehension element
# slot and a container-literal element slot: the callee's OWNED result is
# stored (and moved, at a literal), never copied.
from typing import Iterator

from tpy import Array, Own, int32


def make_rows(n: int32) -> Own[list[str]]:
    out: list[str] = []
    out.append("r")
    for i in range(n):
        out.append("x")
    return out


def make_map(n: int32) -> Own[dict[str, int32]]:
    d: dict[str, int32] = {}
    d["k"] = n
    return d


def make_set(n: int32) -> Own[set[int32]]:
    return {n}


def make_ints(n: int32) -> Own[list[int32]]:
    return [n]


class Maker:
    tag: str
    rows: list[list[str]]
    grid: list[list[int32]]

    def __init__(self, tag: str, n: int32) -> None:
        self.tag = tag
        # constructor, member-init container literal.
        self.rows = [make_rows(1), make_rows(2)]  # tpyc: ok
        # constructor, member-init comprehension: a reject here fails the
        # whole constructor rather than demoting to the body.
        self.grid = [make_ints(i) for i in range(n)]  # tpyc: ok

    def row(self, n: int32) -> Own[list[str]]:
        out: list[str] = []
        out.append(self.tag)
        return out

    def built(self) -> int32:
        # method: the comprehension element is a method call on self.
        rs = [self.row(i) for i in [1, 2]]  # tpyc: ok
        rs[0].append("m")
        return len(rs[0])


def gen_lens() -> Iterator[int32]:
    # generator: the comprehension lowers inside the resumable frame.
    rs = [make_rows(i) for i in [1, 2]]  # tpyc: ok
    rs[0].append("m")
    yield len(rs[0])
    yield len(rs)


# module level: the same element at a global's initializer.
TOP = [make_rows(1), make_rows(2)]  # tpyc: ok


def main() -> None:
    # free function, comprehension element.
    rs = [make_rows(i) for i in [1, 2]]  # tpyc: ok
    rs[0].append("m")
    print("comp", len(rs), len(rs[0]))

    # ... and at an explicitly annotated `list[list[...]]` slot.
    ls: list[list[int32]] = [make_ints(i) for i in range(2)]  # tpyc: ok
    ls[0].append(9)
    print("annotated", len(ls), len(ls[0]))

    # free function, container-literal element. Unannotated, so the slot is a
    # fixed Array -- aggregate-init, which already moved.
    lit = [make_rows(1), make_rows(2)]  # tpyc: ok
    lit[0].append("m")
    print("literal", len(lit), len(lit[0]))

    # Every section stores the callee's OWNED result, then mutates it and
    # prints the new length: a read-only section would match CPython even if
    # the element had been silently copied.
    #
    # ... and at an annotated `list[list[str]]` slot, whose std::vector is the
    # one an initializer_list would have copied each element into (its
    # elements are const, so a brace-init copy-constructs each container).
    # The pinned render is the `::tpy::make_vector` helper, whose operands
    # are C++ CALL ARGUMENTS: their evaluation order is the C++ compiler's,
    # not Python's left-to-right -- a declared, postponed divergence
    # (BUGS.md#subexpression-right-to-left-eval), pinned here so a policy
    # change shows up as a snapshot diff, not as a silent one.
    lv: list[list[str]] = [make_rows(1), make_rows(2)]  # tpyc: ok
    lv[0].append("m")
    print("litvec", len(lv), len(lv[0]))

    # dict LITERAL, the VALUE slot: the same brace-init copy, via the
    # ordered_map initializer_list ctor.
    dl: dict[int32, list[str]] = {1: make_rows(1), 2: make_rows(2)}  # tpyc: ok
    dl[1].append("m")
    print("dictlit", len(dl), len(dl[1]))

    # dict comprehension, the VALUE slot.
    dv: dict[int32, list[str]] = {i: make_rows(i) for i in [1, 2]}  # tpyc: ok
    dv[1].append("m")
    print("dictcomp", len(dv), len(dv[1]))

    # a dict-returning and a set-returning callee at the same element slot.
    ms = [make_map(1), make_map(2)]  # tpyc: ok
    ms[0]["extra"] = 9
    print("dictelem", len(ms), len(ms[0]))
    ss = [make_set(1), make_set(2)]  # tpyc: ok
    ss[0].add(7)
    print("setelem", len(ss), len(ss[0]))

    # a fixed-size Array slot takes the same element.
    arr: Array[list[str], 2] = [make_rows(1), make_rows(2)]  # tpyc: ok
    arr[0].append("m")
    print("array", len(arr), len(arr[0]))

    m = Maker("t", 2)
    m.rows[0].append("m")
    m.grid[0].append(9)
    print("ctor", len(m.rows), len(m.rows[0]), len(m.grid), len(m.grid[0]))
    print("method", m.built())

    for v in gen_lens():
        print("gen", v)

    TOP[0].append("m")
    print("module", len(TOP), len(TOP[0]))


main()
