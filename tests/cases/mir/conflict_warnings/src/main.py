# MIR's lifetime warnings: a hazard warns at its kill, safe siblings stay silent. The core shapes are
# here; returned records, records, elements, summaries, "may" hazards and loops are in the part_* modules.
import part_elements, part_loop_resets, part_loops, part_may, part_may_known, part_records
import part_returns
from fixtures import P, Tok, mk
from tpy import StrView, auto_readonly, copy, int32


def first(s: StrView) -> StrView:
    return s


# replacement, free function: the buffer a view member views is replaced, then the member is read
def local_buf(flag: bool) -> None:
    buf = mk("a")
    t = Tok(buf, 1)
    if flag:
        buf = mk("b")  # tpyc: warning(/this assignment frees the old value of 'buf', but 't.s' still views it and is read on line 19; give the new value its own name/)
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
        ps.append(P(2))  # tpyc: warning(/this call may move the elements of 'ps', but 'p' still refers to them and is used on line 62/)
    return p.x


# end of a block: a view of a block-local buffer outlives the block; a view holder is told to create the
# buffer before the block, never to assign it directly (a view of the call result would view a temporary,
# freed just the same)
def block_view(c: bool) -> bool:
    v: StrView = "x"
    if c:
        buf = mk("a")
        v = buf  # tpyc: warning(/'buf' is freed at the end of this block, but 'v' still views it and is read on line 73; create 'buf' before the block$/)
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
    t = Tok(first(mk(c)), 1)  # tpyc: warning(/the temporary 'mk\(c\)' lives only until the end of this statement, but 't.s' still views it and is read on line 88/)
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
    part_records.overwritten(True)
    part_records.restore(t)
    print("restore", t.s)
    print("kept_view", kept_view("k"))
    h = Holder()
    n = h.twin(False)
    print("twin", n, h.view)
    replace_then_read_global_call()
    part_returns.main()
    part_records.main()
    part_elements.main()
    part_may.main()
    part_may_known.main()
    part_loops.main()
    part_loop_resets.main()


main()
