# The one view rule for inferred str/bytes locals: a view only over static
# storage, a view-family parameter, or a slice / view method / tuple element of
# a str, bytes or tuple NAME the function binds, which no statement of the
# function rebinds or rewrites; a loop variable never lends, and a field read,
# a container element or an rvalue-tuple element is OWNED.
import asyncio
from enum import Enum
from typing import Final, Iterator
from tpy import String, int32

GREETING: Final[str] = "final-greeting-static-storage"


class Color(Enum):
    RED = 1


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk_s(n: int32) -> str:
    return "payload-long-enough-to-defeat-sso-" + str(n)


def mk_b(n: int32) -> bytes:
    return mk_s(n).encode()


class H:
    s: str
    b: bytes
    t: tuple[str, bytes]
    rec: Rec

    def __init__(self, n: int32) -> None:
        self.s = mk_s(n)
        self.b = mk_b(n)
        self.t = (mk_s(n + 1), mk_b(n + 1))
        self.rec = Rec(n)

    def method(self) -> None:
        # method: a field read and a field tuple element are owned.
        y = self.s  # tpyc: type(str) ok
        z = self.b  # tpyc: type(bytes) ok
        e = self.t[0]  # tpyc: type(str) ok
        eb = self.t[1]  # tpyc: type(bytes) ok
        self.s = mk_s(90)
        self.b = mk_b(90)
        self.t = (mk_s(91), mk_b(91))
        print("method", y, z, e, eb)


def split(url: str) -> tuple[str, str]:
    return (url[:12], url[12:])


def split_b(url: bytes) -> tuple[bytes, bytes]:
    return (url[:12], url[12:])


def pick(h: H) -> tuple[Rec, str]:
    return (h.rec, h.s)


def sec_static() -> None:
    # static storage: a literal, a Final global, an enum member's name, a
    # slice of a str literal; a bytes literal's slice renders over a
    # temporary span, so it owns.
    a = "literal"  # tpyc: type(StrView) ok
    ab = b"literal"  # tpyc: type(BytesView) ok
    g = GREETING  # tpyc: type(StrView) ok
    nm = Color.RED.name  # tpyc: type(StrView) ok
    sl = "xalpha"[1:]  # tpyc: type(StrView) ok
    bl = b"xbytes"[1:]  # tpyc: type(bytes) ok
    print("static", a, ab, g, nm, sl, bl)


def sec_param(s: str, b: bytes, o: str | None, ob: bytes | None) -> None:
    # a view-family parameter lends; a narrowed `bytes | None` owns its buffer.
    v = s  # tpyc: type(StrView) ok
    w = b  # tpyc: type(BytesView) ok
    if o is not None and ob is not None:
        vo = o  # tpyc: type(StrView) ok
        wo = ob  # tpyc: type(bytes) ok
        print("param narrowed", vo, wo)
    print("param", v, w)


def sec_slice(s: str, b: bytes) -> None:
    # a slice over a str/bytes name, param or owned local.
    v = s[1:]  # tpyc: type(StrView) ok
    w = b[1:]  # tpyc: type(BytesView) ok
    ls = mk_s(3)
    lb = mk_b(3)
    v2 = ls[1:]  # tpyc: type(StrView) ok
    w2 = lb[1:]  # tpyc: type(BytesView) ok
    print("slice", v, w, v2, w2)


def sec_method(s: str, b: bytes) -> None:
    # a view-returning method over a str/bytes name; a String receiver lends
    # too, and the `+=` that follows is the write that demotes its view.
    v = s.strip()  # tpyc: type(StrView) ok
    w = b.strip()  # tpyc: type(BytesView) ok
    st = String()
    st += "  mutable-string-receiver-long-payload  "
    x = st.strip()  # tpyc: type(str) ok
    st += "more"
    print("method-view", v, w, x)


def sec_tuple_name(t: tuple[str, bytes]) -> None:
    # an element of a tuple NAME, by index and by unpack.
    a = t[0]  # tpyc: type(StrView) ok
    c = t[1]  # tpyc: type(BytesView) ok
    x, y = t  # tpyc: type(StrView) ok
    print("tuple-name", a, c, x, y)


