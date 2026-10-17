# A rebound, tuple-captured or yielded borrow marks its sources' roots written at
# every position; a tuple bound once from const storage keeps const pointers.
import asyncio
from typing import Iterator
from tpy import int32, error_return, readonly, ReturnException


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    bp: tuple[Box, Box]
    pr: tuple[int32, Box]

    def __init__(self, a: int32) -> None:
        self.bp = (Box(a), Box(a + 1))
        self.pr = (a, Box(a + 2))

    # method: the source is a field of the otherwise unwritten receiver
    def meth(self, m: "H", c: bool) -> int32:
        t = self.bp  # tpyc: ok
        if c:
            t = m.bp
        m.bp[0].n += 1
        return t[0].n


class K:
    n: int32

    # constructor: the source is a field of an otherwise unwritten parameter
    def __init__(self, ro: H, m: H, c: bool) -> None:
        t = ro.bp  # tpyc: ok
        if c:
            t = m.bp
        m.bp[1].n += 1
        self.n = t[1].n


class O:
    f: Box

    def __init__(self, n: int32) -> None:
        self.f = Box(n)

    # method: a tuple local capturing a local alias of a self field takes its
    # address, so the receiver stays writable through the tuple
    def capture_self_alias(self, m: "O") -> None:
        b = self.f
        t = (b, 1)  # tpyc: ok
        t[0].n += 3
        m.f.n += 10
        print("capture_self_alias", t[0].n, b.n, self.f.n)


class Cm:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Bad(Exception, ReturnException):
    pass


# free function, written through: the tuple first holds two locals, then is
# re-pointed at a field of an otherwise unwritten parameter
def written_through(ro: H, m: H) -> None:
    a = Box(1)
    b = Box(2)
    t = (a, b)
    t[0].n += 1
    print("written_through", t[0].n, a.n)
    t = ro.bp  # tpyc: ok
    m.bp[0].n += 5
    print("written_through", t[0].n, a.n, b.n)


# free function, read only: one source unwritten, the other written
def read_only(ro: H, m: H, c: bool) -> int32:
    t = ro.bp  # tpyc: ok
    if c:
        t = m.bp
    m.bp[0].n += 100
    return t[0].n + t[1].n


# nested def: the source is a field of a captured parameter
def nested(ro: H, m: H, c: bool) -> int32:
    def inner() -> int32:
        t = ro.bp  # tpyc: ok
        if c:
            t = m.bp
        m.bp[0].n += 1
        return t[0].n
    return inner()


# loop variable: the tuple is re-pointed at a field of each element
def loop_var(h0: H, ros: list[H], m: H) -> int32:
    t = h0.bp
    s = 0
    for r in ros:
        t = r.bp  # tpyc: ok
        m.bp[0].n += 1
        s += t[0].n
    return s


# loop variable reused: the tuple is re-pointed at a field of the first
# loop's variable, whose name a later loop over an unwritten list reuses;
# the rebind marks the first loop's list written, not the second's
def loop_var_reuse(ros: list[H], others: list[H], m: H) -> int32:
    t = m.bp
    s = 0
    for r in ros:
        t = r.bp  # tpyc: ok
        s += t[0].n
    t[0].n += 1000
    for r in others:
        s += r.bp[1].n
    return s + t[0].n


# try/finally: rebound inside the try body
def in_try(ro: H, m: H, c: bool) -> int32:
    t = m.bp
    try:
        if c:
            t = ro.bp  # tpyc: ok
    finally:
        m.bp[1].n += 1
    return t[1].n


# match arm: rebound inside a case
def in_match(ro: H, m: H, c: int32) -> int32:
    t = m.bp
    match c:
        case 1:
            t = ro.bp  # tpyc: ok
        case _:
            pass
    m.bp[0].n += 1
    return t[0].n


# with body: rebound inside the managed block
def in_with(ro: H, m: H, c: bool) -> int32:
    t = m.bp
    with Cm() as _k:
        if c:
            t = ro.bp  # tpyc: ok
    m.bp[0].n += 1
    return t[0].n


# @error_return body
@error_return(Bad)
def in_error_return(ro: H, m: H, c: bool) -> int32:
    t = m.bp
    if c:
        t = ro.bp  # tpyc: ok
    m.bp[0].n += 1
    if t[0].n < 0:
        raise Bad()
    return t[0].n


# branch-hoisted: first bound in both arms of an if
def hoisted(ro: H, m: H, c: bool) -> int32:
    if c:
        t = ro.bp  # tpyc: ok
    else:
        t = m.bp
    m.bp[0].n += 1
    return t[0].n


# Optional borrow tuple: None, then a field of an otherwise unwritten parameter
def optional(ro: H, m: H, c: bool) -> int32:
    t: tuple[int32, Box] | None = None
    if c:
        t = ro.pr  # tpyc: ok
    m.pr[1].n += 1
    if t is not None:
        return t[1].n
    return 0


