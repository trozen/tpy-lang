# MIR's lifetime warnings in bodies MIR does not lower yet (coverage gaps), store escapes through a
# returned record, and warnings against what the caller passes: parameter views, a dict growing under
# its walk, and callee summaries.
from fixtures import Bad, P, Tok, mk
from tpy import Own, StrView, error_return, int32


def first(s: StrView) -> StrView:
    return s


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
    try:
        e = error_body(False)
        print("error_body", e)
    except Bad:
        print("error_body raised")
    try_body(False)
    match_arm(1, False)
    print("through_param", through_param(Tok("ab", 1), ["c"]))
    print("dict_insert", dict_insert({1: 1, 2: 2}))
    drop_and_read_call()
    replace_and_read_call()
    summary_inverse_call()