def sec_field() -> None:
    # a record field read binds owned storage; the record is rewritten after.
    h = H(1)
    y = h.s  # tpyc: type(str) ok
    z = h.b  # tpyc: type(bytes) ok
    h.s = mk_s(2)
    h.b = mk_b(2)
    print("field", y, z)
    # owned even when nothing writes the record afterwards.
    h2 = H(12)
    y2 = h2.s  # tpyc: type(str) ok
    z2 = h2.b  # tpyc: type(bytes) ok
    print("field ro", y2, z2)
    h.method()


def sec_container(i: int32) -> None:
    # a container element binds owned storage; the container is rewritten after.
    names = [mk_s(1), mk_s(2)]
    blobs = [mk_b(1), mk_b(2)]
    y = names[i]  # tpyc: type(str) ok
    z = blobs[i]  # tpyc: type(bytes) ok
    names.clear()
    names.append(mk_s(9))
    blobs.clear()
    blobs.append(mk_b(9))
    print("container", y, z)
    # owned even when nothing writes the container afterwards.
    ro = [mk_s(3)]
    rob = [mk_b(3)]
    y2 = ro[0]  # tpyc: type(str) ok
    z2 = rob[0]  # tpyc: type(bytes) ok
    print("container ro", y2, z2)


def sec_nested() -> None:
    # an element of a tuple held in a list binds owned storage.
    xs: list[tuple[str, bytes]] = []
    xs.append((mk_s(1), mk_b(1)))
    y = xs[0][0]  # tpyc: type(str) ok
    z = xs[0][1]  # tpyc: type(bytes) ok
    xs.clear()
    xs.append((mk_s(7), mk_b(7)))
    print("nested", y, z)


def sec_unpack() -> None:
    # an rvalue tuple unpack moves its str/bytes elements out of the holder.
    host, port = split(mk_s(1))  # tpyc: type(str) ok
    hb, pb = split_b(mk_b(1))  # tpyc: type(bytes) ok
    print("unpack", host, port, hb, pb)
    # a reused target is moved into too.
    host2 = "seed"
    host2, port2 = split(mk_s(2))  # tpyc: ok
    print("unpack reused", host2, port2)
    # a borrowed record sibling keeps aliasing its source.
    h = H(5)
    r, nm = pick(h)  # tpyc: ok
    r.n = 55
    h.s = mk_s(56)
    print("unpack sibling", r.n, h.rec.n, nm)


def sec_loop_tuple() -> None:
    # an element of a loop variable binds owned storage.
    xs: list[tuple[str, bytes]] = []
    xs.append((mk_s(1), mk_b(1)))
    keep = ""  # tpyc: type(str)
    keepb = b""  # tpyc: type(bytes)
    for p in xs:
        keep = p[0]  # tpyc: ok
        keepb = p[1]  # tpyc: ok
    xs.clear()
    xs.append((mk_s(8), mk_b(8)))
    print("loop-tuple", keep, keepb)


def sec_loop_name() -> None:
    # a loop variable is a view per iteration but never lends to a local.
    names = [mk_s(1)]
    blobs = [mk_b(1)]
    k = ""  # tpyc: type(str)
    k2 = ""  # tpyc: type(str)
    k3 = ""  # tpyc: type(str)
    kb = b""  # tpyc: type(bytes)
    for n in names:  # tpyc: type(StrView)
        k = n  # tpyc: ok
        k2 = n[1:]  # tpyc: ok
        k3 = n.strip()  # tpyc: ok
    for bb in blobs:  # tpyc: type(BytesView)
        kb = bb  # tpyc: ok
    pairs: list[tuple[str, bytes]] = []
    pairs.append((mk_s(4), mk_b(4)))
    q = ""  # tpyc: type(str)
    qb = b""  # tpyc: type(bytes)
    for ps, pbb in pairs:  # tpyc: type(StrView)
        q = ps  # tpyc: ok
        qb = pbb  # tpyc: ok
    names.clear()
    names.append(mk_s(9))
    blobs.clear()
    blobs.append(mk_b(9))
    pairs.clear()
    pairs.append((mk_s(5), mk_b(5)))
    print("loop-name", k, k2, k3, kb, q, qb)


