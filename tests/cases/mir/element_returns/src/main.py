# MIR over getters returning an element by reference: each summary is known and its callers lower.
# Live sections are defined but never called (they diverge from CPython at run time).
from tpy import Array, Span, StrView, auto_readonly, copy, int32, readonly


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Q:
    p: P

    def __init__(self) -> None:
        self.p = P(1)


# getters that write what they then return: the write precedes the result the caller holds
class G:
    items: list[P]
    grid: list[list[P]]
    q: Q

    def __init__(self) -> None:
        self.items = [P(1), P(2)]
        self.grid = [[P(1), P(2)]]
        self.q = Q()

    def first(self) -> P:  # tpyc: mir_summary(known)
        return self.items[0]

    def push_first(self) -> P:  # tpyc: mir_summary(known)
        self.items.append(P(0))
        return self.items[0]

    def push_tail(self) -> Span[P]:  # tpyc: mir_summary(known)
        self.items.append(P(0))
        return self.items[0:]

    def push_deep(self) -> P:  # tpyc: mir_summary(known)
        r = self.grid[0]
        r.append(P(0))
        return r[0]

    def reset_get(self) -> P:  # tpyc: mir_summary(known)
        self.q.p = P(5)
        return self.q.p

    def keep(self, a: P) -> P:  # tpyc: mir_summary(known)
        self.items.append(P(0))
        return a


class K:
    p: P

    def __init__(self) -> None:
        self.p = P(7)


# getter, free function: writes its list, then returns an element of it
def push_get(xs: list[P]) -> P:  # tpyc: mir_summary(known)
    xs.append(P(0))
    return xs[0]


# getter, free function: passes an argument back after growing its list
def pass_back(a: P, xs: list[P]) -> P:  # tpyc: mir_summary(known)
    xs.append(P(0))
    return a


class H:
    items: list[P]
    names: list[str]

    def __init__(self) -> None:
        self.items = [P(1), P(2)]
        self.names = ["a", "b"]

    # getter, method: an element of a list field
    def at(self, i: int32) -> P:  # tpyc: mir_summary(known)
        return self.items[i]

    # getter, method: a str element of a list field, as a view
    def name_at(self, i: int32) -> StrView:  # tpyc: mir_summary(known)
        return self.names[i]

    # getter, property
    @property
    def head(self) -> P:  # tpyc: mir_summary(known)
        return self.items[0]

    # getter, method: a region endpoint, a Span over the list field's elements
    def tail(self) -> Span[P]:  # tpyc: mir_summary(known)
        return self.items[1:]

    # getter, @auto_readonly: both bodies
    @auto_readonly
    def last(self) -> auto_readonly[P]:  # tpyc: mir_summary(known)
        return self.items[1]


# getter, free function: an element of a list parameter
def first(xs: list[P]) -> P:  # tpyc: mir_summary(known)
    return xs[0]


# getter, free function: the index is evaluated within the return's full expression
def nth(xs: list[P], i: int32) -> P:  # tpyc: mir_summary(known)
    return xs[i + 1]


# getter, free function: an owned-temporary index or key (a BigInt sum, a str concatenation),
# evaluated within the return's full expression
def big_nth(xs: list[P], i: int) -> P:  # tpyc: mir_summary(known)
    return xs[i + 1]


def keyed(d: dict[str, list[int32]], s: str) -> list[int32]:  # tpyc: mir_summary(known)
    return d[s + "a"]


# getter, free function: an element of a Span, of an Array
def span_first(s: Span[P]) -> P:  # tpyc: mir_summary(known)
    return s[0]


def array_first(a: Array[P, 2]) -> P:  # tpyc: mir_summary(known)
    return a[0]


# getter, free function: a nested element; an element of a Span over lists
def nested(rows: list[list[P]], i: int32, j: int32) -> P:  # tpyc: mir_summary(known)
    return rows[i][j]


def span_nested(s: Span[list[P]]) -> P:  # tpyc: mir_summary(known)
    return s[0][0]


