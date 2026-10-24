# An unproven None access raises CPython's exception for the operation,
# catchable at every position; one section per position and per kind.
import asyncio
from typing import Callable, ClassVar, Iterator

from tpy import Own, Ptr, ReturnException, dispatch, error_return, int32, readonly, take_ptr


class E(Exception, ReturnException):
    pass


class P:
    LIMIT: ClassVar[int] = 42

    def __init__(self, x: int) -> None:
        self.x = x

    def m(self) -> int:
        return self.x

    @property
    def double(self) -> int:
        return self.x * 2

    @staticmethod
    def s() -> int:
        return 7

    @classmethod
    def c(cls) -> int:
        return 8


class H:
    p: P | None
    n: int | None
    cb: Callable[[int], int] | None

    def __init__(self) -> None:
        self.p = None
        self.n = None
        self.cb = None

    # position: method
    def read(self) -> int:
        p = self.p
        return p.x  # tpyc: warning(/Potential None access/)


class Built:
    v: int

    # position: constructor
    def __init__(self, p: P | None) -> None:
        self.v = 0
        try:
            self.v = p.x  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("ctor:", e)


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        print("with: exit")
        return False


class Suppress:
    def __enter__(self) -> "Suppress":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        print("suppress: exit")
        return True


# An `__exit__` annotated `-> None` cannot suppress.
class Plain:
    def __enter__(self) -> "Plain":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("plain: exit")


class Clears:
    p: P | None

    def __init__(self) -> None:
        self.p = P(7)

    def __enter__(self) -> "Clears":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.p = None
        return True


class Holds:
    c: Suppress
    x: int

    def __init__(self) -> None:
        self.c = Suppress()
        self.x = 9


class AsyncCm:
    async def __aenter__(self) -> "AsyncCm":
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, a: None, b: None, c: None) -> None:
        pass


class PtrHolder:
    q: Ptr[P]

    def __init__(self, q: Ptr[P]) -> None:
        self.q = q


class ClearsGlobal:
    def __init__(self, n: int = 0) -> None:
        self.n = n

    def __enter__(self) -> None:
        global GP
        GP = None

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


class Lends:
    h: H

    def __init__(self) -> None:
        self.h = H()

    def __enter__(self) -> H:
        return self.h

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


Q = P(0)
GP: Ptr[P] = Q


def pick(flag: bool, q: P) -> P | None:
    if flag:
        return q
    return None


def pick_int(flag: bool) -> int | None:
    if flag:
        return 1
    return None


def pick_list(flag: bool, xs: list[int]) -> list[int] | None:
    if flag:
        return xs
    return None


calls = 0


def bump() -> int:
    global calls
    calls += 1
    return calls


# position: generator body
def gen(p: P | None) -> Iterator[int]:
    yield 0
    yield p.x  # tpyc: warning(/Potential None access/)


# position: async def
async def coro(p: P | None) -> int:
    await asyncio.sleep(0)
    return p.x  # tpyc: warning(/Potential None access/)


# position: @error_return body -- the check raises, it is not an error return
@error_return(E)
def er(p: P | None) -> int:
    return p.x  # tpyc: warning(/Potential None access/)


def er_wrap(p: P | None) -> int:
    try:
        return er(p)
    except E:
        print("errret: E")
        return -1


def ro_read(p: readonly[P] | None) -> int:
    return p.x  # tpyc: warning(/Potential None access/)


# The Own parameter pins the owning Optional's subscript; nothing here needs
# to consume it, hence the warning.
def own_index(xs: Own[list[int] | None]) -> int:  # tpyc: warning(/never consumed/)
    return xs[0]  # tpyc: warning(/Potential None access/)


