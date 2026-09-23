# A str/bytes view first bound in a block (loop body, if/elif/match arm, try
# body, except handler, loop else) and read after it is declared outside that
# block: it stays a zero-copy view only when every source is static or bound
# in that outer scope (a param, a local bound before the block, a loop
# variable over such storage, a view that qualifies); anything else owns.
from typing import Final, Iterator

from tpy import ReturnException, StrView, ValueType, error_return

import store


class H:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


def mk(i: int) -> tuple[str, int]:
    return ("payload-number-" + str(i) + "-long-enough-to-defeat-sso", i)


def split2(s: str) -> tuple[str, str]:
    return (s + "-left-part-long-enough-for-heap", s + "-right-part-long-enough-for-heap")


# for body: the unpack views the per-iteration call result.
def for_call_unpack() -> None:
    for i in range(3):
        a, b = split2("iter" + str(i))  # tpyc: ok
    print("for_call_unpack", a, b)


# while body: same, while spelling.
def while_call_unpack() -> None:
    i = 0
    while i < 2:
        a, b = split2("w" + str(i))  # tpyc: ok
        i += 1
    print("while_call_unpack", a, b)


# for body: a body-local tuple name is the source.
def loop_local_unpack() -> None:
    for i in range(3):
        t = mk(i)
        a, b = t  # tpyc: ok
    print("loop_local_unpack", a, b)


# for body: subscript, field and slice views of body locals.
def loop_local_views() -> None:
    for i in range(3):
        t = mk(i)
        h = H("holder-name-" + str(i) + "-long-enough-to-defeat-sso")
        s = "slice-source-" + str(i) + "-long-enough-to-defeat-sso"
        a = t[0]  # tpyc: ok
        f = h.name  # tpyc: ok
        c = s[2:]  # tpyc: ok
    print("loop_local_views", a, f, c)


# if arm: subscript and field views of arm locals.
def branch_views(flag: bool) -> None:
    if flag:
        t = mk(1)
        h = H("holder-name-in-branch-long-enough-to-defeat-sso")
        a = t[0]  # tpyc: ok
        f = h.name  # tpyc: ok
    else:
        a = "else-literal"
        f = "else-field"
    print("branch_views", a, f)


# match arm: the same subscript view.
def match_views(k: int) -> None:
    match k:
        case 1:
            t = mk(7)
            a = t[0]  # tpyc: ok
        case _:
            a = "default-arm"
    print("match_views", a)


# alias of a body-local view, and a slice of a slice.
def view_of_view() -> None:
    for i in range(2):
        t = mk(i)
        v = t[0]
        w = v  # tpyc: ok
        s = "slice-of-slice-source-long-enough-to-defeat-sso-" + str(i)
        v2 = s[2:]
        x = v2[3:]  # tpyc: ok
    print("view_of_view", w, x)


# nested subscript and a view-returning method call of body locals.
def deep_sources() -> None:
    for i in range(2):
        n = ((mk(i)[0], i), i)
        a = n[0][0]  # tpyc: ok
        s = "   strip-source-long-enough-to-defeat-sso-" + str(i) + "   "
        b = s.strip()  # tpyc: ok
    print("deep_sources", a, b)


# elif arm and except handler: arm-local sources.
def elif_arm(k: int) -> None:
    if k == 0:
        a = "zero"
    elif k == 1:
        t = mk(1)
        a = t[0]  # tpyc: ok
    else:
        a = "other"
    print("elif_arm", a)


def except_handler() -> None:
    try:
        n = int("not-a-number")
        a = "parsed" if n > 0 else "negative"
    except ValueError:
        t = mk(2)
        a = t[0]  # tpyc: ok
    print("except_handler", a)


# try body with a handler, and a loop `else` clause.
def try_body() -> None:
    try:
        t = mk(3)
        a = t[0]  # tpyc: ok
    except ValueError:
        a = "x"
    print("try_body", a)


def loop_else() -> None:
    for i in range(2):
        pass
    else:
        t = mk(4)
        a = t[0]  # tpyc: ok
    print("loop_else", a)