# getter, free function: a container element two regions deep
def deep_row(cube: list[list[list[int32]]]) -> list[int32]:  # tpyc: mir_summary(known)
    return cube[0][0]


# getter, free function: a container element, of a list and of a dict
def prow(grid: list[list[P]], i: int32) -> list[P]:  # tpyc: mir_summary(known)
    return grid[i]


def row_of(rows: list[list[int32]], i: int32) -> list[int32]:  # tpyc: mir_summary(known)
    return rows[i]


def value_of(d: dict[str, list[int32]], k: str) -> list[int32]:  # tpyc: mir_summary(known)
    return d[k]


# getter, free function: a conditional over two elements of one list
def pick(xs: list[P], c: bool) -> P:  # tpyc: mir_summary(known)
    return xs[0] if c else xs[1]


# getter, free function: a tuple's record member
def tfirst(t: tuple[P, int32]) -> P:  # tpyc: mir_summary(known)
    return t[0]


# getter, free function: a str element as a view
def name_of(names: list[str], i: int32) -> StrView:  # tpyc: mir_summary(known)
    return names[i]


# getter, free function: an element bound to a local first, then returned; a whole Span returned
def local_first(xs: list[P]) -> P:  # tpyc: mir_summary(known)
    p = xs[0]
    return p


def local_row(rows: list[list[int32]], i: int32) -> list[int32]:  # tpyc: mir_summary(known)
    r = rows[i]
    return r


def local_value(d: dict[str, list[int32]], k: str) -> list[int32]:  # tpyc: mir_summary(known)
    v = d[k]
    return v


def whole_span(s: Span[P]) -> Span[P]:  # tpyc: mir_summary(known)
    return s


# live, free function: the list grows while the element is kept
def live_free() -> None:  # tpyc: mir(conflict /replacement/)
    xs = [P(1)]
    a = first(xs)  # tpyc: mir_borrows(a, xs[elements])
    xs.append(P(2))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'a' still refers to them/)
    print(a.x)


