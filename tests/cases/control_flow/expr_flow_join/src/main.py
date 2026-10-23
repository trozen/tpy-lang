# Expression-level control flow joins its paths as an `if` statement joins
# its branches: each operand of `and` / `or`, a ternary's arms, a chained
# comparison's later operands, an `assert` message, a comprehension's clauses
# and a `match` guard start from what the operands before them proved, a
# later operand's writes kill those facts, and what holds after the
# expression is what held on every path out of it. A `while` head runs again
# before every pass, so it is analyzed in the loop-entry state, and the loop
# is left from its FALSE path or from a `break` (after the `finally` bodies
# the break unwinds through); a name the body only steps one way keeps the
# bound on the other side.
# There is no section where a later operand kills an `isinstance` narrowing
# by rebinding the union: a walrus rebinding a non-value union is refused.
from typing import Iterator, Literal, Optional
from tpy import Ptr, int32


o_glob: Optional[int32] = 5


def reset() -> int32:
    global o_glob
    o_glob = 7
    return 1


# ternary: a direct walrus in an arm that may store None rebinds `o` too
# (the arm does not run here); a walrus outside any operand keeps what its
# value proves
def ternary_walrus(flag: bool) -> None:
    o: Optional[int32] = 5
    if o is not None:
        r = ((o := None) is None) if flag else False
        print("ternary-walrus", r, o + 1)  # tpyc: warning(/Potential None access/)
    p: Optional[int32] = 5
    if p is not None:
        s = (p := None) is None  # tpyc: ok
        print("walrus-none", s, p is None)  # tpyc: ok


# ternary: an arm's call writes the global
def ternary_call(flag: bool) -> None:
    if o_glob is not None:
        r = reset() if flag else 0
        print("ternary-call", r, o_glob + 1)  # tpyc: warning(/Potential None access/)


# or / and: a right operand's call writes the global, whether or not it ran
def bool_ops(flag: bool) -> None:
    if o_glob is not None:
        r = flag or reset() > 0
        print("or", r, o_glob + 1)  # tpyc: warning(/Potential None access/)
    if o_glob is not None:
        s = flag and reset() > 0
        print("and", s, o_glob + 1)  # tpyc: warning(/Potential None access/)


# chained comparison: a later operand that may run writes the global
def chained_kill(k: int32) -> None:
    if o_glob is not None:
        b = 0 < k < reset()
        print("chained", b, o_glob + 1)  # tpyc: warning(/Potential None access/)


class Haystack:
    def __contains__(self, v: int32) -> bool:
        global o_glob
        o_glob = 6
        return True


# in: a membership test whose `__contains__` is TPy code, as a statement
def contains_stmt() -> None:
    h = Haystack()
    if o_glob is not None:
        1 in h
        print("contains", o_glob + 1)  # tpyc: warning(/Potential None access/)


# comprehension: a filter's walrus is a store in the loop body, so it
# narrows the local for the element after it
def comp_rebind_local() -> None:
    x: Optional[str] = "start"
    ys = [x.upper() for i in range(2) if (x := "hello") is not None]  # tpyc: ok
    print("comp-local", ys, x)


def maybe(n: int32) -> Optional[int32]:
    return n if n > 0 else None


# comprehension back edge: the second filter's walrus reaches the first
# filter of the next iteration, which reads the Optional checked
def comp_back_edge() -> None:
    x = maybe(5)
    ys = [i for i in range(2) if x + 1 > 0 if (x := maybe(7)) is not None]  # tpyc: warning(/Potential None access/)
    print("comp-back-edge", ys)


# comprehension target: a filter's facts about the clause's own variable do
# not reach the enclosing variable of that name
def comp_target_shadow() -> None:
    y = maybe(4)
    ys = [y + 1 for y in [maybe(1), maybe(2)] if y is not None]  # tpyc: ok
    print("comp-shadow", ys, y + 1)  # tpyc: warning(/Potential None access/)


def message(ok: bool) -> str:
    return "bad" if ok else "worse"