class Stop(Exception, ReturnException):
    pass


# @error_return body.
@error_return(Stop)
def er_body(k: int) -> int:
    for i in range(2):
        t = mk(i + k)
        a = t[0]  # tpyc: ok
    print("er_body", a)
    return k


class Box:
    first: str

    # constructor body.
    def __init__(self) -> None:
        for i in range(2):
            t = mk(i)
            a = t[0]  # tpyc: ok
        self.first = a

    # method body.
    def hoist(self) -> None:
        for i in range(2):
            h = H("method-holder-long-enough-to-defeat-sso-" + str(i))
            f = h.name  # tpyc: ok
        print("method", f)


# closure body.
def closure_body() -> None:
    def inner() -> None:
        for i in range(2):
            t = mk(i)
            a = t[0]  # tpyc: ok
        print("closure_body", a)

    inner()


# a hoisted alias of an arm-local record: the view reads the record, not the
# alias, so it owns (element alias, try body and `with` target likewise).
def alias_arm(flag: bool) -> None:
    if flag:
        h = H("alias-arm-holder-long-enough-to-defeat-sso")
        r = h
        v = r.name  # tpyc: ok
    else:
        g = H("alias-else-holder-long-enough-to-defeat-sso")
        r = g
        v = r.name  # tpyc: ok
    print("alias_arm", v)


def elem_alias_arm(flag: bool) -> None:
    if flag:
        hs = [H("elem-alias-holder-long-enough-to-defeat-sso")]
        r = hs[0]
        v = r.name  # tpyc: ok
    else:
        gs = [H("elem-alias-else-holder-long-enough-to-sso")]
        r = gs[0]
        v = r.name  # tpyc: ok
    print("elem_alias_arm", v)


def try_alias() -> None:
    try:
        h = H("try-alias-holder-long-enough-to-defeat-sso")
        r = h
        v = r.name  # tpyc: ok
    except ValueError:
        g = H("try-alias-else-holder-long-enough-to-sso")
        r = g
        v = r.name  # tpyc: ok
    print("try_alias", v)


class Ctx:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n

    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, kind, value, tb) -> None:
        pass


def with_target_arm(flag: bool) -> None:
    if flag:
        with Ctx("with-target-name-long-enough-to-defeat-sso") as c:
            v = c.name  # tpyc: ok
    else:
        with Ctx("with-target-else-name-long-enough-to-sso") as c:
            v = c.name  # tpyc: ok
    print("with_target_arm", v)


# a match capture rebinding a hoisted view reads the capture's subject.
def capture_rebind() -> None:
    for i in range(2):
        h = H("capture-holder-" + str(i) + "-long-enough-to-defeat-sso")
        v = "lit"
        match h:
            case H(name=v):  # tpyc: ok
                pass
    print("capture_rebind", v)


def first(t: tuple[str, int]) -> StrView:
    return t[0]


class P(ValueType):
    name: str

    def __init__(self, n: str) -> None:
        self.name = n

    def nm(self) -> StrView:
        return self.name


# a tuple argument and a ValueType receiver lend storage to a view result.
def lending_operands(flag: bool) -> None:
    if flag:
        t = mk(8)
        v = first(t)  # tpyc: ok
        p = P("value-type-name-long-enough-to-defeat-sso")
        w = p.nm()  # tpyc: ok
    else:
        v = "else"
        w = "else"
    print("lending_operands", v, w)


# a loop variable reused by a later loop: the view reads the FIRST loop's
# iterable, a body-local list.
def reused_loop_var(ps: list[str]) -> None:
    for i in range(2):
        xs = [mk(i)[0], mk(i + 10)[0]]
        for s in xs:
            v = s  # tpyc: ok
    for s in ps:
        pass
    print("reused_loop_var", v)


FG: Final[str] = "final-global-long-enough-to-defeat-sso-xxxxxxxxxx"


class K:
    TAG: Final[str] = "class-constant-long-enough-to-defeat-sso-xxxxxxx"