def sec_walrus() -> None:
    # a walrus rebind of the source name demotes the view.
    s = mk_s(1)
    b = mk_b(1)
    v = s[:]  # tpyc: type(str) ok
    w = b[:]  # tpyc: type(bytes) ok
    print("walrus new", (s := mk_s(2)), (b := mk_b(2)))
    print("walrus", v, w)


def sec_param_rebind(s: str, b: bytes) -> None:
    # a rebind of the source parameter demotes the view.
    v = s  # tpyc: type(str) ok
    w = b  # tpyc: type(bytes) ok
    s = mk_s(4)
    b = mk_b(4)
    print("param-rebind", v, w, s, b)


def gen(s: str, h: H) -> Iterator[str]:
    # generator: bound before a yield, read after it.
    v = s[1:]  # tpyc: ok
    y = h.s  # tpyc: type(str) ok
    z = h.b  # tpyc: type(bytes) ok
    yield "gen-start"
    h.s = mk_s(70)
    yield v
    yield y
    yield str(len(z))


async def coro(h: H) -> int32:
    # async: bound before an await, read after it.
    y = h.s  # tpyc: type(str) ok
    z = h.b  # tpyc: type(bytes) ok
    await asyncio.sleep(0)
    h.s = mk_s(80)
    print("async", y, z)
    return len(y)


def sec_closure() -> None:
    # closure: a nested def reads the outer local.
    h = H(3)
    y = h.s  # tpyc: type(str) ok
    z = h.b  # tpyc: type(bytes) ok

    def show() -> None:
        print("closure", y, z)

    h.s = mk_s(33)
    show()


def sec_comprehension() -> None:
    # comprehension: element reads of tuples held in a list are owned.
    pairs: list[tuple[str, bytes]] = []
    pairs.append((mk_s(1), mk_b(1)))
    firsts = [p[0] for p in pairs]  # tpyc: ok
    seconds = [p[1] for p in pairs]  # tpyc: ok
    pairs.clear()
    pairs.append((mk_s(6), mk_b(6)))
    print("comprehension", firsts, seconds)


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def sec_with() -> None:
    # with body: a field read bound inside the block, used after it.
    h = H(4)
    with Ctx():
        y = h.s  # tpyc: type(str) ok
        z = h.b  # tpyc: type(bytes) ok
    h.s = mk_s(44)
    h.b = mk_b(44)
    print("with", y, z)


def sec_try() -> None:
    # try/finally: container elements bound in the try body.
    names = [mk_s(1)]
    blobs = [mk_b(1)]
    try:
        y = names[0]  # tpyc: type(str) ok
        z = blobs[0]  # tpyc: type(bytes) ok
    finally:
        names.clear()
        blobs.clear()
    print("try", y, z)


class P:
    u: tuple[Rec, str]
    ub: tuple[Rec, bytes]

    def __init__(self, n: int32) -> None:
        self.u = (Rec(n), mk_s(n))
        self.ub = (Rec(n), mk_b(n))


def bump(p: P) -> None:
    p.u = (Rec(0), mk_s(0))
    p.ub = (Rec(0), mk_b(0))


def sec_tuple_name_over_field() -> None:
    # a tuple NAME borrowing a record field: its element is a field read, so
    # it owns, whatever reaches the field afterwards (a mutable argument, a
    # field write, a write through an alias).
    p = P(1)
    u = p.u
    ub = p.ub
    y = u[1]  # tpyc: type(str) ok
    z = ub[1]  # tpyc: type(bytes) ok
    bump(p)
    print("tuple-field arg", y, z)
    q = P(2)
    qu = q.u
    qub = q.ub
    y2 = qu[1]  # tpyc: type(str) ok
    z2 = qub[1]  # tpyc: type(bytes) ok
    # the warnings are the tuple aliases `qu` / `qub`: they still hold a loan
    # on the field they reference, which the write does invalidate.
    q.u = (Rec(3), mk_s(3))  # tpyc: warning(/while borrowed/)
    q.ub = (Rec(3), mk_b(3))  # tpyc: warning(/while borrowed/)
    print("tuple-field write", y2, z2)
    r = P(4)
    ru = r.u
    rub = r.ub
    y3 = ru[1]  # tpyc: type(str) ok
    z3 = rub[1]  # tpyc: type(bytes) ok
    g = r
    g.u = (Rec(5), mk_s(5))
    g.ub = (Rec(5), mk_b(5))
    print("tuple-field alias", y3, z3)


