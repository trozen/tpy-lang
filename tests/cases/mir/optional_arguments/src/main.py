# MIR pins for Optional parameters (`p: Q | None`, `k: int32 | None`): known callee summaries, and every call kind
# binding an Optional argument. Witness sections are never run: each reads storage the callee may have replaced.
from tpy import int32


class Q:
    v: int32

    def __init__(self, v: int32) -> None:  # tpyc: mir(covered)
        self.v = v

    def get(self) -> int32:
        return self.v


class P:
    v: int32
    items: list[int32]

    def __init__(self, v: int32) -> None:
        self.v = v
        self.items = []


class H:
    p: Q | None
    n: int32

    # constructor callee: an Optional record parameter's payload copied into the field
    def __init__(self, p: Q | None) -> None:  # tpyc: mir(covered)
        self.p = p  # tpyc: warning(/copies Q \| None into field/)
        self.n = 1

    # method callee: a payload read
    def peek(self, p: Q | None) -> int32:  # tpyc: mir(covered) mir_summary(known)
        if p is not None:
            return p.v + self.n
        return self.n

    # method callee: the holder copied into a field of self
    def store(self, p: Q | None) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.p = p  # tpyc: warning(/copies Q \| None into field/)

    # witness callee (receiver form): self.p emptied, then the parameter's payload read
    def clear_read(self, p: Q | None) -> int32:  # tpyc: mir(conflict /alias_obligation/)
        self.p = None
        if p is not None:
            return p.v
        return 0


# callee: a presence test on the parameter holder
def present(p: Q | None) -> bool:  # tpyc: mir(covered) mir_summary(known)
    return p is not None


# callee: a payload read
def peek(p: Q | None) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if p is not None:
        return p.v
    return -1


# callee: a payload write the caller observes
def bump(p: Q | None) -> None:  # tpyc: mir(covered) mir_summary(known)
    if p is not None:
        p.v += 1


# callee: the holder copied into a field
def put(h: H, p: Q | None) -> None:  # tpyc: mir(covered) mir_summary(known)
    h.p = p  # tpyc: warning(/copies Q \| None into field/)


# caller, free function: None, a name, an Optional field, a narrowed name
def call_free(q: Q, h: H) -> int32:  # tpyc: mir(covered)
    n = peek(None) + peek(q) + peek(h.p)  # the Optional arguments under test
    o = h.p
    if o is not None:
        n += peek(o)  # a narrowed name passes its holder's pointer
    return n


# caller, free function: a temporary built for the call
def call_free_temp() -> int32:  # tpyc: mir(covered)
    return peek(Q(4))


# caller, method: the same shapes (a temporary there is a THIR reject, BUGS.md#batch7-everyday-arg-rejects)
def call_method(q: Q, h: H) -> int32:  # tpyc: mir(covered)
    n = h.peek(None) + h.peek(q) + h.peek(h.p)  # the Optional arguments under test
    o = h.p
    if o is not None:
        n += h.peek(o)  # a narrowed name
    return n


# caller, constructor: the same shapes
def call_ctor(q: Q, h: H) -> int32:  # tpyc: mir(covered)
    a = H(None)  # the Optional arguments under test
    b = H(q)
    c = H(h.p)
    n = peek(a.p) + peek(b.p) + peek(c.p)
    o = h.p
    if o is not None:
        e = H(o)  # a narrowed name
        n += peek(e.p)
    return n


# a temporary at a constructor's Optional parameter, bound as any call's argument
def call_ctor_temp() -> int32:  # tpyc: mir(certified)
    d = H(Q(4))
    return d.n


# caller: the payload write lands in the caller's record, through a name and an Optional field
def call_bump(q: Q, h: H) -> int32:  # tpyc: mir(covered)
    bump(q)  # the record argument the callee writes
    bump(h.p)  # the field payload the callee writes
    return q.v * 100 + peek(h.p)


# callee: a result that may borrow the payload's list
def items_or(p: P | None, fallback: list[int32]) -> list[int32]:  # tpyc: mir(covered) mir_summary(known)
    if p is not None:
        return p.items
    return fallback