# inverse: a Final global and a class constant are static storage.
def static_sources(flag: bool) -> None:
    if flag:
        a = FG  # tpyc: ok
        b = K.TAG  # tpyc: ok
    else:
        a = "e"
        b = "e"
    print("static_sources", a, b)


class M:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n

    def nv(self) -> StrView:
        return self.name


# a pre-block record MOVED into an arm-local alias at its last use: the view
# reads the alias's own storage, which dies with the arm, so it owns.
def moved_alias(flag: bool) -> None:
    h = M("moved-alias-holder-long-enough-to-defeat-sso")
    if flag:
        r = h
        i = r.nv()  # tpyc: ok
    else:
        i = "x"
    print("moved_alias", i)


# inverse: the pre-block record is read again, so the alias borrows it and
# the view stays one.
def kept_alias(flag: bool) -> None:
    h = M("kept-alias-holder-long-enough-to-defeat-sso")
    if flag:
        r = h
        i = r.nv()  # tpyc: ok
    else:
        i = "x"
    print("kept_alias", i, h.name)


# a plain module variable read through its module owns when hoisted (the
# module can rebind it); a Final constant of that module stays a view.
def module_attr(flag: bool) -> None:
    if flag:
        a = store.PLAIN  # tpyc: ok
        b = store.CONST  # tpyc: ok
    else:
        a = "x"
        b = "x"
    store.reset()
    print("module_attr", a, b)


# a match capture over a CALL subject reads a temporary.
def capture_of_call() -> None:
    for i in range(2):
        v = "lit"
        match M("capture-of-call-" + str(i) + "-long-enough-to-defeat-sso"):
            case M(name=v):  # tpyc: ok
                pass
    print("capture_of_call", v)


# a view first bound in a loop NESTED in an `if` arm, over arm-local storage.
def nested_loop_in_arm(flag: bool) -> None:
    if flag:
        ys = [mk(1)[0], mk(2)[0]]
        for s in ys:
            w = s  # tpyc: ok
        h = H("nested-arm-holder-long-enough-to-defeat-sso")
        for i in range(2):
            f = h.name  # tpyc: ok
    else:
        w = "e"
        f = "e"
    print("nested_loop_in_arm", w, f)


shadowed = mk(100)


# a loop-body local that shadows a module global is the function's own
# variable, not the global's storage: the view owns.
def shadows_global() -> None:
    for i in range(2):
        shadowed = mk(i)
        a = shadowed[0]  # tpyc: ok
    print("shadows_global", a)


class Util:
    @staticmethod
    def head(s: str) -> StrView:
        return s[:24]


# inverse: a module- or class-qualified call lends only its arguments, so a
# view of a param through it stays a view.
def qualified_call(s: str) -> None:
    for i in range(2):
        a = store.head(s)  # tpyc: ok
        b = Util.head(s)  # tpyc: ok
    print("qualified_call", a, b)


def uppers(ws: list[str]) -> Iterator[str]:
    for w in ws:
        yield w.upper()


# a generator expression or function yields its str BY VALUE into the
# per-step result: a loop variable hoisted past the loop reads that, so the
# view owns (upper-casing and identity genexprs, a generator function).
def generator_heads(words: list[str]) -> None:
    while True:
        y = ""
        for q in (w.upper() for w in words):
            y = q  # tpyc: ok
        break
    while True:
        y2 = ""
        for q2 in uppers(words):
            y2 = q2  # tpyc: ok
        break
    while True:
        y3 = ""
        for q3 in (w for w in words):
            y3 = q3  # tpyc: ok
        break
    print("generator_heads", y, y2, y3)


class Bag:
    xs: list[str]

    def __init__(self) -> None:
        self.xs = ["bag-element-alpha-long-enough-to-defeat-sso",
                   "bag-element-omega-long-enough-to-defeat-sso"]

    def __iter__(self) -> Iterator[str]:
        for x in self.xs:
            yield x