# live, method: the receiver's list grows (sema is silent)
def live_method(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    a = h.at(0)  # tpyc: mir_borrows(a, h.items[elements])
    h.items.append(P(3))  # tpyc: warning(/may move the elements of 'h.items', but 'a' still refers to them/)
    print(a.x)


# live, method: an element is replaced in place (sema is silent)
def live_replaced(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    a = h.at(0)  # tpyc: mir_borrows(a, h.items[elements])
    h.items[0] = P(7)  # tpyc: warning(/this assignment may overwrite what 'a' refers to in place/)
    print(a.x)


# live, property (sema is silent)
def live_property(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    a = h.head  # tpyc: mir_borrows(a, h.items[elements])
    h.items.clear()  # tpyc: warning(/may move the elements of 'h.items', but 'a' still refers to them/)
    print(a.x)


# live, @auto_readonly readonly clone: the getter is called through a readonly alias, the list grows
# through the mutable owner (sema is silent)
def as_readonly(h: readonly[H]) -> readonly[H]:  # tpyc: mir_summary(known)
    return h


def live_auto_readonly(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    ro = as_readonly(h)
    a = ro.last()  # tpyc: mir_borrows(a, h.items[elements])
    h.items.append(P(4))  # tpyc: warning(/may move the elements of 'h.items', but 'a' still refers to them/)
    print(a.x)


# live, @auto_readonly mutable clone (sema is silent)
def live_auto(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    a = h.last()  # tpyc: mir_borrows(a, h.items[elements])
    h.items.append(P(4))  # tpyc: warning(/may move the elements of 'h.items', but 'a' still refers to them/)
    print(a.x)


# live, free function: an index computed inside the getter
def live_nth() -> None:  # tpyc: mir(conflict /replacement/)
    xs = [P(1), P(2)]
    a = nth(xs, 0)  # tpyc: mir_borrows(a, xs[elements])
    xs.clear()  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'a' still refers to them/)
    print(a.x)


# live, owned-temporary index or key: through the getters, and bound directly
def live_big() -> None:  # tpyc: mir(conflict /replacement/)
    xs = [P(1), P(2)]
    a = big_nth(xs, 0)  # tpyc: mir_borrows(a, xs[elements])
    xs.append(P(3))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'a' still refers to them/)
    print(a.x)


def live_keyed() -> None:  # tpyc: mir(conflict /replacement/)
    d: dict[str, list[int32]] = {"a": [1]}
    v = keyed(d, "")  # tpyc: mir_borrows(v, d[elements])
    d.clear()  # tpyc: warning(/Mutation of 'd' while borrowed/) warning(/may move the elements of 'd', but 'v' still refers to them/)
    print(v[0])


def live_bind_big(xs: list[P], i: int) -> None:  # tpyc: mir(conflict /replacement/)
    p = xs[i + 1]  # tpyc: mir_borrows(p, xs[elements])
    xs.append(P(3))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'p' still refers to them/)
    print(p.x)


def live_bind_keyed(d: dict[str, list[int32]], s: str) -> None:  # tpyc: mir(conflict /replacement/)
    v = d[s + "a"]  # tpyc: mir_borrows(v, d[elements])
    d.clear()  # tpyc: warning(/Mutation of 'd' while borrowed/) warning(/may move the elements of 'd', but 'v' still refers to them/)
    print(v[0])


# live, Span: the list a Span argument views grows
def live_span(xs: list[P]) -> None:  # tpyc: mir(conflict /replacement/)
    s: Span[P] = xs[0:]
    a = span_first(s)  # tpyc: mir_borrows(a, xs[elements])
    xs.append(P(2))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'a' still refers to them/)
    print(a.x)


# live, Array: an element is replaced (as a `__setitem__` call), warned as a growth that "may move"
# the elements: BUGS.md#array-setitem-structure-write
def live_array(arr: Array[P, 2]) -> None:  # tpyc: mir(conflict /replacement/)
    a = array_first(arr)  # tpyc: mir_borrows(a, arr[elements])
    arr[0] = P(9)  # tpyc: warning(/may move the elements of 'arr', but 'a' still refers to them/)
    print(a.x)


# live, nested: the inner list grows (the one element 'a' is in)
def live_inner() -> None:  # tpyc: mir(conflict /replacement/)
    rows = [[P(1)]]
    a = nested(rows, 0, 0)  # tpyc: mir_borrows(a, rows[elements][elements])
    rows[0].append(P(2))  # tpyc: warning(/this call may move the element 'a' still refers to/)
    print(a.x)


# live, nested: an element of the inner list is replaced in place ('rows' is a parameter: over an
# unannotated literal the caller is uncovered, BUGS.md#nested-record-literal-element-store-silent)
def live_inner_store(rows: list[list[P]]) -> None:  # tpyc: mir(conflict /replacement/)
    a = nested(rows, 0, 0)  # tpyc: mir_borrows(a, rows[elements][elements])
    rows[0][0] = P(3)  # tpyc: warning(/this assignment may overwrite what 'a' refers to in place/)
    print(a.x)


# live, nested: the outer list is cleared, or grows (a moved inner list keeps its buffer: "may");
# 'rows' is a parameter as above (BUGS.md#nested-record-literal-element-store-silent)
def live_outer_clear(rows: list[list[P]]) -> None:  # tpyc: mir(conflict /replacement/)
    a = nested(rows, 0, 0)  # tpyc: mir_borrows(a, rows[elements][elements])
    rows.clear()  # tpyc: warning(/may move the elements of 'rows', but 'a' still refers to them/)
    print(a.x)


def live_outer_append(rows: list[list[P]], row: list[P]) -> None:  # tpyc: mir(conflict /replacement/)
    a = nested(rows, 0, 0)  # tpyc: mir_borrows(a, rows[elements][elements])
    rows.append(copy(row))  # tpyc: warning(/may move the elements of 'rows', but 'a' still refers to them/)
    print(a.x)


# live, nested Span: an element of an element through a Span argument, and read off a Span local
def live_span_nested() -> None:  # tpyc: mir(conflict /replacement/)
    # Annotated: a slice of an unannotated nested literal is refused (BUGS.md#span-param-slice-of-literal-list-unsupported).
    rows: list[list[P]] = [[P(1)]]
    sp: Span[list[P]] = rows[0:]
    a = span_nested(sp)  # tpyc: mir_borrows(a, rows[elements][elements])
    rows[0].append(P(2))  # tpyc: warning(/this call may move the element 'a' still refers to/)
    print(a.x)


def live_span_direct() -> None:  # tpyc: mir(conflict /replacement/)
    rows: list[list[P]] = [[P(1)]]
    sp: Span[list[P]] = rows[0:]
    a = sp[0][0]  # tpyc: mir_borrows(a, rows[elements][elements])
    rows[0].append(P(2))  # tpyc: warning(/this call may move the element 'a' still refers to/)
    print(a.x)


# live, container element two regions deep: the middle list is cleared
def live_deep(cube: list[list[list[int32]]]) -> None:  # tpyc: mir(conflict /replacement/)
    r = deep_row(cube)  # tpyc: mir_borrows(r, cube[elements][elements])
    cube[0].clear()  # tpyc: warning(/this call may move the element 'r' still refers to/)
    print(r[0])


# live, container result at an elements endpoint: 'r' holds the inner list as a direct
# `r = grid[0]` does, so growing it moves the element 'a' keeps
def live_row_call(grid: list[list[P]]) -> None:  # tpyc: mir(conflict /replacement/)
    r = prow(grid, 0)
    a = grid[0][0]
    r.append(P(9))  # tpyc: warning(/this call may move the element 'a' still refers to/)
    print(a.x)


# live, container element: of a list, of a dict
def live_row() -> None:  # tpyc: mir(conflict /replacement/)
    rows = [[1]]
    r = row_of(rows, 0)  # tpyc: mir_borrows(r, rows[elements])
    rows.clear()  # tpyc: warning(/Mutation of 'rows' while borrowed/) warning(/may move the elements of 'rows', but 'r' still refers to them/)
    print(r[0])


def live_value() -> None:  # tpyc: mir(conflict /replacement/)
    # Annotated: BUGS.md#dict-local-pending-list-value-arg-rejects.
    d: dict[str, list[int32]] = {"a": [1]}
    v = value_of(d, "a")  # tpyc: mir_borrows(v, d[elements])
    d.clear()  # tpyc: warning(/Mutation of 'd' while borrowed/) warning(/may move the elements of 'd', but 'v' still refers to them/)
    print(v[0])


# live, a dict view walked: an element of a value list is kept while that list grows
def live_values(ds: list[dict[str, list[P]]]) -> None:  # tpyc: mir(conflict /replacement/)
    d = ds[0]
    for row in d.values():
        a = row[0]  # tpyc: mir_borrows(a, ds[elements][elements][elements])
        ds[0]["a"].append(P(5))  # tpyc: warning(/this call may move the element 'a' still refers to/)
        print(a.x)


# live, conditional: a getter's over one list, and a binding's own
def live_pick(c: bool) -> None:  # tpyc: mir(conflict /replacement/)
    xs = [P(1), P(2)]
    a = pick(xs, c)  # tpyc: mir_borrows(a, xs[elements])
    xs.append(P(3))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'a' still refers to them/)
    print(a.x)


