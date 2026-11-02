# MIR's lifetime warnings: each definite hazard warns at its kill (replacement, end of a block,
# a temporary's end, a store or return escaping the body); safe siblings and evidence stay silent.
from tpy import Own, ReturnException, Span, StrView, auto_readonly, copy, error_return, int32


class Tok:
    s: StrView
    n: int32

    def __init__(self, s: StrView, n: int32) -> None:
        self.s = s
        self.n = n

    def adopt(self, v: StrView) -> None:
        self.s = v


class Bad(Exception, ReturnException):
    code: int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk(c: str) -> str:
    return c * 40


def mk_a(n: int32) -> Own[A]:
    return A(n)


def first(s: StrView) -> StrView:
    return s


# replacement, free function: the buffer a view member views is replaced, then the member is read
def local_buf(flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        buf = mk("b")  # tpyc: warning(/this assignment frees the old value of 'buf', but 't.s' still views it and is read on line 54; give the new value its own name/)
    print("local_buf", t.s[0], t.n)


# replacement, free function, safe sibling: the new value gets its own name
def local_buf_named(flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        other = mk("b")  # tpyc: ok
        print("local_buf_named", other[0])
    print("local_buf_named", t.s[0])


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    # replacement, method: the list grows under its own loop
    def grow(self) -> int32:
        t = 0
        for x in self.items:
            if x < 0:
                self.items.append(x)  # tpyc: warning(/this call may move the elements of 'self.items', but the iteration over 'self.items' still walks them/)
            t += x
        return t

    # replacement, method, safe sibling: the loop walks a copy
    def grow_copy(self) -> int32:
        t = 0
        for x in copy(self.items):
            if x < 0:
                self.items.append(x)  # tpyc: ok
            t += x
        return t


# replacement, free function: an element reference held across a growth of its list
def kept(ps: list[P], flag: bool) -> int32:
    p = ps[0]
    if flag:
        ps.append(P(2))  # tpyc: warning(/this call may move the elements of 'ps', but 'p' still refers to them and is used on line 97/)
    return p.x


# end of a block: a view of a block-local buffer outlives the block
def block_view(c: bool) -> bool:
    v: StrView = "x"
    if c:
        buf = mk("a")
        v = buf  # tpyc: warning(/'buf' is freed at the end of this block, but 'v' still views it and is read on line 106; create 'buf' before the block/)
    return v == "x"


# end of a block, safe sibling: the buffer is created before the block
def block_view_outer(c: bool) -> bool:
    v: StrView = "x"
    buf = mk("a")
    if c:
        v = buf  # tpyc: ok
    return v == "x"


# a temporary's end: a record keeps a view of a temporary that dies with its statement (never run)
def temp_view(c: str) -> bool:
    t = Tok(first(mk(c)), 1)  # tpyc: warning(/the temporary 'mk\(c\)' lives only until the end of this statement, but 't.s' still views it and is read on line 121/)
    return t.s == "x"


# a temporary's end, safe sibling: the buffer is bound to a variable first
def temp_view_bound(c: str) -> bool:
    buf = mk(c)
    t = Tok(first(buf), 1)  # tpyc: ok
    return t.s == "x"


# store escape: a body-local buffer stored into the caller's record (never run)
def fill(t: Tok, c: str) -> None:
    buf = mk(c)
    t.s = buf  # tpyc: warning(/'buf' is freed when 'fill' returns, but 't.s' keeps a view of it; make 'Tok.s' an owned 'str'/)


# store escape, safe sibling: a literal lives as long as the program
def fill_static(t: Tok) -> None:
    t.s = "lit"  # tpyc: ok


class C:
    v: StrView

    # store escape, constructor: a view of a temporary kept in a field (never constructed)
    def __init__(self, c: str) -> None:
        self.v = first(mk(c))  # tpyc: warning(/the temporary 'mk\(c\)' lives only until the end of this statement, but 'self.v' keeps a view of it; make 'C.v' an owned 'str'/)


# return escape: the result views a reassigned parameter's new buffer (never run)
def reassigned(s: str) -> StrView:
    s = s + "x"
    return s  # tpyc: warning(/the return type 'StrView' lends the result, but 's' is created inside 'reassigned' and freed when it returns/)


# return escape, safe sibling: the result views the caller's buffer
def kept_view(s: StrView) -> StrView:
    return s  # tpyc: ok


class Holder:
    s: StrView

    def __init__(self) -> None:
        self.s = "init"

    @property
    def view(self) -> StrView:
        return self.s

    # store escape, property setter: a body-local buffer kept in the receiver (never run)
    @view.setter
    def view(self, c: str) -> None:
        buf = mk(c)
        self.s = buf  # tpyc: warning(/'buf' is freed when 'Holder.view' returns, but 'self.s' keeps a view of it/)

    # replacement, @auto_readonly: both twins find it, one warning is given
    @auto_readonly
    def twin(self, flag: bool) -> int32:  # tpyc: mir(conflict /replacement/)
        buf = mk("a")
        t = Tok(buf, 1)
        if flag:
            buf = mk("b")  # tpyc: warning(/this assignment frees the old value of 'buf', but 't.s' still views it/)
        print("twin", t.s[0])
        return t.n


# @error_return body: MIR does not lower it yet, so the hazard is not checked (a coverage gap)
@error_return(Bad)
def error_body(flag: bool) -> int32:  # tpyc: mir(uncovered /-return body/)
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        buf = mk("b")
    print("error_body", t.s[0])
    return t.n


# try body: MIR does not lower a try statement yet (a coverage gap), so the hazard is not checked
def try_body(flag: bool) -> None:  # tpyc: mir(uncovered /unsupported statement/)
    try:
        buf = mk("a")
        t = Tok(buf, 1)
        if flag:
            buf = mk("b")
        print("try_body", t.s[0])
    except ValueError:
        pass


# match arm: MIR does not lower a match statement yet (a coverage gap), so the hazard is not checked
def match_arm(k: int32, flag: bool) -> None:  # tpyc: mir(uncovered /unsupported statement/)
    match k:
        case 1:
            buf = mk("a")
            t = Tok(buf, 1)
            if flag:
                buf = mk("b")
            print("match_arm", t.s[0])
        case _:
            pass


# returned record: the escape happens on some paths only (a callee re-points the member on the
# other) -- silent by decision, filed: TODO.md "escapes on only some paths" precision item
def r_held(q: Tok, flag: bool) -> Own[Tok]:  # tpyc: mir(conflict /store_escape/)
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        t.adopt(q.s)
    return t


# returned record, safe sibling: a literal replaces the view on one path, so no path is proven
def r_lit(flag: bool) -> Own[Tok]:  # tpyc: mir(conflict /store_escape/)
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        t.s = "lit"
    return t


# store into the caller's record, safe sibling: a callee re-points the member on one path
def s_held(t: Tok, q: Tok, flag: bool) -> None:  # tpyc: mir(conflict /store_escape/)
    buf = mk("a")
    t.s = buf
    if flag:
        t.adopt(q.s)


# returned record, regression: only one return hands back the record holding the loan -- silent by
# decision, filed: TODO.md "escapes on only some paths" precision item
def two_returns(flag: bool) -> Own[Tok]:  # tpyc: mir(conflict /store_escape/)
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        return t
    u = Tok("x", 2)
    return u


# returned record, regression: the temporary's end is decided on its own when the escape is not proven
def returned_maybe(c: str, flag: bool, q: Tok) -> Own[Tok]:
    t = Tok(first(mk(c)), 1)  # tpyc: warning(/the temporary 'mk\(c\)' lives only until the end of this statement, but 't.s' still views it/)
    print("returned_maybe", t.s[0])
    if flag:
        t.adopt(q.s)
    return t


# returned record, regression: a temporary's statement end warns although the record is returned
def returned(c: str) -> Own[Tok]:
    t = Tok(first(mk(c)), 1)  # tpyc: warning(/the temporary 'mk\(c\)' lives only until the end of this statement, but 't.s' still views it/)
    print("returned", t.s[0])
    t.s = "safe"
    return t


class Named:
    x: int32
    n: str

    def __init__(self, x: int32, n: str) -> None:
        self.x = x
        self.n = n


class NamedLine:
    a: Named

    def __init__(self, a: Named) -> None:
        self.a = a


class Points:
    items: list[P]
    inner: P

    def __init__(self) -> None:
        self.items = [P(1)]
        self.inner = P(1)


class Frame:
    pts: Points

    def __init__(self) -> None:
        self.pts = Points()


# replacement of a record, wording: a view of the record's str field sees freed storage (never run)
def record_view(ln: NamedLine, flag: bool) -> bool:
    v: StrView = ln.a.n
    if flag:
        ln.a = Named(9, mk("n"))  # tpyc: warning(/this assignment frees the old value of 'ln.a', but 'v' still views it/)
    return v == "old"


# replacement of a record, wording: a reference into the record's list field (never run)
def record_list(f: Frame, flag: bool) -> int32:
    p = f.pts.items[0]
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment frees the old value of 'f.pts', but 'p' still refers to it/)
    return p.x


# replacement of a record, wording: a reference to its list field object sees it rewritten in place
def record_list_object(f: Frame, flag: bool) -> int32:
    a = f.pts
    p = a.items
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment overwrites 'f.pts' in place, but 'p' still refers to it and sees the new value/)
    return p[0].x