def positions() -> None:
    p = pick(False, Q)
    # position: function -- read, write, method
    try:
        print(p.x)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("func read:", e)
    try:
        p.x = 2  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("func write:", e)
    try:
        print(p.m())  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("func method:", e)
    try:
        print(H().read())
    except AttributeError as e:
        print("method:", e)
    b = Built(None)
    print("ctor v:", b.v)
    try:
        for v in gen(p):
            print("gen:", v)
    except AttributeError as e:
        print("gen:", e)
    try:
        print(asyncio.run(coro(p)))
    except AttributeError as e:
        print("async:", e)
    try:
        # The lambda captures the Optional by value, hence the copy warning.
        fn: Callable[[], int] = lambda: p.x  # tpyc: warning(/Potential None access/) warning(/may keep its own copy/)
        print(fn())
    except AttributeError as e:
        print("lambda:", e)
    try:
        print([p.x for _ in range(2)])  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("comp:", e)
    # position: with body -- __exit__ runs before the handler
    try:
        with Ctx():
            print(p.x)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("with:", e)
    # position: try/finally -- the finally body runs before the handler
    try:
        try:
            print(p.x)  # tpyc: warning(/Potential None access/)
        finally:
            print("finally: body")
    except AttributeError as e:
        print("finally:", e)
    try:
        print(er_wrap(p))
    except AttributeError as e:
        print("errret:", e)
    # position: match arm
    try:
        match bump():
            case 1:
                print(p.x)  # tpyc: warning(/Potential None access/)
            case _:
                print("match: other")
    except AttributeError as e:
        print("match:", e)


def slots() -> None:
    # slot: param, readonly param, Optional field, element, call result
    try:
        print(ro_read(None))
    except AttributeError as e:
        print("readonly param:", e)
    h = H()
    try:
        print(h.p.x)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("field:", e)
    ps: list[P | None] = [None]
    try:
        print(ps[0].x)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("element:", e)
    try:
        print(pick(False, Q).x)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("call result:", e)


def members() -> None:
    # kind: augmented write, property, static / class method, class constant
    p = pick(False, Q)
    try:
        p.x += 1  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("aug write:", e)
    try:
        print(p.double)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("property:", e)
    try:
        print(p.s())  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("staticmethod:", e)
    try:
        print(p.c())  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("classmethod:", e)
    try:
        print(p.LIMIT)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("class constant read:", e)
    try:
        # A write through an instance targets the class storage in TPy,
        # hence the portability warning; here it raises before the write.
        p.LIMIT = 5  # tpyc: warning(/Potential None access/) warning(/via instance writes to class storage/)
    except AttributeError as e:
        print("class constant write:", e)
    try:
        print(read_x(None))
    except AttributeError as e:
        print("field body:", e)


def subscripts() -> None:
    base = [1]
    xs = pick_list(False, base)
    try:
        print(xs[0])  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("list index:", e)
    d: dict[str, int] | None = None
    if bump() < 0:
        d = {"k": 1}
    try:
        print(d["k"])  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("dict key:", e)
    try:
        print(own_index(None))
    except TypeError as e:
        print("own param:", e)


def operands() -> None:
    n = pick_int(False)
    k: int32 | None = None
    f: float | None = None
    if bump() < 0:
        k = 3
        f = 2.0
    try:
        print(n + 1)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("left +:", e)
    try:
        print(1 + n)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("right +:", e)
    try:
        print(k * 2)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("int32 *:", e)
    try:
        print(f ** 2)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("float **:", e)
    try:
        print(n < 3)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("left <:", e)
    try:
        print(3 < n)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("right <:", e)
    try:
        print(-n)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("unary -:", e)
    try:
        print(~k)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("unary ~:", e)
    h = H()
    try:
        print(h.n + 1)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("field +:", e)
    ns: list[int | None] = [None]
    try:
        print(ns[0] + 1)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("element +:", e)
    try:
        print(pick_int(False) + 1)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("call result +:", e)
    try:
        print(h.n < 3)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("field <:", e)
    try:
        print(ns[0] < 3)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("element <:", e)
    try:
        print(-h.n)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("field unary -:", e)
    try:
        print(first_lt((None, 2)))
    except TypeError as e:
        print("tuple element <:", e)
    try:
        # A walrus operand is checked as the value it binds.
        print((m := pick_int(False)) < 3)  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("walrus <:", e)


