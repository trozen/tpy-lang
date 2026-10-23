# A borrow of a written loop element kept past the loop reads the element
# CPython aliases; a loop variable moved out while borrowed copies, warned.
from tpy import Ptr, StrView, int32


class C:
    def __init__(self, v: int32) -> None:
        self.v = v


class P:
    n: int32
    name: str
    cell: C

    def __init__(self, n: int32, name: str) -> None:
        self.n = n
        self.name = name
        self.cell = C(n * 100)

    def get(self) -> C:
        return self.cell


class H:
    ptr: Ptr[P]

    def __init__(self) -> None:
        self.ptr = None


def bump(p: P) -> None:
    p.n += 1


def ident(x: P) -> P:
    return x


def junk() -> int32:
    # Fresh allocations that would reuse a freed element's storage (a list:
    # an unmutated list literal would be an Array; so are the sources below).
    js: list[P] = [P(77, "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"),
                   P(88, "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr")]
    return len(js)


# alias kept past a loop inside an `if`
def alias_in_if(flag: bool) -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    last = P(0, "z")
    if flag:
        for p in ps:
            p.n += 1  # tpyc: ok
            last = p
    junk()
    last.n += 10
    print("alias_in_if:", last.n)


# the element written by a callee
def call_alias(flag: bool) -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    last = P(0, "z")
    if flag:
        for p in ps:
            bump(p)  # tpyc: ok
            last = p
    junk()
    last.n += 10
    print("call_alias:", last.n)


# loop inside a try
def try_alias() -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    last = P(0, "z")
    try:
        for p in ps:
            p.n += 1  # tpyc: ok
            last = p
    finally:
        pass
    junk()
    last.n += 10
    print("try:", last.n)


# the alias hoisted out of a while body
def while_hoist() -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    while True:
        last = P(0, "z")
        for p in ps:
            p.n += 1  # tpyc: ok
            last = p
        break
    junk()
    last.n += 10
    print("while_hoist:", last.n)


# nested loops: the inner element kept
def nested_loop(flag: bool) -> None:
    rows: list[list[P]] = [[P(1, "a")], [P(2, "b")]]
    keep = P(0, "z")
    if flag:
        for row in rows:
            for q in row:
                q.n += 1  # tpyc: ok
                keep = q
    junk()
    keep.n += 10
    print("nested_loop:", keep.n)


# a member of the element kept
def member(flag: bool) -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    keep = C(0)
    if flag:
        for p in ps:
            p.n += 1  # tpyc: ok
            keep = p.cell
    junk()
    keep.v += 10
    print("member:", keep.v)


# a pointer field set to the element
def ptr_field(flag: bool) -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    h = H()
    if flag:
        for p in ps:
            p.n += 1  # tpyc: ok
            h.ptr = p
    junk()
    hp = h.ptr
    if hp is not None:
        hp.n += 10
        print("ptr_field:", hp.n)


# a declared view of the element's string
def declared_view(flag: bool) -> None:
    ps: list[P] = [P(1, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                   P(2, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")]
    nm: StrView = "zz"
    if flag:
        for p in ps:
            p.n += 1  # tpyc: ok
            nm = p.name
    junk()
    print("declared_view:", nm)


# a borrowing call's result kept
def ident_call(flag: bool) -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    last = P(0, "z")
    if flag:
        for p in ps:
            p.n += 1  # tpyc: ok
            last = ident(p)
    junk()
    last.n += 10
    print("ident:", last.n)


# the kept element lent on to an outer name after the loop
def relend_after(flag: bool) -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    outer = P(9, "o")
    if flag:
        last = P(0, "z")
        for p in ps:
            p.n += 1  # tpyc: ok
            last = p
        outer = last
    junk()
    outer.n += 10
    print("relend_after:", outer.n)


# the inner name reused by a later loop
def reused_name(flag: bool) -> None:
    rows: list[list[P]] = [[P(1, "a")], [P(2, "b")]]
    others: list[P] = [P(50, "o")]
    keep = P(0, "z")
    if flag:
        for row in rows:
            for q in row:
                q.n += 1  # tpyc: ok
                keep = q
            for q in others:
                print("reused_name: other", q.n)
    junk()
    keep.n += 10
    print("reused_name:", keep.n)


# moved out while a member of it is still borrowed: copied, as a local is
def moved_while_borrowed() -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    out: list[P] = []
    keep = C(5)
    for p in ps:
        keep = p.cell
        out.append(p)  # tpyc: warning(/copies P into owned storage/)
    keep.v += 10
    print("moved_while_borrowed:", keep.v, len(out))


# moved out while a call's result still borrows it: copied, as a local is
def moved_while_call_borrowed() -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    out: list[P] = []
    for p in ps:
        keep = p.get()
        # the plain-local twin (`x = P(..); keep = x.get(); out.append(x)`)
        # warns the same pair, "borrowed container" included
        # (BUGS.md#borrowed-container-warning-names-record)
        out.append(p)  # tpyc: warning(/copies P into owned storage/)
        keep.v += 10
        print("moved_while_call_borrowed:", keep.v, len(out))


# moved out with no other borrower: the loop consumes its last-use source
def moved_out() -> None:
    ps: list[P] = [P(1, "a"), P(2, "b")]
    out: list[P] = []
    for p in ps:
        out.append(p)  # tpyc: ok
    out[0].n += 5
    print("moved_out:", out[0].n, out[1].n)


def main() -> None:
    alias_in_if(True)
    call_alias(True)
    try_alias()
    while_hoist()
    nested_loop(True)
    member(True)
    ptr_field(True)
    declared_view(True)
    ident_call(True)
    relend_after(True)
    reused_name(True)
    moved_while_borrowed()
    moved_while_call_borrowed()
    moved_out()


main()
