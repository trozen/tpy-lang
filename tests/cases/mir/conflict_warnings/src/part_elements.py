# MIR's lifetime warnings for element writes: indices carry no identity, so a write to one element
# may land on the one a holder keeps ("may" warnings); in-place writes under a walk stay silent.
from fixtures import P, Tok
from tpy import Span, StrView, int32


# which element: an element write under another element's reference -- indices carry no identity,
# so `ps[j]` may be `ps[i]` and the write may overwrite what `p` refers to (a "may" warning)
def other_element(ps: list[P], i: int32, j: int32) -> int32:  # tpyc: mir(conflict /replacement/)
    p = ps[i]
    ps[j] = P(9)  # tpyc: warning(/this assignment may overwrite what 'p' refers to in place, and 'p' would see the new value on line 12/)
    p.x += 100
    return p.x


# which element, loop variable: an element write while the loop variable refers to an element
def loop_var_overwrite(ps: list[P], j: int32, flag: bool) -> int32:
    t = 0
    for p in ps:
        if flag:
            ps[j] = P(9)  # tpyc: warning(/this assignment may overwrite what 'p' refers to in place/)
        t += p.x
    return t


# which element, adjacency: appending to one row while walking another, which may be the same row
def link(adj: list[list[int32]], u: int32) -> None:
    for v in adj[u]:
        adj[v].append(u)  # tpyc: warning(/this call may move the element the iteration over 'adj\[u\]' still walks, and the loop continues on line 28; iterate over a copy \('copy\(adj\[u\]\)'\)/)


# which element, temporary borrow: `rows[0]` and `rows[1]` are distinct constants, warned all the
# same (accepted false warning: indices carry no identity)
def other_row(rows: list[list[int32]]) -> None:
    for v in rows[1]:
        rows[0].append(v)  # tpyc: warning(/this call may move the element the iteration over 'rows\[1\]' still walks/)


# which element, str element: replacing an element may free the buffer a view of another one views
def str_view(names: list[str], i: int32) -> None:
    v: StrView = names[0]
    names[i] = "x" + str(i)  # tpyc: warning(/this assignment may free the element 'v' still views, and 'v' is read on line 43/)
    print("str_view", v)


# which element, str loop twin: the loop variable is an owned copy, so the element store under the loop
# frees nothing it holds, and the cursor depends on the structure only: no conflict
# (the copy itself is unwarned: BUGS.md#str-loop-var-copied-when-list-written)
def str_loop(names: list[str], i: int32) -> None:  # tpyc: mir(covered)
    for s in names:
        names[i] = "y" + str(i)  # tpyc: ok
        print("str_loop", s)


# which element, safe sibling: an element write in place of the one the loop just read
def scale(xs: list[int32]) -> None:
    i = 0
    for x in xs:
        xs[i] = x * 2  # tpyc: ok
        i += 1


# which element, safe sibling: the same through a Span parameter
def span_scale(s: Span[int32]) -> None:
    i = 0
    for x in s:
        s[i] = x * 2  # tpyc: ok
        i += 1


# which element, safe sibling: a dict update of the key being visited
def dict_scale(d: dict[int32, int32]) -> None:
    for k in d:
        d[k] = d[k] * 2  # tpyc: ok


# which element, plain holder: a row store may free the row holding the element `p` refers to
def inner_ref(rows: list[list[P]], i: int32, j: int32) -> int32:  # tpyc: mir(conflict /replacement/)
    p = rows[i][0]
    rows[j] = [P(1)]  # tpyc: warning(/this assignment may free the element 'p' still refers to, and 'p' is used/)
    p.x += 10
    return p.x


# which element, view field: an element store may free the buffer a record's view member views
def tok_view(names: list[str], i: int32) -> None:  # tpyc: mir(conflict /replacement/)
    t = Tok(names[0], 1)
    names[i] = "y" + str(i)  # tpyc: warning(/this assignment may free the element 't.s' still views.*or make 'Tok.s' an owned 'str'/)
    print("tok_view", t.s)


def clob(ps: list[P]) -> None:
    ps[0] = P(100)


# which element, callee: a callee's element store under the loop variable
def callee_loop(ps: list[P], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for p in ps:
        if flag:
            clob(ps)  # tpyc: warning(/this call may overwrite what 'p' refers to in place/)
        t += p.x
    return t


def store_then_grow(xs: list[P]) -> None:
    xs[0] = P(1)
    xs.append(P(2))


def grow_then_store(xs: list[P]) -> None:
    xs.append(P(2))
    xs[0] = P(1)


# which element, one call writing an element and the shape: the definite shape write is what warns
def kept_store_then_grow(xs: list[P], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    p = xs[0]
    if flag:
        store_then_grow(xs)  # tpyc: warning(/this call may move the elements of 'xs', but 'p' still refers to them/)
    return p.x


# the same with the callee's writes the other way round
def kept_grow_then_store(xs: list[P], flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    p = xs[0]
    if flag:
        grow_then_store(xs)  # tpyc: warning(/this call may move the elements of 'xs', but 'p' still refers to them/)
    return p.x


# which element, one call writing an inner element and growing that inner list: both are "may"
# hazards, and the move is what warns, not the in-place overwrite
def row_store_then_grow(rows: list[list[P]], i: int32, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    p = rows[i][0]
    if flag:
        store_then_grow(rows[0])  # tpyc: warning(/this call may move the element 'p' still refers to, and 'p' is used/)
    return p.x


# the same with the callee's writes the other way round
def row_grow_then_store(rows: list[list[P]], i: int32, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    p = rows[i][0]
    if flag:
        grow_then_store(rows[0])  # tpyc: warning(/this call may move the element 'p' still refers to, and 'p' is used/)
    return p.x


def main() -> None:
    ps = [P(1), P(2)]
    n = other_element(ps, 0, 1)
    # `p` is ps[0]: the write through it shows in the list
    print("other_element", n, ps[0].x, ps[1].x)
    print("loop_var_overwrite", loop_var_overwrite(ps, 1, False))
    adj = [[1], [0], [0]]
    link(adj, 2)
    print("link", adj)
    rows = [[1], [2, 3]]
    other_row(rows)
    print("other_row", rows)
    names = ["a", "b"]
    str_view(names, 1)
    str_loop(names, 1)
    print("names", names)
    xs = [1, 2, 3]
    scale(xs)
    span_scale(xs)
    print("scale", xs)
    d = {1: 1, 2: 2}
    dict_scale(d)
    print("dict_scale", d)
    grid = [[P(1)], [P(2)]]
    m = inner_ref(grid, 0, 1)
    # `p` is grid[0][0]: the write through it shows in the list
    print("inner_ref", m, grid[0][0].x, grid[1][0].x)
    tok_view(["a", "b"], 1)
    print("callee_loop", callee_loop(ps, False))
    qs = [P(5)]
    print("kept_calls", kept_store_then_grow(qs, False), kept_grow_then_store(qs, False), len(qs))
    print("row_calls", row_store_then_grow(grid, 0, False), row_grow_then_store(grid, 0, False), len(grid[0]))
