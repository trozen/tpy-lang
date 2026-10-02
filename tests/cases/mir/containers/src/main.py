# MIR pins for containers as places: element reads and writes, iterator loans,
# Span and dict views, container methods as structure / element writes, owned
# and borrowed container holders, container conflicts each beside a safe
# sibling, and the kept refusals. A conflict section's write never dangles at
# runtime: a flag the caller passes False, data the run never produces, or an
# aliasing MIR assumes that the call does not set up.
from tpy import int32, StrView, Span, Array, Own, readonly


class Point:
    # constructor: a scalar field
    def __init__(self, x: int32) -> None:  # tpyc: mir(covered)
        self.x = x


class Bag:
    items: list[int32]

    # constructor: an owned container field member-initialized from a literal
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.items = []  # tpyc: mir_owned(self.items)

    # method: a stub method call writes the field's structure
    def push(self, v: int32) -> None:  # tpyc: mir(covered)
        self.items.append(v)  # tpyc: mir_write(self.items[structure])

    # method: an iterator over a field place
    def total(self) -> int32:  # tpyc: mir(covered)
        t = 0
        for v in self.items:
            t += v
        return t

    # method: a Span parameter read beside the receiver's field
    def plus_span(self, s: Span[int32]) -> int32:  # tpyc: mir(covered)
        t = len(self.items)
        for v in s:
            t += v
        return t

    # method: the field returned as a borrowed result (a method publishes no summary,
    # so `b.view_items()` is opaque to its callers)
    def view_items(self) -> list[int32]:  # tpyc: mir(covered)
        return self.items


class Index:
    counts: dict[str, int32]

    # constructor: a dict field member-initialized from a literal
    def __init__(self) -> None:  # tpyc: mir(covered)
        self.counts = {}  # tpyc: mir_owned(self.counts)

    # method: a dict subscript write replaces elements in place
    def bump(self, k: str, v: int32) -> None:  # tpyc: mir(covered)
        self.counts[k] = v  # tpyc: mir_write(self.counts[elements])