def first_lt(t: tuple[int32 | None, int32]) -> bool:
    return t[0] < 3  # tpyc: warning(/Potential None access/)


# A check that raised proves nothing where control resumes after it: a
# finally, a handler, the code after a suppressing with.
def ptr_finally(p: Ptr[P]) -> None:
    try:
        print(p.x)
    finally:
        try:
            print(p.x)  # tpyc: nullable(p)
        except AttributeError as e:
            print("flow finally:", e)


def ptr_suppress(p: Ptr[P]) -> None:
    with Suppress():
        print(p.x)
    try:
        print(p.x)  # tpyc: nullable(p)
    except AttributeError as e:
        print("flow after with:", e)


def assert_finally(p: P | None) -> None:
    try:
        assert p is not None
        print(p.x)
    finally:
        try:
            print(p.x)  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("flow assert finally:", e)


def assert_suppress(p: P | None) -> None:
    with Suppress():
        assert p is not None
        print(p.x)
    try:
        print(p.x)  # tpyc: warning(/Potential None access/)
    except AttributeError as e:
        print("flow assert after with:", e)


# The suppressing `__exit__` clears the field the statement started with
# narrowed.
def exit_clears(h: Clears) -> None:
    if h.p is not None:
        with h:
            raise ValueError("boom")
        try:
            print(h.p.x)  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("flow exit clears:", e)


# A suppressing manager covers the code from its own `__enter__` on.
def ptr_header(p: Ptr[Holds]) -> None:
    with p.c:
        pass
    # A proof inside a `with` header lands at the statement boundary.
    print("flow header:", p.x)  # tpyc: nullable(p)


# A manager that cannot suppress keeps what its body proved.
def ptr_plain(p: Ptr[P]) -> None:
    with Plain():
        print("flow plain body:", p.x)
    print("flow plain:", p.x)  # tpyc: non_null(p)


@error_return(E)
def fail_return(flag: bool) -> int:
    if flag:
        raise E()
    return 1


def fail_throw(flag: bool) -> int:
    if flag:
        raise ValueError("v")
    return 1


# A finally is entered from the try body before what it proved and from a
# handler: neither the body's nor the handler's fact holds there.
def finally_return(flag: bool) -> None:
    p: P | None = None
    q: P | None = None
    try:
        fail_return(flag)
        p = P(1)
    except E:
        q = P(2)
    finally:
        try:
            print(p.x)  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("finally return p:", e)
        try:
            print(q.x)  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("finally return q:", e)


def finally_throw(flag: bool) -> None:
    p: P | None = None
    q: P | None = None
    try:
        fail_throw(flag)
        p = P(1)
    except ValueError:
        q = P(2)
    finally:
        try:
            print(p.x)  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("finally throw p:", e)
        try:
            print(q.x)  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("finally throw q:", e)


def async_mk(v: int) -> Own[AsyncCm]:
    return AsyncCm()


async def async_clear(h: PtrHolder) -> None:
    h.q = None


# The `__aenter__` await lets another task clear the field the header read.
async def async_header(h: PtrHolder) -> None:
    async with async_mk(h.q.x):
        try:
            print(h.q.x)  # tpyc: nullable(h.q)
        except AttributeError as e:
            print("flow async header:", e)


async def async_header_main(h: PtrHolder) -> None:
    t = asyncio.create_task(async_clear(h))
    await async_header(h)
    await t


# `__enter__` is a call: it clears the global an earlier statement proved.
def enter_clears_proved_global(m: ClearsGlobal) -> None:
    print("flow enter proved:", GP.x)
    with m:
        try:
            y = GP.x  # tpyc: nullable(GP)
            print(y)
        except AttributeError as e:
            print("flow enter clears proved:", e)


def reset_global() -> None:
    global GP
    GP = Q