def live_alias(c: bool) -> None:  # tpyc: mir(conflict /replacement/)
    xs = [P(1), P(2)]
    r = xs[0] if c else xs[1]
    # The conditional's arms are blocks of their own: what 'r' holds is checked after the join.
    xs.append(P(3))  # tpyc: mir_borrows(r, xs[elements]) warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'r' still refers to them/)
    print(r.x)


# live, tuple: the tuple's record member lies in a list that grows
def live_tuple() -> None:  # tpyc: mir(conflict /replacement/)
    xs = [P(1)]
    t = (xs[0], 1)
    a = tfirst(t)  # tpyc: mir_borrows(a, xs[elements])
    xs.append(P(2))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 'a' still refers to them/)
    print(a.x)


# live, region endpoint: a Span a getter returns, bound unannotated and annotated, read after the
# list grows
def live_tail(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    s = h.tail()  # tpyc: mir_borrows(s, h.items[elements])
    h.items.append(P(3))  # tpyc: warning(/may move the elements of 'h.items', but 's' still views them/)
    print(s[0].x)


def live_tail_annotated(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    s: Span[P] = h.tail()  # tpyc: mir_borrows(s, h.items[elements])
    h.items.append(P(3))  # tpyc: warning(/may move the elements of 'h.items', but 's' still views them/)
    print(s[0].x)


# live, region endpoint: an element of the viewed list replaced through its owner
def live_tail_replaced(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    s = h.tail()
    h.items[1] = P(9)  # tpyc: warning(/this assignment may overwrite what 's' refers to in place/)
    print(s[0].x)


# live, region endpoint at a readonly view type: bound, rebound, and returned by a getter
def as_readonly_tail(h: H) -> Span[readonly[P]]:  # tpyc: mir_summary(known)
    return h.tail()


def live_ro_tail(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    s: Span[readonly[P]] = h.tail()  # tpyc: mir_borrows(s, h.items[elements])
    h.items.append(P(3))  # tpyc: warning(/may move the elements of 'h.items', but 's' still views them/)
    print(s[0].x)


def live_ro_rebind(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    s: Span[readonly[P]] = h.items[0:]
    s = h.tail()  # tpyc: mir_borrows(s, h.items[elements])
    h.items.append(P(3))  # tpyc: warning(/may move the elements of 'h.items', but 's' still views them/)
    print(s[0].x)


def live_ro_returned(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    s = as_readonly_tail(h)  # tpyc: mir_borrows(s, h.items[elements])
    h.items.append(P(3))  # tpyc: warning(/may move the elements of 'h.items', but 's' still views them/)
    print(s[0].x)


# live, whole Span: a getter returning its Span argument, the viewed list grows
def live_whole(xs: list[P]) -> None:  # tpyc: mir(conflict /replacement/)
    sp: Span[P] = xs[0:]
    s = whole_span(sp)  # tpyc: mir_borrows(s, xs[elements])
    xs.append(P(3))  # tpyc: warning(/Mutation of 'xs' while borrowed/) warning(/may move the elements of 'xs', but 's' still views them/)
    print(s[0].x)


# live, a getter's own write: a holder from an EARLIER call still conflicts with the growing call
def live_own_write(g: G) -> None:  # tpyc: mir(conflict /replacement/)
    a = g.first()
    b = g.push_first()  # tpyc: warning(/Mutation of 'g' while borrowed/) warning(/may move the elements of 'g.items', but 'a' still refers to them/)
    print(a.x, b.x)


def live_own_write_rebind(g: G) -> None:  # tpyc: mir(conflict /replacement/)
    b = g.first()
    a = g.push_first()  # tpyc: warning(/Mutation of 'g' while borrowed/) warning(/may move the elements of 'g.items', but 'b' still refers to them/)
    print(b.x, a.x)


# live, a result passed back from an argument the call's writes reach: the callee's no-alias
# obligation warns at the call (argument-alias), not the own-write rule
def live_pass_back(xs: list[P]) -> None:  # tpyc: mir(conflict /argument_alias/)
    r = pass_back(xs[0], xs)  # tpyc: warning(/argument 'xs.0.' points into 'xs'/)
    print(r.x)


def live_keep(g: G) -> None:  # tpyc: mir(conflict /argument_alias/)
    r = g.keep(g.items[0])  # tpyc: warning(/argument 'g.items.0.' points into 'g.items'/)
    print(r.x)


# live, view: a StrView binding of a str element, read after its list grows
def live_view(h: H) -> None:  # tpyc: mir(conflict /replacement/)
    v: StrView = h.name_at(0)  # tpyc: mir_borrows(v, h.names[elements])
    h.names.append("c")  # tpyc: warning(/may move the elements of 'h.names', but 'v' still views them/)
    print(v)


def live_view_free() -> None:  # tpyc: mir(conflict /replacement/)
    names = ["a"]
    v: StrView = name_of(names, 0)  # tpyc: mir_borrows(v, names[elements])
    names.append("b")  # tpyc: warning(/Mutation of 'names' while borrowed/) warning(/may move the elements of 'names', but 'v' still views them/)
    print(v)


# dead, free function: the element is read before the list grows (MIR is clean; sema's checker
# warns, BUGS.md#borrow-warning-not-last-use-aware)
def dead_free() -> None:  # tpyc: mir(certified)
    xs = [P(1)]
    a = first(xs)
    print("dead_free", a.x)
    xs.append(P(2))  # tpyc: warning(/Mutation of 'xs' while borrowed/)


# write-through, free function, method and property: the mutation is seen through the container
def through_free() -> None:  # tpyc: mir(certified)
    xs = [P(1)]
    a = first(xs)
    a.x = 9  # tpyc: ok
    print("through_free", xs[0].x)


def through_method(h: H) -> None:  # tpyc: mir(certified)
    a = h.at(1)
    a.x = 8  # tpyc: ok
    b = h.head
    b.x = 6  # tpyc: ok
    print("through_method", h.items[1].x, h.items[0].x)


# dead, nested and container element
def dead_nested() -> None:  # tpyc: mir(certified)
    rows = [[P(1)]]
    a = nested(rows, 0, 0)
    print("dead_nested", a.x)
    rows[0].append(P(2))  # tpyc: ok
    ints = [[3, 4]]
    r = row_of(ints, 0)
    print("dead_row", len(r))


# dead, owned-temporary index or key (sema's false warnings: BUGS.md#borrow-warning-not-last-use-aware)
def dead_big() -> None:  # tpyc: mir(certified)
    xs = [P(1), P(2)]
    a = big_nth(xs, 0)
    print("dead_big", a.x)
    xs.append(P(3))  # tpyc: warning(/Mutation of 'xs' while borrowed/)


def dead_keyed() -> None:  # tpyc: mir(certified)
    d: dict[str, list[int32]] = {"a": [1]}
    v = keyed(d, "")
    print("dead_keyed", len(v))
    d.clear()  # tpyc: warning(/Mutation of 'd' while borrowed/)


# dead and write-through, region endpoint: read before the growth; a write through the Span is
# seen through the list
def dead_tail(h: H) -> None:  # tpyc: mir(covered)
    s = h.tail()
    print("dead_tail", s[0].x)
    h.items.append(P(3))  # tpyc: ok


def through_tail(h: H) -> None:  # tpyc: mir(covered)
    s = h.tail()
    s[0].x = 7  # tpyc: ok
    print("through_tail", h.items[1].x)


def dead_tail_annotated(h: H) -> None:  # tpyc: mir(covered)
    s: Span[P] = h.tail()
    print("dead_tail_annotated", s[0].x)
    h.items.append(P(3))  # tpyc: ok


def through_tail_annotated(h: H) -> None:  # tpyc: mir(covered)
    s: Span[P] = h.tail()
    s[0].x = 6  # tpyc: ok
    print("through_tail_annotated", h.items[1].x)


# dead, region endpoint at a readonly view type: bound, rebound, returned
def dead_ro_tail(h: H) -> None:  # tpyc: mir(covered)
    s: Span[readonly[P]] = h.tail()  # tpyc: ok
    print("dead_ro_tail", s[0].x)
    t: Span[readonly[P]] = h.items[0:]
    t = h.tail()  # tpyc: ok
    print("dead_ro_rebind", t[0].x)
    u = as_readonly_tail(h)  # tpyc: ok
    print("dead_ro_returned", u[0].x)
    h.items.append(P(3))  # tpyc: ok


# dead, whole Span (sema's false warning: BUGS.md#borrow-warning-not-last-use-aware)
def dead_whole() -> None:  # tpyc: mir(covered)
    xs = [P(1)]
    sp: Span[P] = xs[0:]
    s = whole_span(sp)
    print("dead_whole", s[0].x)
    xs.append(P(3))  # tpyc: warning(/Mutation of 'xs' while borrowed/)


# own write, method: a getter's write precedes the result its caller holds -- flat, Span,
# nested, field
def own_write_flat(g: G) -> None:  # tpyc: mir(covered)
    a = g.push_first()  # tpyc: ok
    print("own_write_flat", a.x)


def own_write_span(g: G) -> None:  # tpyc: mir(covered)
    s = g.push_tail()  # tpyc: ok
    print("own_write_span", s[0].x)


def own_write_deep(g: G) -> None:  # tpyc: mir(covered)
    a = g.push_deep()  # tpyc: ok
    print("own_write_deep", a.x)


def own_write_field(g: G) -> None:  # tpyc: mir(covered)
    a = g.reset_get()  # tpyc: ok
    print("own_write_field", a.x)


# own write: the result slot rebound (sema's false warning: BUGS.md#borrow-warning-not-last-use-aware),
# returned, bound in a loop, and through a free function
def own_write_rebind(g: G) -> None:  # tpyc: mir(covered)
    a = g.first()
    a = g.push_first()  # tpyc: warning(/Mutation of 'g' while borrowed/)
    print("own_write_rebind", a.x)


def own_write_returned(g: G) -> P:  # tpyc: mir_summary(known)
    return g.push_first()  # tpyc: ok


def own_write_loop(g: G) -> None:  # tpyc: mir(covered)
    for _ in range(2):
        a = g.push_first()  # tpyc: ok
        print("own_write_loop", a.x)


def own_write_free(xs: list[P]) -> None:  # tpyc: mir(covered)
    a = push_get(xs)  # tpyc: ok
    print("own_write_free", a.x)


# own write, projected target: a field or a tuple element takes a copy (warned), and MIR does not
# lower the caller -- no exemption applies there
def own_write_field_target(g: G, k: K) -> None:  # tpyc: mir(uncovered /owned result from a borrowed call/)
    k.p = g.push_first()  # tpyc: warning(/copies P into field/)
    print("own_write_field_target", k.p.x)


def own_write_tuple_target(g: G) -> None:  # tpyc: mir(uncovered /missing or invalid layout fact/)
    t = (g.push_first(), 1)  # tpyc: warning(/copies P into owned storage/)
    print("own_write_tuple_target", t[0].x)


# view, getter only: the caller reaches 'h.names' only through the getter's published path
def getter_only_leaf(h: H) -> None:  # tpyc: mir(covered)
    v: StrView = h.name_at(0)  # tpyc: ok
    print("getter_only_leaf", v)


# owned copy: an unannotated binding of a view getter is an owned str, unaffected by the growth
def owned_copy(h: H) -> None:  # tpyc: mir(covered)
    v = h.name_at(0)
    h.names.append("c")  # tpyc: ok
    print("owned_copy", v)


def main() -> None:
    dead_free()
    through_free()
    through_method(H())
    dead_nested()
    dead_big()
    dead_keyed()
    dead_tail(H())
    through_tail(H())
    dead_tail_annotated(H())
    through_tail_annotated(H())
    dead_ro_tail(H())
    dead_whole()
    g = G()
    own_write_flat(g)
    own_write_span(g)
    own_write_deep(g)
    own_write_field(g)
    own_write_rebind(G())
    print("own_write_returned", own_write_returned(G()).x)
    own_write_loop(G())
    own_write_free([P(1)])
    own_write_field_target(G(), K())
    own_write_tuple_target(G())
    getter_only_leaf(H())
    owned_copy(H())


main()
