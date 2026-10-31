# MIR over nested container elements: a list of lists, a dict of lists, an Array of lists, a list
# of records holding a list, views over nested members, a nested @nocopy element, a recursive type.
# Conflict sections are defined but not called: each one diverges from CPython at run time.
from __future__ import annotations
from typing import Iterator
from tpy import Array, Span, copy, int32, nocopy


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]


class Node:
    kids: list[Node]

    # recursive type: a record reaching itself through its list member is refused by name
    def __init__(self) -> None:  # tpyc: mir(uncovered /recursive container element/)
        self.kids = []


class Twin:
    a: list[int32]
    b: list[int32]

    def __init__(self) -> None:
        self.a = [1]
        self.b = [2]


# mutually recursive types: each record reaches the other through its list member
class A:
    bs: list[B]

    def __init__(self) -> None:  # tpyc: mir(uncovered /recursive container element/)
        self.bs = []


class B:
    as_: list[A]

    def __init__(self) -> None:  # tpyc: mir(uncovered /recursive container element/)
        self.as_ = []


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# conflict, free function: an alias of an element is walked while the element grows (sema is silent)
def silent_alias(rows: list[list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    r = rows[0]
    for v in r:
        # MIR's conflict is evidence only (BUGS.md#mir-element-alias-hazard-evidence-only): no warning
        rows[0].append(v)  # tpyc: ok


# conflict, free function: a record element is replaced while its list is walked (sema is silent)
def silent_bag(bags: list[Bag]) -> None:  # tpyc: mir(conflict /replacement/)
    b0 = bags[0]
    for v in b0.items:
        # MIR's conflict is evidence only (BUGS.md#mir-element-alias-hazard-evidence-only): no warning
        bags[0] = Bag()  # tpyc: ok


# conflict, free function: an inner loop walks an element another element's growth may be (sema is silent)
def silent_nest(rows: list[list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for r in rows:
        for v in r:
            # MIR's conflict is evidence only (BUGS.md#mir-element-alias-hazard-evidence-only): no warning
            rows[0].append(v)  # tpyc: ok


# conflict, free function: the outer list grows under a walk of its element
def outer_grow(rows: list[list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in rows[0]:
        rows.append([v])  # tpyc: warning(/Mutation of 'rows' while iterating/) warning(/iteration over 'rows\[0\]' still walks them.*copy\(rows\[0\]\)/)


# conflict, free function: a dict value grows under its own walk
def dict_rows(t: dict[str, list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in t["a"]:
        t["a"].append(v)  # tpyc: warning(/Mutation of 't\[...\]' while iterating/)


# conflict, element store: storing a new list into the walked element replaces its storage
def store_elem(rows: list[list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in rows[0]:
        rows[0] = [v]  # tpyc: warning(/Mutation of 'rows\[...\]' while iterating/)


# conflict, dict element store: storing a new list under the walked key replaces its storage
def store_value(t: dict[str, list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in t["a"]:
        t["a"] = [v]  # tpyc: warning(/Mutation of 't\[...\]' while iterating/)


def grow_outer(rows: list[list[int32]], row: list[int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    rows.append([1])
    return len(row)


# clean, type-based exclusion: no element of `rows` can be `names`, so growing `rows` spares it
def keep(rows: list[list[int32]], names: list[str]) -> int32:  # tpyc: mir(covered)
    rows.append([1])
    return len(names)


# conflict, aliasing argument: the callee grows `rows` while it still reads `row`, an element of it
def alias_call(rows: list[list[int32]]) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return grow_outer(rows, rows[0])  # tpyc: warning(/argument 'rows\[0\]' points into 'rows'/)


def grow_rows(rows: list[list[int32]]) -> None:  # tpyc: mir(covered) mir_summary(known)
    for row in rows:
        row.append(4)


# conflict, summary caller: the callee writes `param0[elements][structure]` under the inner walk
def caller_inner(rows: list[list[int32]]) -> int32:  # tpyc: mir(conflict /replacement/)
    n = 0
    for row in rows:
        for x in row:
            grow_rows(rows)  # tpyc: warning(/Passing borrowed container 'rows'/)
            n += x
    return n


# conservative, free function: distinct keys conflict, since an element place carries no key;
# MIR records the conflict as evidence only and prints nothing
def distinct_keys(t: dict[str, list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in t["a"]:
        t["b"].append(v)  # tpyc: ok


# conservative, free function: distinct rows conflict, since an element place carries no index;
# MIR records the conflict as evidence only and prints nothing
def distinct_rows(rows: list[list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in rows[0]:
        rows[1].append(v)  # tpyc: ok


# clean, free function: growing an element leaves the outer walk alone
def clean_outer(rows: list[list[int32]]) -> None:  # tpyc: mir(covered)
    for row in rows:
        rows[0].append(9)  # tpyc: ok


# clean, free function: growing a value leaves the key walk alone
def clean_keys(t: dict[str, list[int32]]) -> None:  # tpyc: mir(covered)
    for k in t:
        t[k].append(1)  # tpyc: ok


# in-place element write under its walk: evidence, as for a flat list, with no warning
def set_inner(rows: list[list[int32]]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in rows[0]:
        rows[0][0] = v + 1  # tpyc: ok


# loop over a list of lists, binding each inner list
def walk_rows(xs: list[list[int32]]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    n = 0
    for row in xs:
        n += len(row)
    return n


# loop over the keys of a dict of lists
def walk_keys(d: dict[str, list[int32]]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    n = 0
    for k in d:
        n += len(d[k])
    return n


# an Array of lists: each element grows in place
def array_rows(a: Array[list[int32], 2]) -> int32:  # tpyc: mir(covered)
    for row in a:
        row.append(3)
    return len(a[0])


# a list of records holding a list: each record's list grows
def bag_grow(bags: list[Bag]) -> int32:  # tpyc: mir(covered)
    n = 0
    for b in bags:
        b.items.append(2)
        n += len(b.items)
    return n


# two sibling fields of one list type in a record element: no recursion, both modeled
def twin_grow(ts: list[Twin]) -> int32:  # tpyc: mir(covered)
    for t in ts:
        t.a.append(3)
        t.b.append(4)
    return len(ts[0].a) + len(ts[0].b)


# container views over nested members: a Span and a dict's values view
def span_rows(s: Span[list[int32]]) -> int32:  # tpyc: mir(covered)
    n = 0
    for row in s:
        n += len(row)
    return n


def value_rows(d: dict[str, list[int32]]) -> int32:  # tpyc: mir(covered)
    n = 0
    for row in d.values():
        n += len(row)
    return n


# a nested @nocopy element, read in place
def toks(ts: list[list[Tok]]) -> int32:  # tpyc: mir(covered)
    return ts[0][0].n


# summary caller, clean: growing every element moves none of them
def caller_outer(rows: list[list[int32]]) -> int32:  # tpyc: mir(covered)
    n = 0
    for row in rows:
        grow_rows(rows)  # tpyc: warning(/Passing borrowed container 'rows'/)
        n += len(row)
    return n


# nested container literal: an inner list literal is a member of the outer one
def literal_rows() -> int32:  # tpyc: mir(covered)
    # spelled: the bare literal infers an Array of lists
    rows: list[list[int32]] = [[1], [2, 3]]
    rows[0].append(4)
    return len(rows[0]) + len(rows[1])


# an element as an aggregate source: copied, and lent to a callee that grows it
def element_sources(rows: list[list[int32]]) -> int32:  # tpyc: mir(covered)
    kept = copy(rows[0])
    grow_one(rows[0])
    return len(kept) * 10 + len(rows[0])


def grow_one(row: list[int32]) -> None:
    row.append(6)


class Grid:
    rows: list[list[int32]]

    # constructor: a list-of-lists field walked, each row grown
    def __init__(self, rows: list[list[int32]]) -> None:  # tpyc: mir(covered)
        self.rows = rows  # tpyc: warning(/copies list\[list\[int32\]\] into field/)
        for row in self.rows:
            row.append(0)

    # method: the same walk over the field
    def widen(self) -> None:  # tpyc: mir(covered)
        for row in self.rows:
            row.append(5)


# generator: a nested loop source walked before the one suspension
def gen_total(rows: list[list[int32]]) -> Iterator[int32]:  # tpyc: mir(covered)
    n = 0
    for row in rows:
        n += len(row)
    yield n


def pick(rows: list[list[int32]]) -> list[int32]:  # tpyc: mir(covered) mir_summary(known)
    row = rows[0]
    return row


# conflict, summary caller: an inner list returned by a callee is freed by the element store
def picked(rows: list[list[int32]]) -> int32:  # tpyc: mir(conflict /replacement/)
    row = pick(rows)
    # evidence only, so nothing warns: BUGS.md#element-alias-sees-element-assignment, BUGS.md#mir-element-alias-hazard-evidence-only
    rows[0] = [8, 9]  # tpyc: ok
    return len(row)


# summary: an element two region steps deep is no return origin a whole-parameter summary spells, so it is opaque
def deep_row(cube: list[list[list[int32]]]) -> list[int32]:  # tpyc: mir_summary(opaque /unsupported return origin type or access/)
    row = cube[0][0]
    return row


def first(bags: list[Bag]) -> Bag:  # tpyc: mir(covered) mir_summary(known)
    b = bags[0]
    return b


# conflict, summary caller: a record element returned by a callee is freed by the element store
def first_kept(bags: list[Bag]) -> int32:  # tpyc: mir(conflict /replacement/)
    b = first(bags)
    # evidence only, so nothing warns: BUGS.md#element-alias-sees-element-assignment, BUGS.md#mir-element-alias-hazard-evidence-only
    bags[0] = Bag()  # tpyc: ok
    return len(b.items)


def same_rows(rows: list[list[int32]]) -> list[list[int32]]:  # tpyc: mir(covered) mir_summary(known)
    return rows


# clean, summary caller: a returned whole container is not one of its elements, so an element store frees nothing it holds
def same_kept(rows: list[list[int32]]) -> int32:  # tpyc: mir(covered)
    all_rows = same_rows(rows)
    rows[0] = [8, 9]  # tpyc: ok
    return len(all_rows)


def value_of(t: dict[str, list[int32]]) -> list[int32]:  # tpyc: mir(covered) mir_summary(known)
    row = t["a"]
    return row


# conflict, summary caller: a dict value returned by a callee is freed by the store under its key
def value_kept(t: dict[str, list[int32]]) -> int32:  # tpyc: mir(conflict /replacement/)
    row = value_of(t)
    # evidence only, so nothing warns: BUGS.md#mir-element-alias-hazard-evidence-only
    t["a"] = [8, 9]  # tpyc: ok
    return len(row)


def main() -> None:
    # spelled: Span[list[int32]](rows) below needs the list typed
    rows: list[list[int32]] = [[1], [2]]
    t: dict[str, list[int32]] = {"a": [1], "b": [2, 3]}
    distinct_keys(t)
    distinct_rows(rows)
    print("conservative", rows, t)
    clean_outer(rows)
    clean_keys(t)
    set_inner(rows)
    print("clean", rows, t)
    print("walk", walk_rows(rows), walk_keys(t))
    a: Array[list[int32], 2] = [[1], [2]]
    print("array", array_rows(a), a[0], a[1])
    bags = [Bag(), Bag()]
    print("bags", bag_grow(bags), bags[0].items, bags[1].items)
    twins = [Twin()]
    print("twins", twin_grow(twins), twins[0].a, twins[0].b)
    print("views", span_rows(Span[list[int32]](rows)), value_rows(t))
    row: list[Tok] = []
    row.append(Tok(7))
    ts: list[list[Tok]] = []
    ts.append(row)
    print("nocopy", toks(ts))
    print("summary", caller_outer(rows), rows)
    print("literal", literal_rows(), element_sources(rows), rows[0])
    g = Grid([[1]])
    g.widen()
    print("ctor", g.rows)
    print("gen", [n for n in gen_total(rows)])
    print("keep", keep([[1]], ["x", "y"]), len(pick(rows)))
    print("same_kept", same_kept(rows), rows, len(value_of(t)))


main()
