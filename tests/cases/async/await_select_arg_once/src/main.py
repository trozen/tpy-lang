# An awaited select passed positionally evaluates one operand, once, at
# every call kind -- the defaulted call and the `k=` call alike (CPython
# prints one `eval` line per call; the order of operands is not pinned).
import asyncio
from dataclasses import dataclass, astuple
from tpy import int32, dispatch
import await_sel_mod


async def v(s: str) -> str:
    print("eval", s)
    return s


def sink(s: str, k: int32 = 5) -> str:
    return f"{s} {k}"


@dispatch
def dsink(s: str, k: int32 = 5) -> str:
    return f"str {s} {k}"


@dispatch
def dsink(s: int32, k: int32 = 6) -> str:
    return f"int {s} {k}"


def gsink[T](x: T, k: int32 = 5) -> T:
    return x


def vsink(*xs: str, k: int32 = 5) -> str:
    return f"{xs[0]} {xs[1]} {k}"


class M:
    def __init__(self) -> None:
        pass

    def m(self, s: str, k: int32 = 5) -> str:
        return f"{s} {k}"

    @dispatch
    def o(self, s: str, k: int32 = 5) -> str:
        return f"str {s} {k}"

    @dispatch
    def o(self, s: int32, k: int32 = 6) -> str:
        return f"int {s} {k}"

    def __call__(self, s: str, k: int32 = 5) -> str:
        return f"{s} {k}"


class R:
    def __init__(self, s: str, k: int32 = 5) -> None:
        self.s = s
        self.k = k


class Outer:
    class Inner:
        def __init__(self, s: str, k: int32 = 5) -> None:
            self.s = s
            self.k = k


class Err(Exception):
    def __init__(self, msg: str, code: int32 = 0) -> None:
        super().__init__(msg)
        self.code = code


async def go(c: bool) -> None:
    # Free function.
    r1 = sink(await v("y") if c else await v("n"))  # tpyc: ok
    print("free-def", r1)
    r2 = sink(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("free-kw", r2)
    # @dispatch function.
    r3 = dsink(await v("y") if c else await v("n"))  # tpyc: ok
    print("dispatch-def", r3)
    r4 = dsink(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("dispatch-kw", r4)
    # Generic function.
    r5 = gsink(await v("y") if c else await v("n"))  # tpyc: ok
    print("generic-def", r5)
    r6 = gsink(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("generic-kw", r6)
    # Varargs element.
    r7 = vsink(await v("y") if c else await v("n"), "z")  # tpyc: ok
    print("varargs-def", r7)
    r8 = vsink(await v("y") if c else await v("n"), "z", k=7)  # tpyc: ok
    print("varargs-kw", r8)
    mm = M()
    # Method.
    r9 = mm.m(await v("y") if c else await v("n"))  # tpyc: ok
    print("method-def", r9)
    r10 = mm.m(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("method-kw", r10)
    # @dispatch method.
    r11 = mm.o(await v("y") if c else await v("n"))  # tpyc: ok
    print("dispatch-method-def", r11)
    r12 = mm.o(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("dispatch-method-kw", r12)
    # `__call__`.
    r13 = mm(await v("y") if c else await v("n"))  # tpyc: ok
    print("call-def", r13)
    r14 = mm(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("call-kw", r14)
    # Record constructor.
    r15 = R(await v("y") if c else await v("n"))  # tpyc: ok
    print("ctor-def", r15.s, r15.k)
    r16 = R(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("ctor-kw", r16.s, r16.k)
    # Module function.
    r17 = await_sel_mod.mf(await v("y") if c else await v("n"))  # tpyc: ok
    print("module-fn-def", r17)
    r18 = await_sel_mod.mf(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("module-fn-kw", r18)
    # Dotted constructor.
    r19 = Outer.Inner(await v("y") if c else await v("n"))  # tpyc: ok
    print("dotted-ctor-def", r19.s, r19.k)
    r20 = Outer.Inner(await v("y") if c else await v("n"), k=7)  # tpyc: ok
    print("dotted-ctor-kw", r20.s, r20.k)
    # `raise Err(...)`.
    try:
        raise Err(await v("y") if c else await v("n"))  # tpyc: ok
    except Err as e1:
        print("raise-def", str(e1), e1.code)
    try:
        raise Err(await v("y") if c else await v("n"), code=7)  # tpyc: ok
    except Err as e2:
        print("raise-kw", str(e2), e2.code)


@dataclass
class P:
    a: int32
    b: int32


def tsink(t: tuple[int32, int32], k: int32 = 5) -> str:
    return f"{t[0]} {t[1]} {k}"


def macro_sections() -> None:
    p = P(1, 2)
    # A call macro's expansion replaces the argument of a defaulted, a keyword
    # and a keyword-first call; its argument is a name, which it reads once
    # per field (BUGS.md#call-macro-arg-evaluated-per-field).
    print("macro-def", tsink(astuple(p)))  # tpyc: ok
    print("macro-kw", tsink(astuple(p), k=7))  # tpyc: ok
    print("macro-kw-first", tsink(k=8, t=astuple(p)))  # tpyc: ok


asyncio.run(go(True))
asyncio.run(go(False))
macro_sections()