def sec_block_local() -> None:
    # a local declared before a block, rebound there to a slice of a local
    # of the block, outlives that local: it owns.
    head = ""  # tpyc: type(str)
    headb = b""  # tpyc: type(bytes)
    if len(head) == 0:
        body = mk_s(1)
        bodyb = mk_b(1)
        head = body[2:]
        headb = bodyb[2:]
    tail = ""  # tpyc: type(str)
    tailb = b""  # tpyc: type(bytes)
    j = 0
    while j < 2:
        tmp = mk_s(j)
        tmpb = mk_b(j)
        tail = tmp[3:]
        tailb = tmpb[3:]
        j += 1
    print("block-local", head, headb, tail, tailb)


def sec_match() -> None:
    # match arm: a capture of a str field, rewritten after the bind (a bytes
    # field capture is not lowered yet).
    h = H(6)
    match h:
        case H(s=cs):  # tpyc: ok
            h.s = mk_s(66)
            print("match", cs)


def sec_method_local() -> None:
    # a view-returning method over a LOCAL root lends (the local outlives the
    # binding); over a temporary it owns; a rebind of the root demotes.
    a = mk_s(1)
    v = a.strip()  # tpyc: type(StrView) ok
    w = mk_s(2).strip()  # tpyc: type(str) ok
    b = mk_s(3)
    x = b.strip()  # tpyc: type(str) ok
    b = mk_s(4)
    ab = mk_b(1)
    vb = ab.strip()  # tpyc: type(BytesView) ok
    wb = mk_b(2).strip()  # tpyc: type(bytes) ok
    bb = mk_b(3)
    xb = bb.strip()  # tpyc: type(bytes) ok
    bb = mk_b(4)
    print("method-local", v, w, x, vb, wb, xb)


def sec_mutable_name(ba: bytearray, ba2: bytearray) -> None:
    # a String / bytearray NAME lends like a str one; a subscript store or a
    # mutating method on the name demotes the view, so the slice stays
    # CPython's copy wherever the name is written afterwards.
    st = String("  mutable-string-unwritten-long-payload  ")
    x = st.strip()  # tpyc: type(StrView) ok
    v = ba[1:]  # tpyc: type(BytesView) ok
    w = ba2[1:]  # tpyc: type(bytes) ok
    ba2[1] = 90
    # a bytearray slice prints as bytearray(...) in CPython and b'...' here
    # (a stub repr gap), so the values are compared, not printed
    print("mutable-name", x, v == b"bcdef", w == b"bcdef", len(v), len(w))


class CtorViews:
    n: int32

    def __init__(self, s: str, b: bytes, h: H) -> None:
        # constructor: a parameter slice lends; a field read owns.
        v = s[1:]  # tpyc: type(StrView) ok
        w = b[1:]  # tpyc: type(BytesView) ok
        y = h.s  # tpyc: type(str) ok
        z = h.b  # tpyc: type(bytes) ok
        h.s = mk_s(95)
        h.b = mk_b(95)
        self.n = len(v)
        print("ctor", v, w, y, z)


def sec_select_rebound_param(a: str, b: str, c: bytes, d: bytes) -> None:
    # an `or` select over a parameter the body rebinds after the bind owns.
    b = mk_s(1)
    v = a or b  # tpyc: type(str) ok
    b = mk_s(2)
    d = mk_b(1)
    w = c or d  # tpyc: type(bytes) ok
    d = mk_b(2)
    print("select-rebound", v, w)


