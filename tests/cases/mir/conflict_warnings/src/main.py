# MIR's lifetime warnings: each definite or "may" hazard warns at its kill (replacement, end of a
# block, a temporary's end, a store or return escaping the body); safe siblings and evidence stay silent.
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


# dict insert under its own loop -- known gap, silent: BUGS.md#dict-grows-during-own-iteration
def dict_insert(d: dict[int32, int32]) -> int32:  # tpyc: mir(covered)
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


class Hs:
    s: StrView

    def __init__(self) -> None:
        self.s = "-"

    def maybe(self, s: StrView, flag: bool) -> None:
        if flag:
            self.s = s

    def maybe_reset(self, flag: bool) -> None:
        if flag:
            self.s = "safe"


class Hl:
    items: list[int32]

    def __init__(self) -> None:
        self.items = mk_list(1)


def mk_list(n: int32) -> Own[list[int32]]:
    xs: list[int32] = []
    for i in range(24):
        xs.append(n)
    return xs


# may hazard, end of a block, constructor: the record rebuilt in the block views its buffer on one path
def b_ctor(f: bool) -> None:
    tk = Tok("x", 0)
    if f:
        buf = mk("g")
        tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 723/)
    junk = mk("z")
    print("b_ctor", tk.s[0], len(junk))


# may hazard, end of a block, constructor in both arms: only the `if` arm views a block-local buffer
def b_ctor_else(f: bool) -> None:
    if f:
        buf = mk("h")
        tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 734/)
    else:
        tk = Tok("x", 0)
    junk = mk("z")
    print("b_ctor_else", tk.s[0], len(junk))


# may hazard, end of a block, field store: the view member keeps a block-local buffer on one path
def h_field(f: bool) -> None:
    h = Hs()
    if f:
        buf = mk("b")
        h.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'h.s' may still view it and is read on line 744/)
    junk = mk("z")
    print("h_field", h.s[0], len(junk))


class Sites:
    n: int32

    # may hazard, end of a block, method body with local holders
    def run(self, f: bool) -> None:
        tk = Tok("x", 0)
        if f:
            buf = mk("m")
            tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 757/)
        junk = mk("z")
        print("method", tk.s[0], len(junk))

    # may hazard, end of a block, constructor body with local holders
    def __init__(self, f: bool) -> None:
        self.n = 0
        h = Hs()
        if f:
            buf = mk("c")
            h.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'h.s' may still view it and is read on line 767/)
        junk = mk("z")
        print("ctor", h.s[0], len(junk))


# may hazard, end of a loop body left by `break`: the view member keeps the body's buffer
def store_break(xs: list[str]) -> None:
    h = Hs()
    for x in xs:
        buf = mk(x)
        h.s = buf
        break  # tpyc: warning(/'buf' is freed when control leaves its block here, and 'h.s' may still view it and is read on line 778/)
    junk = mk("z")
    print("store_break", h.s[0], len(junk))


# may hazard, iteration over a joined source: growing `a` may move what the walk over `v` walks
def cursor_join(flag: bool) -> None:
    a = [1, 2, 3]
    b = [4, 5, 6]
    v = a if flag else b
    n = 0
    for x in v:
        if n == 0:
            a.append(7)  # tpyc: warning(/this call may move the elements of 'a', and the iteration over 'v' may still walk them and continues on line 787/)
        n += 1
        print("cursor_join", x)


# may hazard, iteration over a joined record's field: replacing `h.items` may free the walked list
def field_cursor(f: bool) -> None:
    h = Hl()
    g = Hl()
    x = h if f else g
    t = 0
    for i in x.items:
        h.items = mk_list(2)  # tpyc: warning(/this assignment frees the old value of 'h.items', and the iteration over 'x.items' may still walk it and continues on line 800/)
        t += i
    print("field_cursor", t)


# may hazard, leaf cursor: a callee may have stored `buf` in `t.s`, whose characters the loop walks
def leaf_cursor(flag: bool) -> None:
    buf = mk("a")
    t = Hs()
    t.maybe(buf, flag)
    n = 0
    for c in t.s:
        if n == 0:
            buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and the iteration over 't.s' may still walk it and continues on line 812/)
        if c == "z":
            n += 1
    print("leaf_cursor", n, len(buf))


# may hazard, a rewrite through a joined name after the block: `x.s = ...` may not rebind `a.s`
def may_rebind(c: bool, f: bool) -> None:
    a = Hs()
    b = Hs()
    x = a if c else b
    if f:
        buf = mk("b")
        a.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'a.s' may still view it and is read on line 830/)
    x.s = "safe"
    junk = mk("z")
    print("may_rebind", a.s[0], len(junk))


# may hazard, a Span over a joined slice: growing `a` may move what `s` views
def span_join(c: bool) -> int32:
    a = [1, 2]
    b = [3, 4]
    s: Span[int32] = b[0:]
    if c:
        s = a[0:]
    a.append(5)  # tpyc: warning(/Mutation of 'a' while borrowed \('append' may invalidate references\)/) warning(/this call may move the elements of 'a', and 's' may still view them and is read on line 841/)
    return s[0]


# may hazard, a view copied back into a field: `a.s = v` restores the freed buffer, read through `x`
def restore_field(f: bool) -> None:
    buf = mk("a")
    old = Tok(buf, 0)
    a = Tok(buf, 1)
    b = Tok("safe", 2)
    x = a if f else b
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'old.s' may still view it and is read on line 855/)
    x.s = "safe"
    v: StrView = old.s
    a.s = v
    print("restore_field", x.s[0])


