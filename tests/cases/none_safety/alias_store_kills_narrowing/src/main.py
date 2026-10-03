# A None store or a mutating call through any alias of an object's inline
# storage kills its field's narrowing; unrelated objects and siblings keep theirs.
from tpy import Own, Ptr, int32


class M:
    v: int | None

    def __init__(self) -> None:
        self.v = 1


class N:
    v: int | None
    w: int | None
    inner: M

    def __init__(self) -> None:
        self.v = 1
        self.w = 1
        self.inner = M()

    def reset(self) -> None:
        self.v = None

    def __enter__(self) -> "N":
        return self

    def __exit__(self, et: None, ev: None, tb: None) -> None:
        pass

    # method: the alias's root is `self`
    def self_alias(self, other: "N", c: bool) -> None:
        s = self if c else other
        if self.v is not None:
            s.v = None
            y = self.v  # tpyc: type(/None/)
            print("self_alias:", y)


class S:
    s: str | None

    def __init__(self) -> None:
        self.s = "hi"


class L:
    v: int | None
    n: int32
    next: Ptr["L"]

    def __init__(self, nxt: Ptr["L"]) -> None:
        self.v = 1
        self.n = 0
        self.next = nxt


class P:
    x: int32

    def __init__(self) -> None:
        self.x = 0

    def shift(self, d: int32) -> None:
        self.x += d


class Item:
    pos: P

    def __init__(self) -> None:
        self.pos = P()


class Holder:
    p: Ptr[P]

    def __init__(self, p: Ptr[P]) -> None:
        self.p = p


def clear(n: N) -> None:
    n.v = None


def clear_m(m: M) -> None:
    m.v = None


def empty_m() -> Own[M]:
    m = M()
    m.v = None
    return m