def sec_loop_order(n: int32) -> None:
    # a write that precedes the bind in source order still reaches the view
    # on the loop's next pass: the view owns.
    ba = bytearray(b"abcd")
    v = b"x"
    s = mk_s(0)
    w = "x"
    for i in range(n):
        for j in range(40):
            ba.append(65)
        s = s.upper() + str(i) * 40
        print("loop-order", bytes(v), w)
        v = ba[0:4]  # tpyc: type(bytes) ok
        w = s[0:4]  # tpyc: type(str) ok


def sec_block_clauses(p: str, pb: bytes, n: int32) -> None:
    # a local of a loop's `else` clause or of an `except` handler dies with
    # that block: a view declared before it and rebound there owns.
    v = p[0:1]
    vb = pb[0:1]
    for i in range(n):
        pass
    else:
        body = mk_s(n)
        bodyb = mk_b(n)
        v = body[0:4]  # tpyc: type(str) ok
        vb = bodyb[0:4]  # tpyc: type(bytes) ok
    w = p[0:1]
    k = 0
    while k < n:
        k += 1
    else:
        body2 = mk_s(k)
        w = body2[0:4]  # tpyc: type(str) ok
    x = p[0:1]
    xb = pb[0:1]
    try:
        raise ValueError("x")
    except ValueError:
        body3 = mk_s(3)
        body3b = mk_b(3)
        x = body3[0:4]  # tpyc: type(str) ok
        xb = body3b[0:4]  # tpyc: type(bytes) ok
    print("block-clauses", v, vb, w, x, xb)


def sec_rebind_spellings() -> None:
    # an unpack target or a match capture that rebinds the root is a rebind
    # like an assignment: the view over it owns.
    s = mk_s(1)
    v = s[1:]  # tpyc: type(str) ok
    s, rest = split(mk_s(2))
    b = mk_b(1)
    vb = b[1:]  # tpyc: type(bytes) ok
    b, restb = split_b(mk_b(2))
    t = mk_s(3)
    w = t[1:]  # tpyc: type(str) ok
    match mk_s(4):
        case t:
            pass
    print("rebind-spellings", v, vb, w, rest, restb)


def sec_loop_var_block_container(a: str, ab: bytes, c: bool) -> None:
    # a loop variable over a container declared in an enclosing block views
    # that container's element: it stays a view.
    merged: list[str] = []
    mergedb: list[bytes] = []
    if c:
        merged.append("z")
    else:
        xs = a.split("/")
        for p in xs:  # tpyc: type(StrView)
            merged.append(p)
        bs = ab.split(b"/")
        for q in bs:  # tpyc: type(BytesView)
            mergedb.append(q)
    print("loop-var-block", merged, mergedb)


def sec_tuple_alias_closure() -> None:
    # an element of a tuple NAME that borrows a record field owns, so a
    # closure that rewrites the field does not reach it.
    p = P(1)
    u = p.u
    ub = p.ub
    y = u[1]  # tpyc: type(str) ok
    z = ub[1]  # tpyc: type(bytes) ok

    def w() -> None:
        p.u = (Rec(5), mk_s(5))
        p.ub = (Rec(5), mk_b(5))

    w()
    junk = [mk_b(i) for i in range(4)]
    print("tuple-alias-closure", y, z, len(junk))


def poke(b: bytearray) -> None:
    b[3] = 88


class BaHolder:
    ba: bytearray

    def __init__(self, ba: bytearray) -> None:
        # scaffolding: a field stores its own bytearray, so the parameter is
        # copied in -- the warning says so and is not under test.
        self.ba = ba


def sec_indirect_writes(ba: bytearray, h: BaHolder) -> None:
    # a write the source NAME does not spell -- through a local alias, a
    # callee's parameter or the field the name borrows -- owns the view too.
    v = ba[1:]  # tpyc: type(bytes) ok
    al = ba
    al[1] = 90
    w = ba[2:]  # tpyc: type(bytes) ok
    poke(ba)
    hb = h.ba
    x = hb[1:]  # tpyc: type(bytes) ok
    h.ba[1] = 89
    print("indirect-writes", v == b"bcdef", w == b"cdef", x == b"bcdef")