# Optional borrow tuple declared from a field, then rebound
def optional_from_field(ro: H, m: H, c: bool) -> int32:
    t: tuple[int32, Box] | None = ro.pr  # tpyc: ok
    if c:
        t = m.pr
    m.pr[1].n += 1
    if t is not None:
        return t[1].n
    return 0


# branch-hoisted inside a loop: bound off the loop variable in one arm
def loop_hoisted(ros: list[H], m: H, c: bool) -> int32:
    s = 0
    for r in ros:
        if c:
            t = r.bp  # tpyc: ok
        else:
            t = m.bp
        m.bp[0].n += 1
        s += t[0].n
    return s


# walrus bound once off an element of an otherwise unwritten list: its element
# pointers stay const, and a write through `m` is observed through it
def walrus_const(rows: list[tuple[int32, Box]], m: list[tuple[int32, Box]]
                 ) -> int32:
    print("walrus", (t := rows[0])[0])  # tpyc: ok
    m[0][1].n += 1
    return t[1].n


# walrus tuple literal over a declared read-only container: its element
# pointer is const, read only
def walrus_lit_ro(nodes: readonly[list[Box]], i: int32) -> int32:
    print("walrus_lit_ro", (t := (i, nodes[i]))[0])  # tpyc: ok
    return t[1].n


# async def: re-pointed by a literal across an await; the first source is a
# field of an otherwise unwritten parameter
async def in_async(ro: O, m: O) -> int32:
    t = (1, ro.f)
    await asyncio.sleep(0)
    t = (2, m.f)  # tpyc: ok
    m.f.n += 1000
    return t[1].n


# generator: a mutable yield of a local alias of a parameter's field keeps
# the parameter writable, as the returned alias does
def yield_field_alias(o: O) -> Iterator[Box]:
    b = o.f
    yield b  # tpyc: ok


# free function: a tuple local capturing a local alias of a parameter's field
# takes its address, so the parameter stays writable; the field is written
# through another name after the capture and read through the tuple
def capture_field_alias(o: O, m: O) -> int32:
    b = o.f
    t = (b, 1)  # tpyc: ok
    m.f.n += 10
    return t[0].n + t[1]


# generator: a frame tuple local capturing a local alias of a parameter's
# field; its write reaches the consumer, the consumer's write reaches it
def gen_capture_alias(o: O) -> Iterator[int32]:
    b = o.f
    t = (b, 1)  # tpyc: ok
    t[0].n += 7
    yield t[0].n
    yield t[0].n + t[1]


def main() -> None:
    x = H(50)
    written_through(x, x)
    r = H(10)
    print("read_only", read_only(r, r, False), read_only(r, H(20), True))
    h = H(30)
    print("meth", h.meth(h, False), h.bp[0].n)
    k = K(h, h, False)
    print("ctor", k.n, h.bp[1].n)
    print("nested", nested(h, h, False), h.bp[0].n)
    hs = [H(1), H(2)]
    print("loop_var", loop_var(h, hs, hs[1]), hs[0].bp[0].n, hs[1].bp[0].n)
    ra = [H(1), H(2)]
    rb = [H(10)]
    print("loop_var_reuse", loop_var_reuse(ra, rb, H(100)), ra[1].bp[0].n,
          rb[0].bp[0].n)
    print("try", in_try(h, h, True), h.bp[1].n)
    print("match", in_match(h, h, 1), h.bp[0].n)
    print("with", in_with(h, h, True), h.bp[0].n)
    try:
        v = in_error_return(h, h, True)
        print("error_return", v, h.bp[0].n)
    except Bad:
        print("error_return raised")
    print("hoisted", hoisted(h, h, True), h.bp[0].n)
    print("optional", optional(h, h, True), optional(h, h, False))
    print("optional_from_field", optional_from_field(h, h, False), h.pr[1].n)
    hs2 = [H(1), H(2)]
    print("loop_hoisted", loop_hoisted(hs2, hs2[1], True), hs2[0].bp[0].n,
          hs2[1].bp[0].n)
    rows = [(1, Box(4))]
    w = walrus_const(rows, rows)
    print("walrus", w, rows[0][1].n)
    v = walrus_lit_ro([Box(3), Box(4)], 1)
    print("walrus_lit_ro", v)
    oa = O(2)
    print("async", asyncio.run(in_async(oa, oa)), oa.f.n)
    o = O(1)
    for y in yield_field_alias(o):
        y.n += 5
    print("yield_field_alias", o.f.n)
    print("capture_field_alias", capture_field_alias(o, o), o.f.n)
    oc = O(1)
    oc.capture_self_alias(oc)
    print("capture_self_alias", oc.f.n)
    og = O(2)
    for gv in gen_capture_alias(og):
        print("gen_capture_alias", gv, og.f.n)
        og.f.n += 100


main()
