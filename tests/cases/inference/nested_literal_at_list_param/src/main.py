# An unannotated list literal reached through a name or a borrow (an element
# read, a dict value, a capture, an items() value, copy()) settles as a list
# where it meets a list slot, and the rows of one literal are one C++ type.
from __future__ import annotations
from typing import Iterator
from tpy import int32, readonly, Span, Own, copy, nocopy


class Row:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class Pin:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ns(xs: list[Row]) -> int32:
    return len(xs)


def grow(xs: list[Row]) -> None:
    xs.append(Row(9))


def ro(xs: readonly[list[Row]]) -> int32:
    return len(xs)


def total(xs: readonly[list[int32]]) -> int32:
    return len(xs)


def pairs(v: readonly[list[tuple[Rec, int32]]]) -> int32:
    return len(v)


def maybe(xs: list[Row] | None) -> int32:
    if xs is None:
        return 0
    xs.append(Row(8))
    return len(xs)


def sp(xs: Span[Row]) -> int32:
    return len(xs)


def pins(xs: list[Pin]) -> int32:
    return len(xs)


def keep(xs: Own[list[Row]]) -> Own[list[Row]]:
    return xs


def fd(m: dict[str, Row]) -> None:
    m["z"] = Row(3)


def fs(s: set[int32]) -> int32:
    return len(s)


def nsi(xs: list[int32]) -> int32:
    return len(xs)


class Filler:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def fill(self, xs: list[int32]) -> None:
        xs.append(3)
        self.n = len(xs)


class K:
    c: int32

    def __init__(self) -> None:
        self.c = 0

    def take(self, xs: list[Row]) -> None:
        xs.append(Row(4))
        self.c = len(xs)


def gen() -> Iterator[int32]:
    ll = [[Row(1)], [Row(2)]]
    # generator body: a method call takes the element in place
    K().take(ll[0])  # tpyc: ok
    yield len(ll[0])


def gen_rows() -> Iterator[list[Row]]:
    ll = [[Row(1)], [Row(2)]]
    # yield: the element is lent; the consumer's append shows here
    yield ll[0]  # tpyc: ok
    print("gen_rows_after", len(ll[0]))


def first() -> Own[list[Row]]:
    ll = [[Row(1)], [Row(2)]]
    # return: the element copies into the owned result
    return ll[0]  # tpyc: warning(/copies list\[Row\] into owned storage/)


def first_ro() -> Own[readonly[list[Row]]]:
    ll = [[Row(5)], [Row(6), Row(7)]]
    return ll[1]  # tpyc: warning(/copies list\[Row\] into owned storage/)


def first_opt() -> Own[list[Row]] | None:
    ll = [[Row(1)], [Row(2)]]
    return ll[0]  # tpyc: warning(/copies list\[Row\] into owned storage/)