def grow(b: bytearray) -> None:
    for i in range(100):
        b.append(65)


def sec_call_write_loop(ba: bytearray, bb: bytearray) -> None:
    # a callee or a local alias writes the name earlier in the loop body than
    # the bind: the pre-scan knows the name is written somewhere, so the view
    # owns from the first bind, whatever the order inside the body.
    v = b"x"
    for i in range(3):
        grow(ba)
        print("call-write-loop arg", bytes(v))
        v = ba[0:4]  # tpyc: type(bytes) ok
    al = bb
    w = b"y"
    for i in range(3):
        for j in range(100):
            al.append(65)
        print("call-write-loop alias", bytes(w))
        w = bb[0:4]  # tpyc: type(bytes) ok


class PS:
    u: tuple[str, Rec]
    ub: tuple[bytes, Rec]

    def __init__(self, n: int32) -> None:
        self.u = (mk_s(n), Rec(n))
        self.ub = (mk_b(n), Rec(n))


def sec_tuple_unpack_over_field() -> None:
    # an unpack of a tuple NAME that borrows a record field: its str / bytes
    # targets are field reads, so they own across the field write.
    q = PS(1)
    qu = q.u
    qub = q.ub
    y, r0 = qu  # tpyc: type(str) ok
    z, rb0 = qub  # tpyc: type(bytes) ok
    # the warnings are the loans `qu` / `qub` still hold on the fields.
    q.u = (mk_s(7), Rec(7))  # tpyc: warning(/while borrowed/)
    q.ub = (mk_b(7), Rec(7))  # tpyc: warning(/while borrowed/)
    print("tuple-unpack-field", y, z)
    # beside a record element too; that element is not printed, it still sees
    # the rewrite (BUGS.md#tuple-field-alias-record-element-sees-rewrite).
    p = P(1)
    u = p.u
    ub = p.ub
    r, y2 = u  # tpyc: ok
    rb, z2 = ub  # tpyc: ok
    # the warnings are the loans `u` / `ub` still hold on the fields.
    p.u = (Rec(7), mk_s(7))  # tpyc: warning(/while borrowed/)
    p.ub = (Rec(7), mk_b(7))  # tpyc: warning(/while borrowed/)
    print("tuple-unpack-field rec", y2, z2)


def split_st(n: int32) -> tuple[String, str]:
    return (String(mk_s(n)), mk_s(n + 1))


def sec_string_elem_move() -> None:
    # a `String` element of an rvalue tuple moves out of the holder.
    a, rest = split_st(1)  # tpyc: ok
    a += "!"
    print("string-elem-move", a, rest)


def sec_alias_spellings(c: bool) -> None:
    # a write through an alias of the root, whatever binds the alias, reaches
    # a view over the root on the loop's next pass: each view owns, so each
    # pass prints CPython's value, never a freed buffer.
    ba = bytearray(b"abcd")
    v = b"x"
    for i in range(2):
        for j in range(100):
            (t := ba).append(65)
        print("alias walrus", bytes(v))
        v = ba[0:4]  # tpyc: type(bytes) ok
    ba2 = bytearray(b"abcd")
    bb2 = bytearray(b"efgh")
    v2 = b"x"
    for i in range(2):
        t2 = ba2 if c else bb2
        for j in range(100):
            t2.append(65)
        print("alias ternary", bytes(v2))
        v2 = ba2[0:4]  # tpyc: type(bytes) ok
    ba3 = bytearray(b"abcd")

    def grow3() -> None:
        t3 = ba3
        for j in range(100):
            t3.append(65)

    v3 = b"x"
    for i in range(2):
        grow3()
        print("alias nested-def", bytes(v3))
        v3 = ba3[0:4]  # tpyc: type(bytes) ok
    ba4 = bytearray(b"abcd")
    bb4 = bytearray(b"efgh")
    v4 = b"x"
    for i in range(2):
        for j in range(100):
            (ba4 if c else bb4).append(65)
        print("alias ternary-receiver", bytes(v4))
        v4 = ba4[0:4]  # tpyc: type(bytes) ok
    ba5 = bytearray(b"abcd")
    bb5 = bytearray(b"efgh")
    v5 = b"x"
    t5 = bb5
    for i in range(2):
        t5 = ba5
        for j in range(100):
            t5.append(65)
        print("alias rebound", bytes(v5))
        v5 = ba5[0:4]  # tpyc: type(bytes) ok


