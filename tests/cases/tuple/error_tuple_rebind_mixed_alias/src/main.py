# One refused pointer-tuple binding per function: a mixed alias (BUGS.md#ptr-tuple-capture-and-binder-rejects)
# or a binding owning nothing whose root is no parameter or `self` (BUGS.md#ptr-tuple-repoint-root-rejects).
from tpy import Own, int32, readonly


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class HB:
    bp: tuple[Box, Box]

    def __init__(self, k: int32) -> None:
        self.bp = (Box(k), Box(k + 1))


class HC:
    k: int32

    def __init__(self) -> None:
        self.k = 1


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def mk(k: int32) -> Own[HB]:
    return HB(k)


def pick(xs: list[Box], b: Box) -> tuple[Box, Box]:
    return (xs[0], b)


G = HB(70)
h = HB(0)


def f(q: tuple[Own[Box], Box], v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # 'q' holds the caller's object above and 'p''s objects here.
    q = p  # tpyc: error(/'q' is bound here to 'p', which keeps objects of its own in storage of 'f' itself, and to other storage at another of its bindings.*bind this value to a name of its own/)
    q[0].n += 100
    print(q[0].n, p[0].n, v.n)


def owning_source(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    s = make_mixed(5, v)
    # 'q' holds 'p''s objects here and the object 's' owns below.
    q = p  # tpyc: error(/'q' is bound here to 'p', which keeps objects of its own in storage of 'owning_source' itself, and to other storage at another of its bindings/)
    q = s
    q[0].n += 100
    print(q[0].n, p[0].n, s[0].n)


def block_root(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    for i in range(2):
        w = HB(50 + i)
        # block-root: the alias would keep the loop body's 'w'.
        q = w.bp  # tpyc: error(/'q' is bound to 'p', which keeps objects of its own in storage of 'block_root', so a binding of 'q' that owns nothing may point only into a parameter's or self's storage for now; here it points into 'w', a local of 'block_root'; bind this value to a name of its own/)
    print(q[0].n, q[1].n)


def owner_loop(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    for i in range(2):
        w = HB(50 + i)
        # owner-loop: the owning tuple itself would keep the loop body's 'w'.
        p = w.bp  # tpyc: error(/'p' keeps objects of its own in storage of 'owner_loop', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'w', a local of 'owner_loop'; bind this value to a name of its own/)
    print(p[0].n, p[1].n)


def top_local(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    k = HB(7)
    # top-local: a local of the body itself is a root no rule places yet.
    p = k.bp  # tpyc: error(/'p' keeps objects of its own in storage of 'top_local', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'k', a local of 'top_local'; bind this value to a name of its own/)
    print(p[0].n)


def global_root(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # global-root: a module global may be rebound under the pointers.
    p = G.bp  # tpyc: error(/'p' keeps objects of its own in storage of 'global_root', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'G', a module global; bind this value to a name of its own/)
    print(p[0].n)


def captured(v: Box, h0: HB) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)

    def inner(c: bool) -> int32:
        q = p
        if c:
            # captured: 'h0' is the enclosing body's parameter, not this one's.
            q = h0.bp  # tpyc: error(/'q' is bound to 'p', which keeps objects of its own in storage of 'captured', so a binding of 'q' that owns nothing may point only into a parameter's or self's storage for now; here it points into 'h0', a name 'inner' captures; bind this value to a name of its own/)
        return q[0].n

    print(inner(True), inner(False))


def readonly_narrowed(hh: readonly[HB | HC], v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    if isinstance(hh, HB):
        # readonly-narrowed: the parameter is declared read-only.
        q = hh.bp  # tpyc: error(/'q' is bound to 'p', which keeps objects of its own in storage of 'readonly_narrowed', so a binding of 'q' that owns nothing may point only into a parameter's or self's storage for now; here it points into 'hh', a readonly parameter; bind this value to a name of its own/)
    print(q[0].n)


def match_binder(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    match mk(50):
        case HB() as h:
            # match-binder: a capture named like a module global is a binder.
            q = h.bp  # tpyc: error(/'q' is bound to 'p', which keeps objects of its own in storage of 'match_binder', so a binding of 'q' that owns nothing may point only into a parameter's or self's storage for now; here it points into 'h', a local of 'match_binder'; bind this value to a name of its own/)
    print(q[0].n, q[1].n)


def element_alias(hs: list[HB], v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p
    e = hs[0]
    # element-alias: 'e' is a local, so `hs[0] = ...` would leave the alias
    # in a replaced element.
    q = e.bp  # tpyc: error(/'q' is bound to 'p', which keeps objects of its own in storage of 'element_alias', so a binding of 'q' that owns nothing may point only into a parameter's or self's storage for now; here it points into 'e', a local of 'element_alias'; bind this value to a name of its own/)
    hs[0] = HB(9)
    print(q[0].n)


def ternary_arm(v: Box, hb: HB, c: bool) -> None:
    p = (Box(1), v)
    p = (Box(2), v)
    s = hb.bp
    # ternary-arm: the arm 's' owns nothing and is a local.
    u = p if c else s  # tpyc: error(/'u' is bound to 'p', which keeps objects of its own in storage of 'ternary_arm', so a binding of 'u' that owns nothing may point only into a parameter's or self's storage for now; here it points into 's', a local of 'ternary_arm'; bind this value to a name of its own/)
    u[0].n += 100
    print(u[0].n, p[0].n, s[0].n)


def own_rebound(hb: Own[HB], v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # own-rebound: the rebind below writes the new object in place.
    p = hb.bp  # tpyc: error(/'p' keeps objects of its own in storage of 'own_rebound', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'hb', a parameter 'own_rebound' rebinds; bind this value to a name of its own/)
    hb = HB(90)
    print(p[0].n, hb.bp[0].n)


def local_tuple(b: Box) -> None:
    q = (b, b)
    p = make_mixed(1, b)
    # local-tuple: 'q' is a local of its own, whatever it points into.
    p = q  # tpyc: error(/'p' keeps objects of its own in storage of 'local_tuple', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'q', a local of 'local_tuple'; bind this value to a name of its own/)
    print(p[0].n)


def local_call_operand(xs: list[Box]) -> None:
    a = Box(1)
    b = Box(2)
    p = (a, b)
    # local-call-operand: the call lends the local 'b'.
    p = pick(xs, b)  # tpyc: error(/'p' keeps objects of its own in storage of 'local_call_operand', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'b', a local of 'local_call_operand'; bind this value to a name of its own/)
    print(p[0].n)


def binder_param(v: Box, hb: HB) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # binder-param: the match capture below rebinds the parameter 'hb'.
    p = hb.bp  # tpyc: error(/'p' keeps objects of its own in storage of 'binder_param', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'hb', a parameter 'binder_param' rebinds; bind this value to a name of its own/)
    match mk(50):
        case HB() as hb:
            print(hb.bp[0].n)
    print(p[0].n, p[1].n)


def nonlocal_param(v: Box, hb: HB) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # nonlocal-param: 'reset' rebinds the parameter 'hb' through `nonlocal`.
    p = hb.bp  # tpyc: error(/'p' keeps objects of its own in storage of 'nonlocal_param', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'hb', a parameter a nested function rebinds; bind this value to a name of its own/)

    def reset() -> None:
        nonlocal hb
        hb = HB(60)

    reset()
    print(p[0].n, p[1].n)


def nonlocal_root(v: Box) -> None:
    w = HB(3)

    def inner() -> int32:
        nonlocal w
        p = make_mixed(1, v)
        p = make_mixed(2, v)
        # nonlocal-root: 'w' is the enclosing body's local, not 'inner''s.
        p = w.bp  # tpyc: error(/'p' keeps objects of its own in storage of 'inner', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'w', a name 'inner' captures; bind this value to a name of its own/)
        w = HB(4)
        return p[0].n

    print(inner())


def temporary_root(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # temporary-root: the object 'mk' returns dies at the end of the statement.
    p = mk(5).bp  # tpyc: error(/'p' keeps objects of its own in storage of 'temporary_root', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into storage with no name; bind this value to a name of its own/)
    print(p[0].n, p[1].n)


f(make_mixed(7, Box(1)), Box(3))
owning_source(Box(3))
block_root(Box(3))
owner_loop(Box(3))
top_local(Box(3))
global_root(Box(3))
captured(Box(3), HB(4))
readonly_narrowed(HB(5), Box(3))
match_binder(Box(3))
element_alias([HB(6)], Box(3))
ternary_arm(Box(3), HB(8), True)
own_rebound(HB(10), Box(3))
local_tuple(Box(3))
local_call_operand([Box(11)])
binder_param(Box(3), HB(12))
nonlocal_param(Box(3), HB(13))
nonlocal_root(Box(3))
temporary_root(Box(3))
print(h.bp[0].n)