# replacement of a record, wording: a reference to a record field of it sees it rewritten in place
def record_field(f: Frame, flag: bool) -> int32:
    a = f.pts
    p = a.inner
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment overwrites 'f.pts' in place, but 'p' still refers to it and sees the new value/)
    return p.x


def items_of(f: Frame) -> list[P]:
    return f.pts.items


# replacement of a record, wording: the list object a call lends out of it is rewritten in place
def lent_list_object(f: Frame, flag: bool) -> int32:
    lst = items_of(f)
    if flag:
        f.pts = Points()  # tpyc: warning(/this assignment overwrites 'f.pts' in place, but 'lst' still refers to it and sees the new value/)
    return lst[0].x


# replacement of a record, wording: a reference to the record itself sees it rewritten in place
def record_alias(ln: NamedLine, flag: bool) -> int32:
    p = ln.a
    if flag:
        ln.a = Named(7, "c")  # tpyc: warning(/this assignment overwrites 'ln.a' in place, but 'p' still refers to it and sees the new value/)
    return p.x


def fail() -> int32:
    xs: list[int32] = []
    return xs[0]


# known false warning: BUGS.md#mir-warning-after-always-raising-call -- fail() raises on every path,
# so the read after it never runs; the witness does not yet cut a path at a call that always raises
def after_raise() -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    buf = mk("b")  # tpyc: warning(/this assignment frees the old value of 'buf', but 't.s' still views it/)
    fail()
    print("after_raise", t.s[0])