# may hazard, a callee that only may reset: `a.maybe_reset(flag)` leaves the copy in `y.s` viewing the
# freed buffer when flag is False (called with True only)
def callee_may_reset(flag: bool) -> None:
    buf = mk("a")
    old = Hs()
    old.s = buf
    a = Hs()
    y = a
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'old.s' may still view it and is read on line 870/)
    v: StrView = old.s
    y.s = v
    a.maybe_reset(flag)
    print("callee_may_reset", y.s[0])


# guard, safe: the read runs only on the path that stored nothing
def g_notc(c: bool) -> None:
    h = Hs()
    if c:
        buf = mk("a")
        h.s = buf  # tpyc: ok
    junk = mk("z")
    if not c:
        print("g_notc", h.s[0], len(junk))


# guard, safe: the record is rebuilt from a literal before the block ends
def g_rebind(f: bool) -> None:
    tk = Tok("x", 0)
    if f:
        buf = mk("d")
        tk = Tok(buf, 1)
        tk = Tok("y", 2)  # tpyc: ok
    junk = mk("z")
    print("g_rebind", tk.s[0], len(junk))


# guard, safe: the view member is rebound to a literal before the block ends
def g_rebind_field(f: bool) -> None:
    h = Hs()
    if f:
        buf = mk("e")
        h.s = buf
        h.s = "y"  # tpyc: ok
    junk = mk("z")
    print("g_rebind_field", h.s[0], len(junk))


# value element, safe: the str loop variable owns its copy, so a store of an element under the loop frees nothing it holds
def value_element(a: list[str]) -> None:
    for s in a:
        a[0] = mk("w")  # tpyc: ok
        print("value_element", s)


# dict values, safe: storing an existing key under a walk of the values replaces in place, inserting nothing
def values_existing_key(d: dict[str, str]) -> None:
    for v in d.values():
        d["a"] = mk("v")  # tpyc: ok
        print("values_existing_key", v)


# known false warning: BUGS.md#mir-may-warning-correlated-conditions -- a write and a later read through
# the same joined name: `x.s = "safe"` may rewrite either object, so the read of `x.s` may see the old buffer
def alias_reset_read(f: bool) -> None:
    buf = mk("a")
    a = Tok(buf, 1)
    b = Tok(buf, 2)
    x = a if f else b
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'a.s' may still view it and is read on line 929/) warning(/this assignment frees the old value of 'buf', and 'b.s' may still view it and is read on line 929/)
    x.s = "safe"
    print("alias_reset_read", x.s[0])


# known false warning: BUGS.md#mir-may-warning-correlated-conditions -- the copy twin of alias_reset_read:
# `y = x` after the write reads the new value whichever object `x` is
def alias_copy_read(f: bool) -> None:
    buf = mk("a")
    a = Tok(buf, 1)
    b = Tok(buf, 2)
    x = a if f else b
    buf = mk("z")  # tpyc: warning(/this assignment frees the old value of 'buf', and 'a.s' may still view it and is read on line 942/) warning(/this assignment frees the old value of 'buf', and 'b.s' may still view it and is read on line 942/)
    x.s = "safe"
    y = x
    print("alias_copy_read", y.s[0])


# known false warning: BUGS.md#mir-may-warning-correlated-conditions -- the second `if f:` rebuilds the
# record whenever the first one made it view `buf`, but paths are not correlated
def correlated_scope_rebind(f: bool) -> str:
    tk = Tok("x", 0)
    if f:
        buf = mk("g")
        tk = Tok(buf, 1)  # tpyc: warning(/'buf' is freed at the end of this block, and 'tk.s' may still view it and is read on line 954/)
    if f:
        tk = Tok("y", 2)
    return str(tk.s[0])


# known false warning: BUGS.md#mir-warning-after-always-raising-call -- fail() raises on every path,
# so the read after it never runs
def may_after_raise(flag: bool) -> None:
    h = Hs()
    if flag:
        buf = mk("a")
        h.s = buf  # tpyc: warning(/'buf' is freed at the end of this block, and 'h.s' may still view it and is read on line 965/)
    fail()
    print("may_after_raise", h.s[0])


# known silent dangle: BUGS.md#mir-hazard-read-on-some-paths-silent -- the read runs under the store's own
# condition, so not every path from the block's end reads `h.s` (called with False only)
def g_samef(f: bool) -> None:
    h = Hs()
    if f:
        buf = mk("b")
        h.s = buf  # tpyc: ok
    junk = mk("z")
    if f:
        print("g_samef", h.s[0], len(junk))


# known silent dangle: BUGS.md#mir-hazard-read-on-some-paths-silent -- the constructor twin of g_samef
def g_samef_ctor(f: bool) -> None:
    tk = Tok("x", 0)
    if f:
        buf = mk("c")
        tk = Tok(buf, 1)  # tpyc: ok
    junk = mk("z")
    if f:
        print("g_samef_ctor", tk.s[0], len(junk))


def may_sections() -> None:
    # each warned section with the argument that keeps it safe
    b_ctor(False)
    b_ctor_else(False)
    h_field(False)
    Sites(False).run(False)
    store_break([])
    cursor_join(False)
    field_cursor(False)
    leaf_cursor(False)
    may_rebind(True, True)
    print("span_join", span_join(False))
    restore_field(False)
    callee_may_reset(True)
    g_notc(True)
    g_samef(False)
    g_samef_ctor(False)
    g_rebind(True)
    g_rebind_field(True)
    value_element(["one", "two"])
    values_existing_key({"a": "x", "b": "y"})
    alias_reset_read(True)
    alias_reset_read(False)
    alias_copy_read(True)
    alias_copy_read(False)
    print("correlated_scope_rebind", correlated_scope_rebind(True), correlated_scope_rebind(False))
    try:
        may_after_raise(True)
    except IndexError:
        print("may_after_raise raised")


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
    may_sections()


main()