# `__enter__` clears the global the header read; the header's proof holds in
# no body statement, the first or a later one.
def enter_clears_global() -> None:
    with ClearsGlobal(GP.x):
        try:
            print(GP.x)  # tpyc: nullable(GP)
        except AttributeError as e:
            print("flow enter clears:", e)
        try:
            print(GP.x)  # tpyc: nullable(GP)
        except AttributeError as e:
            print("flow enter clears second:", e)


# The second manager rebinds the narrowed `h`; the suppressed exception skips
# the body's re-check.
def later_target(original: H, cm: Lends) -> None:
    h = original
    if h.p is not None:
        with Suppress(), cm as h:
            fail_throw(True)
            if h.p is None:
                return
        try:
            print(h.p.x)  # tpyc: warning(/Potential None access/)
        except AttributeError as e:
            print("flow later target:", e)


def flow() -> None:
    try:
        ptr_finally(None)
    except AttributeError as e:
        print("flow try:", e)
    ptr_suppress(None)
    try:
        assert_finally(None)
    except AssertionError:
        print("flow assert: AssertionError")
    assert_suppress(None)
    exit_clears(Clears())
    holds = Holds()
    ptr_header(take_ptr(holds))
    ptr_plain(take_ptr(Q))
    finally_return(True)
    finally_return(False)
    finally_throw(True)
    finally_throw(False)
    asyncio.run(async_header_main(PtrHolder(take_ptr(Q))))
    enter_clears_proved_global(ClearsGlobal())
    reset_global()
    enter_clears_global()
    narrowed = H()
    narrowed.p = P(4)
    later_target(narrowed, Lends())


def times10(v: int) -> int:
    return v * 10


def callables() -> None:
    h = H()
    before = calls
    try:
        # The argument runs before the call raises, as in CPython.
        print(h.cb(bump()))  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("call:", e, calls - before)
    h.cb = times10
    if h.cb is not None:
        print("call narrowed:", h.cb(4))
    g = H()
    try:
        # The callee was None when the call began: installing one in an
        # argument does not make it callable.
        print(g.cb(install(g)))  # tpyc: warning(/Potential None access/)
    except TypeError as e:
        print("call install:", e)


def install(h: H) -> int:
    print("call install: argument ran")
    h.cb = times10
    return 1


def pointers() -> None:
    q = P(5)
    p: Ptr[P] = None
    if bump() < 0:
        p = take_ptr(q)
    try:
        print(p.x)  # tpyc: nullable(p)
    except AttributeError as e:
        print("ptr read:", e)
    try:
        print(p.m())
    except AttributeError as e:
        print("ptr method:", e)
    try:
        print(p.LIMIT)
    except AttributeError as e:
        print("ptr class constant:", e)
    try:
        p.LIMIT = 6  # tpyc: warning(/via instance writes to class storage/)
    except AttributeError as e:
        print("ptr class constant write:", e)


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def get(self) -> int32:
        return self.x


def take_r(r: R) -> int32:
    return r.x


# A body MIR does not model: its field receiver is under the None check.
def read_x(r: R | None) -> int32:  # tpyc: mir(uncovered /unproven pointer field read/)
    return r.x  # tpyc: warning(/Potential None access/)


@dispatch
def apply[T, U](f: Callable[[T | None], U], xs: list[T]) -> U:
    return f(xs[0])


@dispatch
def apply[T, U](f: Callable[[T], U], xs: list[T]) -> U:
    return f(xs[0])


def overload_trial() -> None:
    # The Optional candidate's trial reads `d.x` as a checked access; the
    # winner's `d` is a plain R, so its read takes no check.
    print("trial:", apply(lambda d: d.x + take_r(d), [R(3)]))  # tpyc: ok
    # The same through a method call on the parameter.
    print("trial method:", apply(lambda d: d.get() + take_r(d), [R(4)]))  # tpyc: ok


def main() -> None:
    positions()
    slots()
    members()
    subscripts()
    operands()
    flow()
    callables()
    pointers()
    overload_trial()


# position: module-level statement
G = pick(False, Q)
try:
    print(G.x)  # tpyc: warning(/Potential None access/)
except AttributeError as e:
    print("module:", e)

main()