# assert: the message runs only on the failing path, so its walrus neither
# narrows nor kills past the statement
def assert_message(flag: bool) -> None:
    x = maybe(2)
    assert flag, message((x := 5) is None)
    print("assert-fact", x + 1)  # tpyc: warning(/Potential None access/)
    y: Optional[str] = "hi"
    assert flag, message((y := None) is None)
    print("assert-kill", y.upper())  # tpyc: ok


# match guard: a failed guard falls through to the next arm with what it
# killed
def match_guard(v: int32) -> None:
    if o_glob is not None:
        match v:
            case 1 if reset() > 1:
                print("guard-hit")
            case _:
                print("match-guard", o_glob + 1)  # tpyc: warning(/Potential None access/)


# ternary: both arms establish the same range fact, which holds after it
def ternary_both_arms(c: bool) -> None:
    d = 1
    q = (d := 2) if c else (d := 3)
    print("ternary-both", q, 12 // d)  # tpyc: div_safe(d)


# ternary: an arm's range fact neither reaches the other arm nor survives
# the expression
def ternary_range(c: bool) -> None:
    i = 5
    r = (i := 0) if c else 10 // i  # tpyc: div_safe(i)
    print("ternary-range", r, 7 // i)  # tpyc: div_checked(i)




class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    p: Ptr[P]

    def __init__(self, p: Ptr[P]) -> None:
        self.p = p

    def swap(self, q: Ptr[P]) -> bool:
        self.p = q
        return True


def rotate(xs: list[int32]) -> bool:
    xs.append(xs.pop(0))
    return True


# and: a later operand's write through the receiver kills the pointer fact
# an earlier operand proved
def and_receiver(h: H, q: Ptr[P]) -> bool:
    return h.p is not None and h.swap(q) and h.p.x >= 0  # tpyc: nullable(h.p)


# ternary: the same, in the condition of a conditional expression
def ternary_receiver(h: H, q: Ptr[P]) -> int32:
    return h.p.x if h.p is not None and h.swap(q) else 0  # tpyc: nullable(h.p)


# and: a later operand's mutation of the container kills the bound an
# earlier operand proved
def and_container(xs: list[int32], i: int32) -> bool:
    return i >= 0 and i < len(xs) and rotate(xs) and xs[i] > 0  # tpyc: bounds_checked(xs)


# and: a later operand's walrus rebinds the index
def and_walrus_index(xs: list[int32], j: int32) -> bool:
    i = j
    return i >= 0 and i < len(xs) and (i := i - 1) >= 0 and xs[i] > 0  # tpyc: bounds_checked(xs)


# comprehension filter: the element reads after the filter's later operand
# mutated the container
def filter_container(xs: list[int32]) -> None:
    ys = [xs[i] for i in range(2) if i < len(xs) and rotate(xs)]  # tpyc: bounds_checked(xs)
    print("filter-container", ys)


# if head: a walrus in a later operand rebinds the index the body reads
def if_head_walrus(xs: list[int32], j: int32) -> int32:
    i = j
    if i >= 0 and i < len(xs) and (i := i - 1) >= 0:
        return xs[i]  # tpyc: bounds_checked(xs)
    return 0


# while head: the same, on every pass
def while_head(xs: list[int32]) -> int32:
    k = 0
    total = 0
    while k < len(xs) and rotate(xs):
        total += xs[k]  # tpyc: bounds_checked(xs)
        k += 1
    return total


class Box:
    n: int32 | None

    def __init__(self) -> None:
        self.n = 5

    def clear(self) -> int32:
        self.n = None
        return 1


# comprehension target: the enclosing `a` loses the fact the comprehension
# killed through its alias `b`, though a clause target shadows the name
def comp_alias() -> None:
    a = Box()
    b = a
    if a.n is not None:
        r = [b.clear() for a in [Box()]]
        print("comp-alias", r, a.n)


# chained comparison: the path that goes on keeps what the earlier operands
# bound and proved
def chained_walrus(n: int32) -> None:
    print("chained-walrus", 0 < n < (x := 5) < x + 1)  # tpyc: ok


# comprehension: a filter's walrus does not reach past a comprehension that
# may run zero times
def comp_zero_trip(n: int32, k: int32) -> None:
    x = maybe(1)
    ys = [i for i in range(n) if (x := maybe(5)) is not None]
    print("comp-zero-trip", ys, x + 1)  # tpyc: warning(/Potential None access/)
    d = k
    zs = [i for i in range(n) if (d := 5) > 0]
    print("comp-zero-trip-div", zs, 10 // d)  # tpyc: div_checked(d)


# ternary: a fresh name both arms bind is bound after it
def ternary_fresh(c: bool) -> None:
    q = (e := 2) if c else (e := 3)
    print("ternary-fresh", q, e)  # tpyc: ok


# controls: what the operands before an access proved holds there
def control_bounds(xs: list[int32], i: int32) -> bool:
    return i >= 0 and i < len(xs) and xs[i] > 0  # tpyc: bounds_safe(xs)


def control_pointer(h: H) -> bool:
    return h.p is not None and h.p.x >= 0  # tpyc: non_null(h.p)


def takes_rw(mode: Literal["r", "w"]) -> str:
    return mode


# literal narrowing: each arm narrows `mode`, the join holds their union
def literal_join(mode: Literal["r", "w", "a"]) -> None:
    if mode == "r":
        print("literal-r")
    elif mode == "w":
        print("literal-w")
    else:
        return
    print("literal-join", takes_rw(mode))  # tpyc: ok


# assert: a later operand's call kills what the earlier one proved
def assert_kill() -> None:
    assert o_glob is not None and reset() > 0
    print("assert-kill-op", o_glob + 1)  # tpyc: warning(/Potential None access/)


# while head: a later operand's write through the receiver kills the
# pointer fact for the body
def while_receiver(h: H, q: Ptr[P]) -> int32:
    n = 0
    total = 0
    while h.p is not None and h.swap(q) and n < 2:
        total += h.p.x  # tpyc: nullable(h.p)
        n += 1
    return total


# while head `or`: the body is entered from either operand's TRUE path, and
# the body rebinds the pointer
def while_ptr_or(a: P, b: Ptr[P], n: int32) -> int32:
    total = 0
    i = 0
    p: Ptr[P] = a
    if p is not None:
        while p is not None or i < n:
            total += p.x  # tpyc: nullable(p)
            p = b
            i += 1
            if i >= n:
                break
    return total


# and-operand pointer access: the pending fact does not outlive the
# statement's own path into the arm
def pending_and(r: Ptr[P], other: Ptr[P], c: bool) -> int32:
    t = 0
    if c and r.x > 0:
        r = other
        t += r.x  # tpyc: nullable(r)
    return t


# chained comparison head: the loop-entry state has no range for `i`
def while_chain() -> None:
    i = 2
    try:
        while 0 <= i < 3:
            q = 10 // i  # tpyc: div_checked(i)
            print("while-chain", q)
            i -= 1
    except ZeroDivisionError:
        print("while-chain zero")


# while head read after the body rebinds the Optional it tests
def while_head_rebind() -> None:
    o = maybe(1)
    if o is not None:
        n = 0
        while o + n < 4:  # tpyc: warning(/Potential None access/)
            n += 1
            o = maybe(1)
        print("while-head-rebind", n)


# break: the loop is left with the break's facts, not the head's FALSE path
def break_exit(q: Ptr[P]) -> None:
    p: Ptr[P] = q
    while p is None:
        break
    print("break-exit", p.x)  # tpyc: nullable(p)


# break through finally: the finally the break unwinds through writes `p`
def break_finally(a: P, b: Ptr[P]) -> None:
    p: Ptr[P] = None
    while p is None:
        try:
            p = a
            break
        finally:
            p = b
    print("break-finally", p.x)  # tpyc: nullable(p)


# loop ranges: a name the body only steps up keeps its lower bound
def step_up(xs: list[int32]) -> int32:
    total = 0
    i = 0
    while i < len(xs):
        total += xs[i]  # tpyc: bounds_safe(xs)
        i += 1
    return total


# ... spelled `i = i + 1`
def step_up_spelled(xs: list[int32]) -> int32:
    total = 0
    i = 0
    while i < len(xs):
        total += xs[i]  # tpyc: bounds_safe(xs)
        i = i + 1
    return total


# ... stepped down: the lower bound does not hold
def step_down(xs: list[int32]) -> None:
    i = 0
    n = 0
    while i < len(xs) and n < 2:
        print("step-down", xs[i])  # tpyc: bounds_checked(xs)
        i -= 1
        n += 1


# ... both directions
def step_mixed(xs: list[int32]) -> None:
    i = 0
    n = 0
    while i < len(xs) and n < 4:
        print("step-mixed", xs[i])  # tpyc: bounds_checked(xs)
        if n % 2 == 0:
            i += 1
        else:
            i -= 1
        n += 1


# ... a closure that rebinds it
def step_closure(xs: list[int32]) -> None:
    i = 0

    def reset() -> None:
        nonlocal i
        i = -1

    n = 0
    while i < len(xs) and n < 3:
        print("step-closure", xs[i])  # tpyc: bounds_checked(xs)
        i += 1
        if n == 1:
            reset()
        n += 1


# ... rebound from another name
def step_rebind(xs: list[int32]) -> int32:
    total = 0
    i = 0
    while i < len(xs):
        total += xs[i]  # tpyc: bounds_checked(xs)
        j = i + 1
        i = j
    return total


# finally entry: the step rule is the loop's; a `try` that raised before the
# step reaches the finally with `i` still 0
def finally_step(flag: bool) -> None:
    i = 0
    try:
        if flag:
            raise ValueError("early")
        i += 1
    finally:
        q = 10 // i  # tpyc: div_checked(i)
        print("finally-step", q)


# for: the loop is left with each break's facts
def for_break_exit(xs: list[int32], a: P, b: Ptr[P], c: bool) -> None:
    p: Ptr[P] = a
    for x in xs:
        p = b
        if c:
            break
        p = a
    print("for-break-exit", p.x)  # tpyc: nullable(p)


class Holder:
    p: Optional[P]

    def __init__(self) -> None:
        self.p = P(7)

    def __enter__(self) -> None:
        pass

    def __exit__(self, a: None, b: None, c: None) -> None:
        self.p = P(9)


# with exit: `__exit__` is TPy code, a call on the manager
def with_exit_straight() -> None:
    h = Holder()
    if h.p is None:
        return
    with h:
        pass
    print("with-exit", h.p.x)  # tpyc: warning(/Potential None access/)


# ... on a break that leaves the block
def with_exit_break() -> None:
    h = Holder()
    i = 0
    while i < 2:
        with h:
            if h.p is not None:
                break
        i += 1
    if i < 2:
        print("with-exit-break", h.p.x)  # tpyc: warning(/Potential None access/)


# ... on a loop's back edge
def with_exit_loop() -> None:
    h = Holder()
    if h.p is None:
        return
    i = 0
    while i < 2:
        with h:
            pass
        i += 1
    print("with-exit-loop", h.p.x)  # tpyc: warning(/Potential None access/)


# break through two nested finally bodies: the inner one writes `p`
def break_two_finally(a: P, b: Ptr[P]) -> None:
    p: Ptr[P] = None
    while p is None:
        try:
            try:
                p = a
                break
            finally:
                p = b
        finally:
            print("two-finally-outer")
    print("break-two-finally", p.x)  # tpyc: nullable(p)


# a try/finally around the loop: a break inside does not run it
def finally_around(a: P, b: Ptr[P]) -> None:
    p: Ptr[P] = None
    try:
        while p is None:
            p = a
            break
        # Checked: a loop with a break leaves through one conservative meet
        # (pre-loop state less the loop's kills), whatever the break held.
        print("finally-around", p.x)  # tpyc: nullable(p)
    finally:
        p = b


# for: a break through a finally that writes the pointer
def for_break_finally(xs: list[int32], a: P, b: Ptr[P]) -> None:
    p: Ptr[P] = a
    for x in xs:
        try:
            p = a
            break
        finally:
            p = b
    print("for-break-finally", p.x)  # tpyc: nullable(p)


# step rule: a negative literal step beside a positive one
def step_neg_literal(xs: list[int32]) -> None:
    i = 0
    n = 0
    while i < len(xs) and n < 3:
        print("step-neg-literal", xs[i])  # tpyc: bounds_checked(xs)
        i += 1
        if n == 1:
            i += -1
        n += 1


# ... a walrus rebind in the body
def step_walrus(xs: list[int32]) -> None:
    i = 0
    while i < len(xs):
        print("step-walrus", xs[i])  # tpyc: bounds_checked(xs)
        if (i := i + 1) > 10:
            break


# ... a body that suspends
def step_suspends(xs: list[int32]) -> Iterator[int32]:
    i = 0
    while i < len(xs):
        yield xs[i]  # tpyc: bounds_checked(xs)
        i += 1


# for: the loop target is the loop's own binding, killed at the exit
def for_target_kill() -> None:
    d = 1
    for d in range(1, 3):
        break
    q = 10 // d  # tpyc: div_checked(d)
    print("for-target", q)


# a loop whose `else` returns is left only through its breaks, which leave
# it live: what the other branch proves does not hold after the `if`
def terminating_else(c: bool, a: P, b: Ptr[P]) -> None:
    p: Ptr[P] = b
    if c:
        while True:
            break
        else:
            return
    else:
        p = a
    print("terminating-else", p.x)  # tpyc: nullable(p)


# ... an inner loop whose `else` continues the outer loop
def terminating_else_inner(c: bool, a: P, b: Ptr[P]) -> None:
    p: Ptr[P] = b
    for j in range(2):
        if c:
            while True:
                break
            else:
                continue
        else:
            p = a
        print("terminating-else-inner", p.x)  # tpyc: nullable(p)
        return


# for-else: what the `else` clause proves does not reach a break exit
def for_else_assert(p: Optional[P], n: int32) -> None:
    for i in range(n):
        break
    else:
        assert p is not None
    print("for-else-assert", p.x)  # tpyc: warning(/Potential None access/)


# ... in a generator
def for_else_assert_gen(p: Optional[P], n: int32) -> Iterator[int32]:
    for i in range(n):
        break
    else:
        assert p is not None
    yield p.x  # tpyc: warning(/Potential None access/)


# a break from an inner loop's `else` leaves the outer loop
def inner_else_break(p: Optional[P], n: int32) -> None:
    for j in range(2):
        for i in range(n):
            pass
        else:
            break
    else:
        assert p is not None
    print("inner-else-break", p.x)  # tpyc: warning(/Potential None access/)


# while head `or`: a break taken while the head was TRUE does not carry the
# head's FALSE path
def break_under_head(p: Optional[P], n: int32) -> None:
    i = 0
    while p is None or i < n:
        i += 1
        try:
            if p is None:
                break
        finally:
            pass
    print("break-under-head", p.x)  # tpyc: warning(/Potential None access/)


# break through a finally: the test that sent the break out is not carried
def finally_narrow(p: Ptr[P]) -> None:
    while True:
        try:
            if p is None:
                break
            print("finally-narrow-in", p.x)
            break
        finally:
            pass
    print("finally-narrow", p.x)  # tpyc: nullable(p)


class Clearing:
    p: Optional[P]
    clear: bool

    def __init__(self, clear: bool) -> None:
        self.p = P(5)
        self.clear = clear

    def __enter__(self) -> None:
        pass

    def __exit__(self, a: None, b: None, c: None) -> None:
        if self.clear:
            self.p = None


# with exit that may clear the field: the end of the block, a break out of
# it, a generator around it
def with_exit_clears() -> None:
    # The clearing path: `__exit__` stores None, read back only by a test.
    c = Clearing(True)
    if c.p is None:
        return
    with c:
        pass
    print("with-clears-none", c.p is None)
    h = Clearing(False)
    if h.p is None:
        return
    with h:
        pass
    print("with-clears", h.p.x)  # tpyc: warning(/Potential None access/)
    g = Clearing(False)
    if g.p is None:
        return
    for k in range(3):
        with g:
            break
    print("with-clears-break", g.p.x)  # tpyc: warning(/Potential None access/)


def with_exit_clears_gen() -> Iterator[int32]:
    h = Clearing(False)
    if h.p is None:
        return
    with h:
        yield 1
    yield h.p.x  # tpyc: warning(/Potential None access/)


class PyMgr:
    def __enter__(self) -> None:
        pass

    def __exit__(self, a: None, b: None, c: None) -> None:
        global dm
        dm = 2


dm = 1


# a manager the body binds later, whose name an outer native manager also
# has: the exit's verdict is not borrowed from the outer binding
def with_exit_later_bound(p: PyMgr) -> None:
    acc = 0
    i = 0
    if dm != 0:
        while i < 2:
            acc += 10 // dm  # tpyc: div_checked(dm)
            i += 1
            nm = p
            with nm:
                pass
    print("with-later-bound", acc)


def main() -> None:
    ternary_walrus(False)
    ternary_call(True)
    bool_ops(False)
    chained_kill(1)
    contains_stmt()
    comp_rebind_local()
    comp_back_edge()
    comp_target_shadow()
    assert_message(True)
    match_guard(1)
    ternary_both_arms(False)
    ternary_range(False)
    p1 = P(1)
    p2 = P(2)
    print("and-receiver", and_receiver(H(p1), p2))
    print("ternary-receiver", ternary_receiver(H(p1), p2))
    print("and-container", and_container([5, 7, 9], 1))
    print("and-walrus-index", and_walrus_index([5, 7, 9], 1))
    filter_container([5, 7, 9])
    print("if-head-walrus", if_head_walrus([5, 7, 9], 1))
    print("while-head", while_head([5, 7, 9]))
    comp_alias()
    chained_walrus(1)
    comp_zero_trip(2, 3)
    ternary_fresh(False)
    print("control-bounds", control_bounds([5, 7, 9], 1))
    print("control-pointer", control_pointer(H(p1)))
    literal_join("w")
    assert_kill()
    print("while-receiver", while_receiver(H(p1), p2))
    print("while-ptr-or", while_ptr_or(p1, p2, 3))
    print("pending-and", pending_and(p1, p2, True))
    while_chain()
    while_head_rebind()
    break_exit(p1)
    break_finally(p1, p2)
    print("step-up", step_up([5, 7, 9]))
    print("step-up-spelled", step_up_spelled([5, 7, 9]))
    step_down([1, 2, 3])
    step_mixed([1, 2, 3])
    step_closure([1, 2, 3])
    print("step-rebind", step_rebind([5, 7, 9]))
    finally_step(False)
    for_break_exit([1, 2], p1, p2, True)
    with_exit_straight()
    with_exit_break()
    with_exit_loop()
    break_two_finally(p1, p2)
    finally_around(p1, p2)
    for_break_finally([1], p1, p2)
    step_neg_literal([1, 2, 3])
    step_walrus([1, 2, 3])
    print("step-suspends", list(step_suspends([1, 2, 3])))
    for_target_kill()
    terminating_else(True, p1, p2)
    terminating_else_inner(True, p1, p2)
    for_else_assert(p1, 1)
    print("for-else-assert-gen", list(for_else_assert_gen(p1, 1)))
    inner_else_break(p1, 1)
    break_under_head(p1, 2)
    finally_narrow(p2)
    with_exit_clears()
    print("with-clears-gen", list(with_exit_clears_gen()))
    with_exit_later_bound(PyMgr())


nm = open("expr_flow_join_native.txt", "w")
nm.close()
gi = 0
gs = 0
gxs: list[int32] = [5, 6, 7]


def back_gi() -> None:
    global gi
    gi = 1


# module level: a stepped global a callee rebinds keeps no bound
while gi < len(gxs):
    gs += gxs[gi]  # tpyc: bounds_checked(gxs)
    gi += 1
    if gs < 6:
        back_gi()
print("module-step", gs)

main()