# leak shape: the view member is rebound before any read
def overwritten(flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    buf = mk("b")  # tpyc: ok
    t.s = "safe"
    print("overwritten", t.s, buf[0])


# leak shape: the member is restored to the caller's loan before the return
def restore(t: Tok) -> None:
    old: StrView = t.s
    tmp = mk("q")
    t.s = tmp  # tpyc: ok
    t.s = old


# which element: an element write under another element's reference -- indices carry no identity,
# so `ps[j]` may be `ps[i]` and the write may overwrite what `p` refers to (a "may" warning)
def other_element(ps: list[P], i: int32, j: int32) -> int32:  # tpyc: mir(conflict /replacement/)
    p = ps[i]
    ps[j] = P(9)  # tpyc: warning(/this assignment may overwrite what 'p' refers to in place, and 'p' would see the new value on line 402/)
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
        adj[v].append(u)  # tpyc: warning(/this call may move the element the iteration over 'adj\[u\]' still walks, and the loop continues on line 418; iterate over a copy \('copy\(adj\[u\]\)'\)/)


# which element, temporary borrow: `rows[0]` and `rows[1]` are distinct constants, warned all the
# same (accepted false warning: indices carry no identity)
def other_row(rows: list[list[int32]]) -> None:
    for v in rows[1]:
        rows[0].append(v)  # tpyc: warning(/this call may move the element the iteration over 'rows\[1\]' still walks/)


# which element, str element: replacing an element may free the buffer a view of another one views
def str_view(names: list[str], i: int32) -> None:
    v: StrView = names[0]
    names[i] = "x" + str(i)  # tpyc: warning(/this assignment may free the element 'v' still views, and 'v' is read on line 433/)
    print("str_view", v)


# which element, str loop twin: the loop variable is an owned copy, so the element store under the loop
# frees nothing it holds and warns nothing; the conflict is the store against the walk's elements region,
# kept as evidence (the copy itself is unwarned: BUGS.md#str-loop-var-copied-when-list-written)
def str_loop(names: list[str], i: int32) -> None:  # tpyc: mir(conflict /replacement/)
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


def which_element() -> None:
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


# what the caller passes: a parameter's view member may view the list the body writes -- silent by
# decision, filed: BUGS.md#borrowed-arg-into-mutated-storage
def through_param(t: Tok, xs: list[str]) -> int32:  # tpyc: mir(conflict /replacement/)
    xs[0] = mk("z")
    return t.n + len(t.s)


# dict insert as an element write -- silent by decision, filed: BUGS.md#dict-grows-during-own-iteration
def dict_insert(d: dict[int32, int32]) -> int32:  # tpyc: mir(conflict /replacement/)
    n = 0
    for k in d:
        if k > 100:
            d[k + 1] = 2
        n += 1
    return n


class Hv:
    v: StrView

    def __init__(self, v: StrView) -> None:
        self.v = v


# summary, two parameters: a stored view the caller chooses may view what the body grows, so no
# known summary can drop the conflict
def drop_and_read(xs: list[P], h: Hv) -> bool:  # tpyc: mir_summary(opaque /summary retention/)
    xs.append(P(2))
    return h.v == "keep"


# uncovered until such endpoints publish as obligations (TODO "Argument-alias obligations" (c))
def drop_and_read_call() -> None:  # tpyc: mir(uncovered /known summary/)
    xs = [P(1)]
    h = Hv("keep")
    r = drop_and_read(xs, h)
    print("drop_and_read", r, len(xs))


class Rb:
    buf: str
    s: StrView

    def __init__(self, buf: str, s: StrView) -> None:
        self.buf = buf
        self.s = s


# summary, one parameter: its stored view may view the buffer the body replaces in the same object
def replace_and_read(r: Rb) -> bool:  # tpyc: mir_summary(opaque /summary retention/)
    r.buf = mk("r")
    return r.s == "keep"


# uncovered until such endpoints publish as obligations (TODO "Argument-alias obligations" (c))
def replace_and_read_call() -> None:  # tpyc: mir(uncovered /known summary/)
    rb = Rb("a", "keep")
    r = replace_and_read(rb)
    print("replace_and_read", r, rb.buf[0])


GS: str = "keep"


class Rs:
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


# summary, a global against a parameter: the caller may pass the global's storage
def replace_then_read_global(r: Rs) -> bool:  # tpyc: mir_summary(opaque /summary retention/)
    r.s = mk("r")
    return GS == "keep"


# uncovered until such endpoints publish as obligations (TODO "Argument-alias obligations" (c))
def replace_then_read_global_call() -> None:  # tpyc: mir(uncovered /known summary/)
    rs = Rs("a")
    r = replace_then_read_global(rs)
    print("replace_then_read_global", r, rs.s[0])


class Box:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(1)]