class Counter:
    xs: list[str]
    i: int

    def __init__(self) -> None:
        self.xs = ["counter-alpha", "counter-omega"]
        self.i = 0

    def __iter__(self) -> "Counter":
        return self

    def __next__(self) -> str:
        if self.i >= len(self.xs):
            raise StopIteration
        self.i += 1
        return self.xs[self.i - 1] + "-fresh-suffix-long-enough-to-defeat-sso"


# a record iterated through its generator `__iter__`, and a user iterator
# whose `__next__` returns a fresh str: the loop variable reads the per-step
# result, so the hoisted view owns; the list field itself stays a view.
def implicit_iter_heads() -> None:
    b = Bag()
    while True:
        y = ""
        for q in b:
            y = q  # tpyc: ok
        break
    c = Counter()
    while True:
        z = ""
        for q2 in c:
            z = q2  # tpyc: ok
        break
    while True:
        u = ""
        for q3 in b.xs:
            u = q3  # tpyc: ok
        break
    print("implicit_iter_heads", y, z, u)


# inverse: a for over a list param hands out its element, so the view of it
# stays one.
def list_param_head(words: list[str]) -> None:
    while True:
        y = ""
        for q in words:
            y = q  # tpyc: ok
        break
    print("list_param_head", y)


# inverse: a param source stays a view.
def param_source(p: tuple[str, int], h: H) -> None:
    for i in range(2):
        a = p[0]  # tpyc: ok
        f = h.name  # tpyc: ok
    print("param_source", a, f)


# inverse: a body local read after the loop BEFORE the view is hoisted with
# it, so the view stays one. Read in the other order the view owns: the rule
# decides at the first read after the loop, before the source is hoisted.
def read_order() -> None:
    for i in range(2):
        t = mk(i)
        a = t[0]  # tpyc: ok
    print("read_order", t[1], a)
    for j in range(2):
        u = mk(j)
        b = u[0]  # tpyc: ok
    print("read_order_late", b, u[1])


# inverse: a loop-body view of storage bound BEFORE the loop stays a view.
def loop_durable_source() -> None:
    t = mk(5)
    for i in range(2):
        a = t[0]  # tpyc: ok
    print("loop_durable_source", a)


# inverse: an if-arm view of a function-scope local stays a view.
def branch_durable_source(flag: bool) -> None:
    h = H("outer-holder-name-long-enough-to-defeat-sso")
    if flag:
        f = h.name  # tpyc: ok
    else:
        f = "else-field"
    print("branch_durable_source", f)


# inverse: the for-head target over a live container read after the loop.
def leaked_head() -> None:
    pairs = [("first-key-long-enough-to-defeat-sso", 1),
             ("second-key-long-enough-to-defeat-sso", 2)]
    for a, b in pairs:  # tpyc: ok
        pass
    print("leaked_head", a, b)


def main() -> None:
    for_call_unpack()
    while_call_unpack()
    loop_local_unpack()
    loop_local_views()
    branch_views(True)
    match_views(1)
    loop_durable_source()
    branch_durable_source(True)
    leaked_head()
    view_of_view()
    deep_sources()
    elif_arm(1)
    except_handler()
    try_body()
    loop_else()
    try:
        er_body(2)
    except Stop:
        pass
    bx = Box()
    print("ctor", bx.first)
    bx.hoist()
    closure_body()
    param_source(mk(6), H("param-holder-long-enough-to-defeat-sso"))
    read_order()
    alias_arm(True)
    elem_alias_arm(True)
    try_alias()
    with_target_arm(True)
    capture_rebind()
    lending_operands(True)
    reused_loop_var(["later-loop-element-long-enough-to-defeat-sso"])
    static_sources(True)
    moved_alias(True)
    kept_alias(True)
    module_attr(True)
    capture_of_call()
    nested_loop_in_arm(True)
    shadows_global()
    words = ["generator-head-alpha-long-enough-to-defeat-sso",
             "generator-head-omega-long-enough-to-defeat-sso"]
    generator_heads(words)
    list_param_head(words)
    implicit_iter_heads()
    qualified_call("qualified-call-param-long-enough-to-defeat-sso")


main()