class BaMeth:
    ba: bytearray

    def __init__(self, ba: bytearray) -> None:
        self.ba = ba  # copies into the field (the warning is the scaffolding's)

    def meth(self) -> None:
        # a bytearray name bound from a field is a reference into the field, so
        # it lends nothing: a write through the field spelling before the bind
        # in the loop would not reach the name
        t = self.ba
        v = b"x"
        for i in range(3):
            for j in range(100):
                self.ba.append(65)
            print("field-alias-self", v == b"x" or v == b"abcd")
            v = t[0:4]  # tpyc: type(bytes) ok


def sec_field_alias_loop(h: BaMeth) -> None:
    t = h.ba
    v = b"x"
    for i in range(3):
        for j in range(100):
            h.ba.append(65)
        print("field-alias", v == b"x" or v == b"abcd")
        v = t[0:4]  # tpyc: type(bytes) ok
    h.meth()


GBA = bytearray(b"abcd")


def get_gba() -> bytearray:
    return GBA


def sec_global_getter() -> None:
    # a bytearray name bound from a call that returns a borrow (of a global
    # here) does not own its object, so it lends nothing: the write through
    # the global before the bind in the loop would not reach the name
    t = get_gba()
    v = b"x"
    for i in range(3):
        for j in range(100):
            GBA.append(65)
        print("global-getter", v == b"x" or v == b"abcd")
        v = t[0:4]  # tpyc: type(bytes) ok


# module level: a slice of a global keeps the slice's own view type (no
# deduction runs at module scope); a field read is a copy.
mod_s = mk_s(1)
mod_b = mk_b(1)
mod_v = mod_s[1:]  # tpyc: type(StrView) ok
mod_w = mod_b[1:]  # tpyc: type(BytesView) ok
mod_h = H(2)
mod_y = mod_h.s  # tpyc: type(str) ok
mod_z = mod_h.b  # tpyc: type(bytes) ok
mod_h.s = mk_s(3)
mod_h.b = mk_b(3)
print("module", mod_v, mod_w, mod_y, mod_z)


def main() -> None:
    sec_static()
    sec_param("p", b"q", "o", b"ob")
    sec_slice("slice", b"slice")
    sec_method("  m  ", b"  m  ")
    sec_tuple_name(("tn", b"tn"))
    sec_field()
    sec_container(1)
    sec_nested()
    sec_unpack()
    sec_loop_tuple()
    sec_loop_name()
    sec_walrus()
    sec_param_rebind("ps", b"pb")
    for x in gen("gs", H(7)):
        print("gen", x)
    n = asyncio.run(coro(H(8)))
    print("async ret", n)
    sec_closure()
    sec_comprehension()
    sec_with()
    sec_try()
    sec_tuple_name_over_field()
    sec_block_local()
    sec_match()

    ba1 = bytearray(b"abcdef")
    ba2 = bytearray(b"abcdef")
    sec_mutable_name(ba1, ba2)
    sec_method_local()
    CtorViews("ctor", b"ctor", H(9))
    sec_select_rebound_param("", "x", b"", b"y")
    sec_loop_order(3)
    sec_block_clauses("xyz", b"xyz", 2)
    sec_rebind_spellings()
    sec_loop_var_block_container("a/b", b"c/d", False)
    sec_tuple_alias_closure()
    sec_indirect_writes(bytearray(b"abcdef"), BaHolder(bytearray(b"abcdef")))
    sec_call_write_loop(bytearray(b"abcd"), bytearray(b"abcd"))
    sec_field_alias_loop(BaMeth(bytearray(b"abcd")))
    sec_global_getter()
    sec_tuple_unpack_over_field()
    sec_string_elem_move()
    sec_alias_spellings(True)


main()