# summary, inverse: a conflict inside one lent object is the body's own, whatever the caller passes
def one_object(h: Box, flag: bool) -> int32:  # tpyc: mir_summary(known)
    p = h.items[0]
    if flag:
        h.items.append(P(3))  # tpyc: warning(/'append' may invalidate references/) warning(/may move the elements of 'h.items'/)
    return p.x


# summary, inverse: a conflict between two parameters stays a published no-alias obligation
def grow_keep(xs: list[P], p: P) -> int32:  # tpyc: mir_summary(known) mir(conflict /alias_obligation/)
    xs.append(P(3))
    return p.x


def summary_inverse_call() -> None:
    h = Box()
    n = one_object(h, False)
    xs = [P(1)]
    p = P(4)
    m = grow_keep(xs, p)
    print("summary_inverse", n, m, len(xs))


def main() -> None:
    local_buf(False)
    local_buf_named(True)
    b = Bag()
    print("grow", b.grow(), b.grow_copy())
    print("kept", kept([P(1)], False))
    print("block_view", block_view(False), block_view_outer(True))
    print("temp_view_bound", temp_view_bound("x"))
    t = Tok("x", 3)
    fill_static(t)
    print("fill_static", t.s)
    overwritten(True)
    restore(t)
    print("restore", t.s)
    which_element()
    print("through_param", through_param(Tok("ab", 1), ["c"]))
    print("dict_insert", dict_insert({1: 1, 2: 2}))
    print("kept_view", kept_view("k"))
    h = Holder()
    n = h.twin(False)
    print("twin", n, h.view)
    try:
        e = error_body(False)
        print("error_body", e)
    except Bad:
        print("error_body raised")
    try_body(False)
    try:
        after_raise()
    except IndexError:
        print("after_raise raised")
    print("record_alias", record_alias(NamedLine(Named(1, "a")), False))
    fr = Frame()
    print("record_objects", record_list_object(fr, False), record_field(fr, False), lent_list_object(fr, False))
    match_arm(1, False)
    drop_and_read_call()
    replace_and_read_call()
    replace_then_read_global_call()
    summary_inverse_call()


main()