def main() -> None:
    ll = [[Row(5)], [Row(6)]]
    # element read at a list parameter
    print("elem", ns(ll[0]))  # tpyc: ok
    # dict value at a list parameter
    d = {"k": [Row(7)]}
    print("dict", ns(d["k"]))  # tpyc: ok
    # a local bound to an element is the element: a write shows in ll
    x = ll[1]
    x.append(Row(42))
    print("bind", ns(x), len(ll[1]), ll[1][1].n)  # tpyc: ok
    # the callee appends through the parameter; the peer row is untouched
    g = [[Row(1)], [Row(2)]]
    grow(g[1])  # tpyc: ok
    print("grow", len(g[1]), g[1][1].n, len(g[0]))
    # loop variable and comprehension variable over the outer literal
    for r in ll:
        grow(r)  # tpyc: ok
    print("loop", len(ll[0]), len(ll[1]))
    print("comp", [ns(y) for y in ll])
    # readonly slot: an element, a plain name of records, a plain name of ints
    print("readonly", ro(ll[1]))  # tpyc: ok
    rs = [Row(1)]
    print("readonly_name", ro(rs))  # tpyc: ok
    bs = [1, 2]
    print("readonly_int", total(bs))  # tpyc: ok
    tl = [(Rec(1), 2)]
    print("readonly_tuples", pairs(tl))  # tpyc: ok
    # `| None` slot: an element and a plain name, both written through
    mm = [[Row(1)], [Row(2)]]
    print("optional", maybe(mm[0]), len(mm[0]))  # tpyc: ok
    on = [Row(1)]
    print("optional_name", maybe(on), len(on))  # tpyc: ok
    # Span slot: a view, so the rows stay fixed arrays
    sm = [[Row(1)], [Row(2)]]
    print("span", sp(sm[0]))
    # a dict value that is a dict, a list element that is a set
    dd = {"a": {"x": Row(1)}}
    fd(dd["a"])  # tpyc: ok
    print("dict_of_dict", len(dd["a"]))
    ss = [{1, 2}, {3}]
    print("list_of_set", fs(ss[0]))  # tpyc: ok
    # @nocopy rows: every row builds in place, none copies
    pp = [[Pin(1)], [Pin(2)]]
    print("nocopy", pins(pp[0]))  # tpyc: ok
    # rows at their last use move into the vectors
    r1 = Row(5)
    r2 = Row(6)
    mv = [[r1], [r2]]
    print("moved", ns(mv[1]))  # tpyc: ok
    # closure: a captured list passed inside the nested def
    s0 = [Row(1)]

    def inner() -> None:
        grow(s0)  # tpyc: ok

    inner()
    print("closure", len(s0))
    # dict.items() value at a list parameter, written through
    di = {"k": [Row(1)]}
    for _k, v in di.items():
        grow(v)  # tpyc: ok
    print("items", len(di["k"]))
    # copy(): a method takes the copy, the source is untouched
    s1 = [Row(1)]
    k = K()
    k.take(copy(s1))  # tpyc: ok
    print("copy", k.c, len(s1))
    # Own slot: the element copies, as its annotated twin does
    kept = keep(ll[0])  # tpyc: warning(/copies list\[Row\] into owned storage/)
    print("own", len(kept))
    # a named list stored as a row and used again is copied (warned), as its
    # annotated twin is; CPython aliases it
    a = [Row(1)]
    na = [a, [Row(2)]]  # tpyc: warning(/copies list\[Row\] into owned storage/)
    print("named_row", ns(na[0]), len(a))
    # the rows of a dict literal's values are one type, a named one copied
    b = [Row(1)]
    dv = {"a": b, "b": [Row(2)]}  # tpyc: warning(/copies list\[Row\] into owned storage/)
    print("dict_rows", ns(dv["b"]), len(b))
    # a mutating method on an element makes every row a list
    ap = [[Row(1)], [Row(2)]]
    ap[0].append(Row(9))  # tpyc: ok
    print("append_row", len(ap[0]), ns(ap[1]))
    # rows deeper down and rows inside tuple rows group by position
    deep = [[[Pin(5)]], [[Pin(6)]]]
    print("deep", pins(deep[0][0]))  # tpyc: ok
    tr = [([Pin(1)], "a"), ([Pin(2)], "b")]
    print("tuple_rows", pins(tr[1][0]))  # tpyc: ok
    # lists inside dicts written in a list, and a named dict as a row
    dl = [{"k": [Pin(1)]}, {"k": [Pin(2)]}]
    print("dicts_in_list", pins(dl[1]["k"]))  # tpyc: ok
    # a named dict stored as a row and used again is copied (warned), as a
    # named list is; CPython aliases it
    d1 = {"x": [Row(1)]}
    nd = [d1, {"x": [Row(2)]}]  # tpyc: warning(/copies dict\[str, list\[Row\]\] into owned storage/)
    grow(nd[1]["x"])
    print("named_dict_row", len(nd[1]["x"]), len(d1))
    # a nested row replaced by subscript store joins the rows it replaces
    rp = [[[Pin(1)], [Pin(2)]]]
    rp[0] = [[Pin(3)], [Pin(4)]]
    print("replaced_rows", pins(rp[0][1]))  # tpyc: ok
    # copy() of an int list at a method's mutated list parameter
    d7 = [7]
    f7 = Filler()
    f7.fill(copy(d7))  # tpyc: ok
    print("copy_int", f7.n, len(d7))
    # int rows: their element cells decide the rows
    li = [[5], [6]]
    print("int_rows", nsi(li[0]))
    for v2 in gen():
        print("generator", v2)
    for row in gen_rows():
        row.append(Row(3))
        print("gen_rows", len(row))
    print("return", len(first()), len(first_ro()))
    fo = first_opt()
    if fo is not None:
        print("return_optional", len(fo))


main()