class Pts:
    ps: list[Point]

    # constructor: a container of records -- its record elements are built from constants,
    # which an entry member initializer (operands are parameters) cannot express yet
    def __init__(self) -> None:  # tpyc: mir(uncovered /^constructor initializer needs parameter or literal$/)
        self.ps = [Point(1), Point(2)]

    # method: an element borrow of a field, grown through a local alias of that field
    # (BUGS.md#self-field-alias-growth-under-element-borrow)
    def alias_grow(self, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
        p = self.ps[0]  # tpyc: mir_borrows(p, self.ps[elements])
        x = self.ps
        if flag:
            x.append(Point(7))  # tpyc: mir_write(x[structure])
        return p.x

    # method: safe sibling -- the element holder is dead at the growth; the growth through
    # the alias is read back through the field
    def alias_grow_after_use(self) -> int32:  # tpyc: mir(covered)
        p = self.ps[0]
        v = p.x
        x = self.ps
        x.append(Point(8))
        return v + len(self.ps)


# free function: borrowed list parameter, iterator loan
def total(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    t = 0
    for x in xs:
        t += x
    return t


# free function: element borrow of a record element
def first(ps: list[Point]) -> int32:  # tpyc: mir(covered)
    p = ps[0]  # tpyc: mir_borrows(p, ps[elements])
    return p.x


# free function: a structural write through a stub method, published in the summary
def grow(ps: list[Point]) -> None:  # tpyc: mir(covered) mir_summary(known)
    ps.append(Point(1))  # tpyc: mir_write(ps[structure])


# free function: the summary's write is flow-insensitive (the flag does not narrow it)
def grow_if(ps: list[Point], flag: bool) -> None:  # tpyc: mir(covered) mir_summary(known)
    if flag:
        ps.append(Point(9))


# free function: an element holder live across a structure write
def kept(ps: list[Point], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    p = ps[0]
    if flag:
        ps.append(Point(2))  # tpyc: warning(/Mutation of 'ps'/)
    return p.x


# free function: safe sibling -- the holder is dead before the append; sema still warns,
# its loan lasting to the end of the holder's scope (BUGS.md#borrow-warning-not-last-use-aware)
def kept_after_use(ps: list[Point]) -> int32:  # tpyc: mir(covered)
    p = ps[0]
    v = p.x
    ps.append(Point(2))  # tpyc: warning(/Mutation of 'ps'/)
    return v


# free function: an iterator live across a structure write
def iterate_and_grow(xs: list[int32]) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for x in xs:
        if x < 0:
            xs.append(x)  # tpyc: warning(/Mutation of 'xs'/)
        t += x
    return t


# free function: safe sibling -- the append follows the loop
def iterate_then_grow(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    t = 0
    for x in xs:
        t += x
    xs.append(t)  # tpyc: mir_write(xs[structure])
    return t


# free function: the structure write arrives through a callee's summary
def forwarded(ps: list[Point], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    p = ps[0]
    grow_if(ps, flag)  # tpyc: warning(/borrowed container/)
    return p.x


# free function: safe sibling -- the holder is dead before the callee runs; sema still
# warns (BUGS.md#borrow-warning-not-last-use-aware)
def forwarded_after_use(ps: list[Point], flag: bool) -> int32:  # tpyc: mir(covered)
    p = ps[0]
    v = p.x
    grow_if(ps, flag)  # tpyc: warning(/borrowed container/)
    return v


# free function: an element replacement under a live element borrow -- sema says ok
# (BUGS.md#setitem-write-under-live-element-borrow)
def replace_elem(ps: list[Point], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    p = ps[0]
    if flag:
        ps[0] = Point(3)  # tpyc: mir_write(ps[elements])
    return p.x


# free function: safe sibling -- the holder is dead at the element write
def replace_after_use(ps: list[Point]) -> int32:  # tpyc: mir(covered)
    p = ps[0]
    x = p.x
    ps[0] = Point(4)
    return x


# free function: an augmented write of a str element, then a str parameter read -- the
# parameter may view the very element the write replaces (the conservative
# external-alias class: distinct parameters may alias)
def str_elem_aug(ys: list[str], k: str) -> int:  # tpyc: mir(conflict /replacement/)
    ys[0] += "a"  # tpyc: mir_write(ys[elements])
    return len(k)


# free function: safe sibling -- the parameter is read before the element write
def str_elem_aug_after_use(ys: list[str], k: str) -> int:  # tpyc: mir(covered)
    n = len(k)
    ys[0] += "a"  # tpyc: mir_write(ys[elements])
    return n


# free function: safe sibling -- an augmented write of an int element, no view live
def int_elem_aug(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    xs[0] += 1  # tpyc: mir_write(xs[elements])
    return xs[0]


# free function: a dict subscript write under a live loop over its values
def dict_write_in_loop(d: dict[str, int32], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in d.values():
        if flag:
            d["x"] = v  # tpyc: mir_write(d[elements])
        t += v
    return t


# free function: safe sibling -- the write follows the loop
def dict_write_after_loop(d: dict[str, int32]) -> int32:  # tpyc: mir(covered)
    t = 0
    for v in d.values():
        t += v
    d["x"] = t  # tpyc: mir_write(d[elements])
    return t


# free function: an iterator over an alias, the container grown through the other name
# (BUGS.md#iter-loan-keyed-by-alias-name)
def alias_iter_grow(xs: list[int32]) -> int32:  # tpyc: mir(conflict /replacement/)
    ys = xs
    t = 0
    for v in ys:
        if v < 0:
            xs.append(v)  # tpyc: mir_write(xs[structure])
        t += v
    return t


# free function: safe sibling -- the growth follows the loop
def alias_iter_then_grow(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    ys = xs
    t = 0
    for v in ys:
        t += v
    xs.append(t)
    return t


def grow_ints(ys: list[int32], flag: bool) -> None:  # tpyc: mir(covered) mir_summary(known)
    if flag:
        ys.append(7)


# free function: a field handed to a mutating callee while it is iterated
# (BUGS.md#mutating-callee-non-name-arg-unchecked)
def field_iter_callee(r: Bag, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in r.items:
        grow_ints(r.items, flag)
        t += v
    return t


# free function: safe sibling -- the callee runs after the loop
def field_callee_after_iter(r: Bag) -> int32:  # tpyc: mir(covered)
    t = 0
    for v in r.items:
        t += v
    grow_ints(r.items, False)
    return t


# free function: one parameter iterated while another is grown -- the two may name one
# list, so MIR reports it by the external-alias rule even when the caller passes two
# (BUGS.md#aliased-args-iterated-param-grown)
def copy_into(src: list[int32], out: list[int32]) -> None:  # tpyc: mir(conflict /replacement/)
    for v in src:
        out.append(v)


# free function: safe sibling -- the iterator is dead before the growth
def copy_count(src: list[int32], out: list[int32]) -> None:  # tpyc: mir(covered)
    n = 0
    for v in src:
        n += v
    out.append(n)


# free function: an explicit str view taken off the loop variable outlives the loop and
# the iterable is cleared before the view is read (sema copies the inferred form under
# its one view rule; the explicit StrView borrows, and MIR reports the clear)
def last_row(rows: list[str], flag: bool) -> int:  # tpyc: mir(conflict /replacement/)
    t: StrView = ""
    for d in rows:
        t = d
    if flag:
        rows.clear()  # tpyc: mir_write(rows[structure])
    return len(t)


# free function: safe sibling -- the view is read before the clear
def last_row_read_first(rows: list[str]) -> int:  # tpyc: mir(covered)
    t: StrView = ""
    for d in rows:
        t = d
    n = len(t)
    rows.clear()
    return n


# free function: owned container local, Span slice, len
def own() -> int32:  # tpyc: mir(covered)
    xs = [1, 2, 3]  # tpyc: mir_owned(xs)
    s = xs[1:3]  # tpyc: mir_borrows(s, xs[elements])
    return s[0] + len(xs)


# free function: a Span result summarized by its parameter origin (readonly: a mutable
# Span of a const-deduced list is ill-formed C++, BUGS.md#readonly-list-slice-span-mutable)
def tail(xs: list[int32]) -> Span[readonly[int32]]:  # tpyc: mir(covered) mir_summary(known)
    return xs[1:]


# free function: a Span parameter element read
def span_first(s: Span[int32]) -> int32:  # tpyc: mir(covered)
    return s[0]


# free function: an iterator over a Span parameter
def span_sum(s: Span[int32]) -> int32:  # tpyc: mir(covered)
    t = 0
    for v in s:
        t += v
    return t


# free function: a write through a mutable Span of a list parameter
def span_write(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    s = xs[0:2]
    s[0] = 1  # tpyc: mir_write(s[elements])
    return s[0]


# free function: an element write through a Span under a live loop over the same list
# (its safe sibling is `span_write` above, with no loop live). Nothing dangles: the
# verdict is the conservative one, element identity is not tracked, so a write of one
# element is a write of the element the iterator stands on.
def span_write_in_loop(xs: list[int32], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    s = xs[0:2]
    t = 0
    for v in xs:
        if flag:
            s[0] = v  # tpyc: mir_write(s[elements])
        t += v
    return t


# free function: an Array element write -- the Array `__setitem__` stub does not declare
# that it preserves references, so the write is the stub call, a structure write
def arr_write(a: Array[int32, 3]) -> int32:  # tpyc: mir(covered)
    a[1] = 9  # tpyc: mir_write(a[structure])
    return a[1]


# free function: an owned result moved out
def make(n: int32) -> Own[list[int32]]:  # tpyc: mir(covered)
    xs = [n]
    xs.append(n)
    return xs


# free function: a field container returned as a borrowed result
def items_of(b: Bag) -> list[int32]:  # tpyc: mir(covered) mir_summary(known)
    return b.items


# free function: dict with str keys -- the key read is a view of the elements
def count_keys(d: dict[str, int32]) -> int32:  # tpyc: mir(covered)
    n = 0
    for k in d:
        n += d[k]  # tpyc: mir_borrows(k, d[elements])
    return n


# free function: a dict keys view iterated -- the key is a view of the dict's elements
# (a dict view bound to a local does not lower yet: BUGS.md#dict-view-local-binding-rejected)
def keys_view(d: dict[str, int32]) -> int:  # tpyc: mir(covered)
    n = 0
    for k in d.keys():
        n += len(k)  # tpyc: mir_borrows(k, d[elements])
    return n


# free function: a dict values view measured
def values_len(d: dict[str, int32]) -> int:  # tpyc: mir(covered)
    return len(d.values())


# free function: a dict items view measured, not iterated
def items_len(d: dict[str, int32]) -> int:  # tpyc: mir(covered)
    return len(d.items())


# free function: iterating the items view yields (key, value) tuples, and a tuple is not
# a container member kind, so the loop has no native iteration facts
def items_iter(d: dict[str, int32]) -> int32:  # tpyc: mir(uncovered /^missing or invalid native iteration facts$/)
    n = 0
    for k, v in d.items():
        n += v
    return n


# free function: a set iterated
def set_total(s: set[int32]) -> int32:  # tpyc: mir(covered)
    t = 0
    for v in s:
        t += v
    return t


# free function: an Array element read
def arr_first(a: Array[int32, 3]) -> int32:  # tpyc: mir(covered)
    return a[0]


# free function: kept refusal -- a container of views holds a borrow (never called:
# a list[StrView] literal is itself a codegen reject)
def holds(vs: list[StrView]) -> int32:  # tpyc: mir(uncovered /^container holds a borrow$/)
    return len(vs)


# comprehension: a frame, kept refused
def comp(xs: list[int32]) -> Own[list[int32]]:  # tpyc: mir(uncovered /^unsupported expression$/)
    return [x + 1 for x in xs]


# free function: kept refusal -- extend takes an Iterable protocol parameter
def extend_it(xs: list[int32], ys: list[int32]) -> None:  # tpyc: mir(uncovered /^stub declares no contract$/)
    xs.extend(ys)


# free function: kept refusal -- a container element that is itself a container
def nested(xss: list[list[int32]]) -> int32:  # tpyc: mir(uncovered /^unsupported native container element$/)
    return xss[0][0]


# a caller's construct of a record with a container member is the constructor body's own
def main() -> None:  # tpyc: mir(uncovered /^constructor container field$/)
    b = Bag()
    b.push(1)
    b.push(2)
    print("Bag.push:", len(b.items))
    print("Bag.total:", b.total())
    print("Bag.plus_span:", b.plus_span([3, 4]))
    print("Bag.view_items:", len(b.view_items()))
    # the borrowed result aliases the field: a growth through it is read back through b
    bi = items_of(b)
    bi.append(5)
    print("items_of:", bi[0], len(b.items))
    ix = Index()
    ix.bump("a", 5)
    print("Index.bump:", ix.counts["a"])
    pts = Pts()
    print("Pts.alias_grow:", pts.alias_grow(False))
    print("Pts.alias_grow_after_use:", pts.alias_grow_after_use())
    ps = [Point(5), Point(6)]
    print("first:", first(ps))
    grow(ps)
    print("grow:", len(ps))
    grow_if(ps, True)
    print("grow_if:", len(ps))
    print("kept:", kept(ps, False))
    print("kept_after_use:", kept_after_use(ps), len(ps))
    print("forwarded:", forwarded(ps, False))
    print("forwarded_after_use:", forwarded_after_use(ps, True), len(ps))
    print("replace_elem:", replace_elem(ps, False))
    print("replace_after_use:", replace_after_use(ps), ps[0].x)
    ws = ["q"]
    print("str_elem_aug:", str_elem_aug(ws, "kk"), ws[0])
    print("str_elem_aug_after_use:", str_elem_aug_after_use(ws, "kk"), ws[0])
    ns = [4, 5]
    print("int_elem_aug:", int_elem_aug(ns), ns[0])
    d = {"a": 1, "bb": 2}
    print("dict_write_in_loop:", dict_write_in_loop(d, False))
    dc = {"x": 1, "y": 2}
    print("dict_write_after_loop:", dict_write_after_loop(dc), len(dc))
    xs: list[int32] = [1, 2, 3]
    print("total:", total(xs))
    print("alias_iter_grow:", alias_iter_grow(xs))
    print("alias_iter_then_grow:", alias_iter_then_grow(xs), len(xs))
    print("iterate_then_grow:", iterate_then_grow(xs), len(xs))
    r = Bag()
    r.push(4)
    grow_ints(r.items, True)
    print("grow_ints:", len(r.items))
    print("field_iter_callee:", field_iter_callee(r, False))
    print("field_callee_after_iter:", field_callee_after_iter(r), len(r.items))
    out: list[int32] = []
    copy_into(xs, out)
    print("copy_into:", len(out), out[0])
    copy_count(xs, out)
    print("copy_count:", len(out), out[1])
    rows = ["alpha", "beta"]
    print("last_row:", last_row(rows, False))
    print("last_row_read_first:", last_row_read_first(rows), len(rows))
    print("own:", own())
    print("span_write_in_loop:", span_write_in_loop(xs, False))
    # TPy slices a list into a zero-copy Span where CPython copies, so `scratch` after the
    # write would print differently under the two (the Span stub twin of that rule is
    # BUGS.md#cpy-span-stub-copies-slice).
    scratch = [0, 0]
    print("span_write:", span_write(scratch))
    arr: Array[int32, 3] = [4, 5, 6]
    print("arr_write:", arr_write(arr))
    print("arr_first:", arr_first(arr))
    m = make(7)
    print("make:", m[1], len(m))
    print("count_keys:", count_keys(d))
    print("keys_view:", keys_view(d))
    print("values_len:", values_len(d))
    print("items_len:", items_len(d))
    print("items_iter:", items_iter(d))
    st = {1, 2, 3}
    print("set_total:", set_total(st))
    t = tail(xs)
    sp = xs[1:]
    sp2 = xs[0:2]
    # a correct warning: the Spans t, sp and sp2 over xs are read after the call, and the
    # callee may grow xs
    print("iterate_and_grow:", iterate_and_grow(xs))  # tpyc: warning(/borrowed container .xs./)
    print("tail:", t[0])
    print("span_first:", span_first(sp))
    print("span_sum:", span_sum(sp2))
    c = comp(xs)
    print("comp:", c[0])
    xss = [[8]]
    print("nested:", nested(xss))
    ys = [9]
    # a spurious warning: no Span over xs is read again, but sema's loans last to the end
    # of the holders' scope (BUGS.md#borrow-warning-not-last-use-aware)
    extend_it(xs, ys)  # tpyc: warning(/borrowed container .xs./)
    print("extend_it:", len(xs))


main()