# ternary alias (bound before the narrowing, so the store is what kills)
def ternary(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        t.v = None
        y = a.v  # tpyc: type(/None/)
        print("ternary:", y)


# or alias
def or_alias(a: N, b: N) -> None:
    t = b or a
    if a.v is not None:
        t.v = None
        y = a.v  # tpyc: type(/None/)
        print("or_alias:", y)


# walrus alias
def walrus(a: N) -> None:
    print("walrus:", (t := a).w)
    if a.v is not None:
        t.v = None
        y = a.v  # tpyc: type(/None/)
        print("walrus:", y)


# nested def: the alias, the narrowing and the store all sit in k()
def nested(a: N, c: bool) -> None:
    def k() -> None:
        t = a if c else N()
        if a.v is not None:
            t.v = None
            y = a.v  # tpyc: type(/None/)
            print("nested:", y)
    k()


# chain alias: t holds a.inner
def chain(a: N) -> None:
    t = a.inner
    if a.inner.v is not None:
        t.v = None
        y = a.inner.v  # tpyc: type(/None/)
        print("chain:", y)


# composed chain: a possibly-None store through the root reaches t = b.inner, b = a
def composed(a: N, opt: int | None) -> None:
    b = a
    t = b.inner
    if t.v is not None:
        a.inner.v = opt
        y = t.v  # tpyc: type(/None/)
        print("composed:", y)


# reverse composed chain: a store through t = b.inner, b = a reaches a.inner
def composed_reverse(a: N) -> None:
    b = a
    t = b.inner
    if a.inner.v is not None:
        t.v = None
        y = a.inner.v  # tpyc: type(/None/)
        print("composed_reverse:", y)


# loop entry: the store after the read reaches the read on the back edge
def loop_entry(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        i = 0
        while i < 2:
            y = a.v  # tpyc: type(/None/)
            print("loop_entry:", y)
            t.v = None
            i += 1


# handler and finally entry: a None store through an alias before a raise reaches both
def handler_finally(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        try:
            t.v = None
            raise ValueError("stop")
        except ValueError:
            y = a.v  # tpyc: type(/None/)
            print("handler_finally except:", y)
            assert a.v is not None
            x = a.v  # tpyc: type(int)
            print("handler_finally re-narrowed:", x + 1)
        finally:
            z = a.v  # tpyc: type(/None/)
            print("handler_finally finally:", z)
        w = a.v  # tpyc: type(int)
        print("handler_finally after:", w + 1)


# len-derived range: a pop through t = xs if c else ys may shrink xs (BUGS.md#range-len-loop-entry-survives-shrink)
def len_range(xs: list[int32], ys: list[int32], c: bool) -> None:
    t = xs if c else ys
    for i in range(len(xs)):
        t.pop()
        print("len_range:", xs[i])  # tpyc: bounds_checked(xs)


# len-derived range kept: an append and a pop through an alias of an unrelated list
def len_range_unrelated(xs: list[int32]) -> None:
    ys = [3]
    t = ys
    for i in range(len(xs)):
        t.append(7)
        t.pop()
        print("len_range_unrelated:", xs[i])  # tpyc: bounds_safe(xs)
    # read through ys after the loop, so t stays an alias of it
    print("len_range_unrelated ys:", len(ys))


# local root instead of a parameter
def local_root(c: bool) -> None:
    a = N()
    t = a if c else N()
    if a.v is not None:
        t.v = None
        y = a.v  # tpyc: type(/None/)
        print("local_root:", y)


# direct None store, no alias
def direct(a: N) -> None:
    if a.v is not None:
        a.v = None
        y = a.v  # tpyc: type(/None/)
        print("direct:", y)


# direct store of a value that may be None
def store_optional(a: N, opt: int | None) -> None:
    if a.v is not None:
        a.v = opt
        y = a.v  # tpyc: type(/None/)
        print("store_optional:", y)


# mutable call argument bound by a ternary (BUGS.md#reference-ternary-position-gaps)
def call_ternary(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.v is not None:
        clear(t)
        y = a.v  # tpyc: type(/None/)
        print("call_ternary:", y)


# keyword call argument bound by a ternary
def call_kwarg(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.v is not None:
        clear(n=t)
        y = a.v  # tpyc: type(/None/)
        print("call_kwarg:", y)


# mutable call argument through a field chain
def call_chain(a: N) -> None:
    if a.inner.v is not None:
        clear_m(a.inner)
        y = a.inner.v  # tpyc: type(/None/)
        print("call_chain:", y)


# method receiver through a ternary
def receiver_ternary(a: N, b: N, c: bool) -> None:
    if a.v is not None:
        (a if c else b).reset()
        y = a.v  # tpyc: type(/None/)
        print("receiver_ternary:", y)


# reassigned alias: h2 is rebound later, a store through it still reaches h
def reassigned(h: N, other: N) -> None:
    h2 = h
    if h.v is not None:
        h2.v = None
        y = h.v  # tpyc: type(/None/)
        print("reassigned:", y)
    h2 = other
    print("reassigned:", h2.w, h.w)


# reassigned alias, method call: h2 is rebound later, h2.reset() reaches h
def reassigned_call(h: N, other: N) -> None:
    h2 = h
    if h.v is not None:
        h2.reset()
        y = h.v  # tpyc: type(/None/)
        print("reassigned_call:", y)
    h2 = other
    print("reassigned_call:", h2.w, h.w)


# kept: a non-None store keeps the field's fact
def kept_non_none(a: N) -> None:
    if a.w is not None:
        a.w = 5
        y = a.w  # tpyc: type(int)
        print("kept_non_none:", y + 1)


# kept: a store of a name that cannot be None keeps the field's fact
def kept_non_none_name(a: N, k: int) -> None:
    if a.w is not None:
        a.w = k
        y = a.w  # tpyc: type(int)
        print("kept_non_none_name:", y + 1)


# kept: an unrelated binding does not touch a.v
def kept_unrelated(a: N) -> None:
    if a.v is not None:
        u = N()
        u.v = None
        y = a.v  # tpyc: type(int)
        print("kept_unrelated:", y + 1)


# kept: a store through t = a.inner leaves the sibling field a.v alone
def kept_sibling(a: N) -> None:
    if a.v is not None:
        t = a.inner
        t.v = None
        y = a.v  # tpyc: type(int)
        print("kept_sibling:", y + 1)


# kept, walk: a store to the sibling field n through the walk variable keeps node.v
def kept_cyclic(head: L) -> None:
    node: Ptr[L] = head
    while node is not None:
        if node.v is not None:
            node.n += 1
            node.n = node.n + 1
            y = node.v  # tpyc: type(int)
            print("kept_cyclic:", y + 1, node.n)
        node = node.next


# a store whose value reads the slot it replaces kills the deref fact that read queued
def queued_deref(h: Holder, q: Ptr[P], c: bool) -> None:
    h.p = q if h.p.x > 0 else h.p
    print("queued_deref direct:", h.p.x)  # tpyc: nullable(h.p)
    t = h if c else Holder(q)
    t.p = q if h.p.x > 5 else h.p
    print("queued_deref alias:", h.p.x)  # tpyc: nullable(h.p)


# plain alias bind with no store: a second write path to a ends a.v's fact
def plain_bind(a: N) -> None:
    if a.v is not None:
        t = a
        y = a.v  # tpyc: type(/None/)
        print("plain_bind:", y, t.w)


# kept, everyday loop: a mutating call on each item's field cannot reach cfg
def everyday_loop(cfg: N, items: list[Item]) -> None:
    if cfg.v is not None:
        for it in items:
            it.pos.shift(1)
            y = cfg.v  # tpyc: type(int)
            print("everyday_loop:", y + it.pos.x)


# kept, pointer hop: a store through nx = a.next lands beneath a.next, beside a.v
def ptr_hop_kept(a: L) -> None:
    nx = a.next
    if a.v is not None:
        if nx is not None:
            nx.n = 3
        y = a.v  # tpyc: type(int)
        print("ptr_hop_kept:", y + 1)


# kept, walk rebind: rebinding node changes no object, so cfg.v survives
def walk_rebind_kept(head: L, cfg: N) -> None:
    if cfg.v is not None:
        node: Ptr[L] = head
        while node is not None:
            node = node.next
        y = cfg.v  # tpyc: type(int)
        print("walk_rebind_kept:", y + 1)


# value copy of an int field: a None store to the field leaves the copy
def copy_int(a: N) -> None:
    x = a.v
    if x is not None:
        a.v = None
        y = x  # tpyc: type(int)
        print("copy_int:", y + 1, a.v)


# value copy of a str field: the copy stays narrowed past a None store
def copy_str(k: S) -> None:
    s = k.s
    if s is not None:
        k.s = None
        print("copy_str:", s.upper(), k.s)  # tpyc: ok


# value copy of a pointer field: a None store to the slot leaves the copy
def copy_ptr(h: Holder) -> None:
    p = h.p
    if p is not None:
        h.p = None
        print("copy_ptr:", p.x)  # tpyc: non_null(p)


# kept: a non-None store through an alias keeps the aliased field's fact
def kept_alias_non_none(a: N) -> None:
    t = a
    if a.v is not None:
        t.v = 7
        y = a.v  # tpyc: type(int)
        print("kept_alias_non_none:", y * 2)


# a non-None store of a whole sub-object through an alias kills the facts beneath it
def sub_object(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.inner.v is not None:
        t.inner = empty_m()
        y = a.inner.v  # tpyc: type(/None/)
        print("sub_object:", y)


# and alias: t = b and a may hold a
def and_alias(a: N, b: N) -> None:
    t = b and a
    if a.v is not None:
        t.v = None
        y = a.v  # tpyc: type(/None/)
        print("and_alias:", y)


# tuple unpack: x holds a
def unpack(a: N, b: N) -> None:
    x, z = a, b
    if a.v is not None:
        x.v = None
        y = a.v  # tpyc: type(/None/)
        print("unpack:", y, z.v)


# with target: t holds a
def with_target(a: N) -> None:
    if a.v is not None:
        with a as t:
            t.v = None
        y = a.v  # tpyc: type(/None/)
        print("with_target:", y)


# match capture: m holds the subject a
def match_capture(a: N) -> None:
    match a:
        case m:
            if a.v is not None:
                m.v = None
                y = a.v  # tpyc: type(/None/)
                print("match_capture:", y)


# loop entry, field-chain argument: the call after the read reaches it on the back edge
def loop_arg_chain(a: N) -> None:
    if a.inner.v is not None:
        i = 0
        while i < 2:
            y = a.inner.v  # tpyc: type(/None/)
            print("loop_arg_chain:", y)
            clear_m(a.inner)
            i += 1


# loop entry, ternary receiver
def loop_receiver_ternary(a: N, b: N, c: bool) -> None:
    if a.v is not None:
        i = 0
        while i < 2:
            y = a.v  # tpyc: type(/None/)
            print("loop_receiver_ternary:", y)
            (a if c else b).reset()
            i += 1


# loop entry, keyword argument through an alias
def loop_kwarg_alias(a: N, b: N, c: bool) -> None:
    t = a if c else b
    if a.inner.v is not None:
        i = 0
        while i < 2:
            y = a.inner.v  # tpyc: type(/None/)
            print("loop_kwarg_alias:", y)
            clear_m(m=t.inner)
            i += 1


# kept: a mutable argument on an unrelated record, positional and keyword
def kept_call_unrelated(a: N, b: N) -> None:
    if a.v is not None:
        clear(b)
        clear(n=b)
        y = a.v  # tpyc: type(int)
        print("kept_call_unrelated:", y + 1, b.v)


# kept: a store to an unrelated object in a try body leaves the handler's a.v
def kept_try_unrelated(a: N) -> None:
    if a.v is not None:
        u = N()
        try:
            u.v = None
            raise ValueError("stop")
        except ValueError:
            y = a.v  # tpyc: type(int)
            print("kept_try_unrelated:", y + 1, u.v)


# kept, handler entry: a non-None store through an alias in the try body keeps a.v
def kept_try_alias_non_none(a: N, c: bool) -> None:
    t = a if c else N()
    if a.v is not None:
        try:
            t.v = 2
            raise ValueError("stop")
        except ValueError:
            y = a.v  # tpyc: type(int)
            print("kept_try_alias_non_none:", y + 1)


# a non-None store with no prior guard creates no fact
def no_fact_created(a: N) -> None:
    a.v = 5
    y = a.v  # tpyc: type(/None/)
    print("no_fact_created:", y)


def main() -> None:
    ternary(N(), True)
    or_alias(N(), N())
    walrus(N())
    nested(N(), True)
    chain(N())
    composed(N(), None)
    composed_reverse(N())
    loop_entry(N(), True)
    handler_finally(N(), False)
    len_range([1, 2], [3, 4, 5], False)
    len_range_unrelated([1, 2])
    local_root(True)
    direct(N())
    store_optional(N(), None)
    call_ternary(N(), N(), True)
    call_kwarg(N(), N(), True)
    call_chain(N())
    receiver_ternary(N(), N(), True)
    other = N()
    other.w = 2
    reassigned(N(), other)
    other_call = N()
    other_call.w = 3
    reassigned_call(N(), other_call)
    kept_non_none(N())
    kept_non_none_name(N(), 4)
    kept_unrelated(N())
    kept_sibling(N())
    s = N()
    o = N()
    s.self_alias(o, True)
    tail = L(None)
    head = L(tail)
    kept_cyclic(head)
    p1 = P()
    p1.shift(2)
    p2 = P()
    queued_deref(Holder(p1), p2, True)
    plain_bind(N())
    everyday_loop(N(), [Item(), Item()])
    hop_tail = L(None)
    hop = L(hop_tail)
    ptr_hop_kept(hop)
    wt = L(None)
    wh = L(wt)
    walk_rebind_kept(wh, N())
    copy_int(N())
    copy_str(S())
    copy_ptr(Holder(p1))
    kept_alias_non_none(N())
    sub_object(N(), N(), True)
    and_alias(N(), N())
    unpack(N(), N())
    with_target(N())
    match_capture(N())
    loop_arg_chain(N())
    loop_receiver_ternary(N(), N(), True)
    loop_kwarg_alias(N(), N(), True)
    kept_call_unrelated(N(), N())
    kept_try_unrelated(N())
    kept_try_alias_non_none(N(), True)
    no_fact_created(N())


main()