# caller: None to callees writing through the payload or returning from it; the absent argument reaches nothing
def call_none(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    bump(None)  # the absent arguments under test
    grow(None)
    return peek(None) + len(items_or(None, xs))


# caller: the result borrows the present payload's list or the fallback; with None, only the fallback
def call_items(o: P | None, xs: list[int32]) -> int32:  # tpyc: mir(covered)
    r = items_or(o, xs)  # tpyc: mir_borrows(r, o[payload].items|xs)
    s = items_or(None, xs)  # tpyc: mir_borrows(s, xs)
    return len(r) * 10 + len(s)


# caller: presence tests and a holder copy
def call_holder(q: Q, h: H) -> int32:  # tpyc: mir(covered)
    n = 0
    if present(q) and not present(None):  # the presence-test callee
        n += 1
    put(h, q)  # the holder-copy callee
    h.store(None)
    return n + peek(h.p)


# scalar callee: an `int32 | None` parameter is the callee's own value
def twice(k: int32 | None) -> int32:  # tpyc: mir(covered) mir_summary(known)
    if k is not None:
        return k * 2
    return 0


def plain(k: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return k + 1


def read_q(q: Q) -> int32:
    return q.v


# scalar caller: None, a plain value, an Optional name, a narrowed name to both parameter kinds
def call_scalar(k: int32 | None, z: int32) -> int32:  # tpyc: mir(covered)
    n = twice(None) + twice(z) + twice(k)  # the scalar Optional arguments under test
    if k is not None:
        n += twice(k) + plain(k)  # a narrowed scalar name
    return n


# scalar caller: an Optional temporary of a loop's full expressions, the while condition and the for body
def scalar_loops(k: int32 | None) -> int32:  # tpyc: mir(covered)
    n = 0
    while twice(k) > n:  # built once per pass of the condition
        n += 1
    for i in range(3):
        n += twice(i)  # built once per iteration
    return n


# caller: a union narrowed to its scalar alternative passed to a plain scalar parameter
def via_scalar(x: int32 | bool) -> int32:  # tpyc: mir(covered)
    if isinstance(x, bool):
        return 0
    return plain(x)  # the narrowed union under test


# caller: a narrowed Optional passed to a plain record parameter and as a method receiver
def narrowed_plain(o: Q | None) -> int32:  # tpyc: mir(covered)
    if o is not None:
        return read_q(o) + o.get()  # the narrowed Optional under test
    return 0


# constructor callee: a scalar Optional parameter stored in a scalar Optional field (kept refusal: the field)
class S:
    k: int32 | None

    def __init__(self, k: int32 | None) -> None:  # tpyc: mir(uncovered /unsupported record fields/)
        self.k = k


def call_scalar_ctor() -> int32:  # tpyc: mir(uncovered /unsupported record fields/)
    a = S(5)  # a value and None at the scalar Optional parameter
    b = S(None)
    n = 0
    if a.k is not None:
        n += a.k
    if b.k is not None:
        n += b.k
    return n


# constructor callee: a scalar Optional parameter no field stores, so a caller constructs through it
class U:
    n: int32

    def __init__(self, n: int32, k: int32 | None) -> None:  # tpyc: mir(covered)
        self.n = n


def call_scalar_ctor_arg(z: int32 | None) -> int32:  # tpyc: mir(covered)
    a = U(1, 5)  # a value, None and an Optional name at the scalar Optional parameter
    b = U(2, None)
    c = U(3, z)
    return a.n + b.n + c.n


# witness: a and b may be one record; b grows the list a's loop iterates
def grow_both(a: P | None, b: P | None) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    n = 0
    if a is not None and b is not None:
        for x in a.items:
            b.items.append(1)
            n += x
    return n


def grow_both_call(p: P) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return grow_both(p, p)  # tpyc: warning(/argument 'p' contains 'p.items', which 'grow_both' modifies through parameter 'b' while it still uses 'a'/)


# witness: a and b may be one holder; emptying b.p ends the payload a.p's holder points at
def replace_holder(a: H | None, b: H | None) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    if a is not None and b is not None:
        o = a.p
        b.p = None
        if o is not None:
            return o.v
    return 0


def replace_holder_call(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return replace_holder(h, h)  # tpyc: warning(/argument 'h' contains 'h.p', which 'replace_holder' modifies through parameter 'b' while it still uses 'a'/)


# witness: the callee empties the field its Optional parameter points into, then reads the payload
def clear_read(p: Q | None, h: H) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.p = None
    if p is not None:
        return p.v
    return 0


def clear_read_call(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return clear_read(h.p, h)  # tpyc: warning(/argument 'h.p' is what 'clear_read' modifies through parameter 'h' while it still uses 'p'/)


# witness, receiver form: the method empties self.p while it reads the payload h.p points at
def clear_read_method_call(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return h.clear_read(h.p)  # tpyc: warning(/argument 'h.p' is what 'H.clear_read' modifies through parameter 'self' while it still uses 'p'/)


# witness: the callee replaces the field in place, then reads the old payload
def replace_read(p: Q | None, h: H) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    h.p = Q(9)
    if p is not None:
        return p.v
    return 0


def replace_read_call(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return replace_read(h.p, h)  # tpyc: warning(/argument 'h.p' is what 'replace_read' modifies through parameter 'h' while it still uses 'p'/)


# witness: a relay's obligation reaches the relay's callers
def relay(p: Q | None, h: H) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    return clear_read(p, h)


def relay_call(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return relay(h.p, h)  # tpyc: warning(/argument 'h.p' is what 'relay' modifies through parameter 'h' while it still uses 'p'/)


# stay silent: None, or a record of the caller's own, aliases nothing h holds
def clear_read_silent(h: H) -> int32:  # tpyc: mir(covered)
    q = Q(3)
    n = clear_read(None, h)  # tpyc: ok
    m = clear_read(q, h)  # tpyc: ok
    return n + m


# witness: an Optional holder parameter written while a record parameter is read
def wr(q: Q, p: H | None) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    if p is not None:
        p.p = None
    return q.v


def wr_call(h: H) -> int32:  # tpyc: mir(conflict /argument_alias/)
    o = h.p
    if o is not None:
        return wr(o, h)  # tpyc: warning(/argument 'o' points into 'h.p', which 'wr' modifies through parameter 'p' while it still uses 'q'/)
    return 0


def grow(p: P | None) -> None:  # tpyc: mir(covered) mir_summary(known)
    if p is not None:
        p.items.append(1)


# kept conflict: growing the list through an Optional callee under a live iterator over it
def grow_under_iter(q: P) -> int32:  # tpyc: mir(conflict /replacement/)
    n = 0
    for x in q.items:
        grow(q)
        n += x
    return n


# safe sibling: grow before iterating
def grow_then_iter(q: P) -> int32:  # tpyc: mir(covered)
    grow(q)
    n = 0
    for x in q.items:
        n += x
    return n


# kept conflict: a holder of the old payload read after put replaced the field
def put_then_read(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    o = h.p
    put(h, None)
    if o is not None:
        return o.v
    return 0


# kept conflict: the same through a method
def store_then_read(h: H) -> int32:  # tpyc: mir(conflict /replacement/)
    o = h.p
    h.store(None)
    if o is not None:
        return o.v
    return 0


# safe sibling: read before put
def read_then_put(h: H) -> int32:  # tpyc: mir(covered)
    o = h.p
    n = 0
    if o is not None:
        n = o.v
    put(h, None)
    return n


class T:
    text: str
    v: int32

    def __init__(self, text: str) -> None:
        self.text = text
        self.v = 1


# callee: replacing b.text reaches no record, so the Optional `a` (whose origin is a T) carries no obligation
def retext(a: T | None, b: T) -> int32:  # tpyc: mir(covered) mir_summary(known)
    b.text = "replaced"
    if a is not None:
        return a.v
    return 0


def retext_call(t: T) -> int32:  # tpyc: mir(covered)
    return retext(t, t)  # tpyc: ok


def lend_text(s: str, b: T) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    b.text = "replaced"
    return len(s)


# witness: a view of the Optional's payload text lent across the replacement of b.text; a and b may be one record
def retext_view(a: T | None, b: T) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    if a is not None:
        return lend_text(a.text, b)
    return 0


def retext_view_call(t: T) -> int32:  # tpyc: mir(conflict /argument_alias/)
    return retext_view(t, t)  # tpyc: warning(/argument 't' contains 't.text', which 'retext_view' modifies through parameter 'b' while it still uses 'a'/)


# kept refusal: a callee returning its payload as a borrowed record
def pick(p: Q | None, q: Q) -> Q:  # tpyc: mir(uncovered /optional_read/)
    if p is not None:
        return p
    return q


def main() -> None:
    print("call_free", call_free(Q(1), H(Q(2))))
    print("call_free_temp", call_free_temp())
    print("call_method", call_method(Q(1), H(Q(2))))
    print("call_ctor", call_ctor(Q(1), H(Q(2))))
    print("call_ctor_temp", call_ctor_temp())
    print("call_bump", call_bump(Q(1), H(Q(5))))
    print("call_none", call_none([1, 2]))
    print("call_items", call_items(P(1), [1, 2]), call_items(None, [1, 2]))
    print("call_holder", call_holder(Q(3), H(None)))
    print("call_scalar", call_scalar(4, 5), call_scalar(None, 5))
    print("scalar_loops", scalar_loops(3), scalar_loops(None))
    print("via_scalar", via_scalar(3), via_scalar(True))
    print("narrowed_plain", narrowed_plain(Q(4)), narrowed_plain(None))
    print("call_scalar_ctor", call_scalar_ctor())
    print("call_scalar_ctor_arg", call_scalar_ctor_arg(7))
    print("clear_read_silent", clear_read_silent(H(Q(8))))
    print("retext_call", retext_call(T("t")))
    print("grow_then_iter", grow_then_iter(P(1)))
    print("read_then_put", read_then_put(H(Q(6))))
    print("pick", pick(None, Q(7)).v)


main()
