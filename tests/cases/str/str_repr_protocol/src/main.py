# str(), repr(), f-strings, a user Stringable parameter and a generic bound agree
# for every type TPy emits: Optional / union / Ptr / alias / tuple part-wise.
import asyncio
from enum import Enum, IntEnum
from typing import Iterable, Iterator, Protocol, Sized
from tpy import (Array, Own, Ptr, Representable, ReturnException, Span, SpanIter,
                 StrView, String, Stringable, ValueType, char, dispatch,
                 error_return, int32, int64, take_ptr)


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __repr__(self) -> str:
        return f"A({self.n})"


class S:
    def __init__(self) -> None:
        pass

    def __str__(self) -> str:
        return "S!"


class Plain:
    def __init__(self) -> None:
        pass


class Color(Enum):
    RED = 1
    BLUE = 2


class Num(IntEnum):
    ONE = 1


type J = str | list[J]


class Holder:
    o: A | None
    x: int32 | None
    pair: tuple[A, int32]
    a: A

    def __init__(self) -> None:
        self.o = A(5)
        self.x = 6
        self.pair = (A(7), 8)
        self.a = A(9)

    def get_a(self) -> A:
        return self.a

    def __repr__(self) -> str:
        # method: a tuple field and Optional fields
        return f"Holder({repr(self.pair)}, {repr(self.o)}, {self.x!r}, {text(self.x)})"  # tpyc: ok


def text(v: Stringable) -> str:
    return str(v)


def rtext(v: Representable) -> str:
    return repr(v)


def gtext[T: Stringable](v: T) -> str:
    return f"{v}"


class Bumpable(Protocol):
    def bump(self) -> None: ...


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1

    def __repr__(self) -> str:
        return f"Box({self.n})"


class Shelf:
    box: Box

    def __init__(self) -> None:
        self.box = Box(1)

    def get_box(self) -> Box:
        return self.box


def bump_it(b: Bumpable) -> None:
    b.bump()


class Touchable(Protocol):
    def touch(self) -> int32: ...


class Cell(ValueType):
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def touch(self) -> int32:
        return self.v

    def __repr__(self) -> str:
        return f"Cell({self.v})"


class CellBox:
    cell: Cell

    def __init__(self) -> None:
        self.cell = Cell(1)

    def replace(self) -> None:
        self.cell = Cell(9)


def replaced_read(c: Touchable, b: CellBox) -> int32:
    # the field holding the first argument is replaced before it is read
    b.replace()
    return c.touch()


def text_after(v: Stringable, b: Box) -> str:
    # the field the first argument was read from is written before it is read
    b.n = 9
    return str(v)


def text_after_elem(v: Stringable, xs: list[int32]) -> str:
    xs[0] = 9
    return str(v)


def opt_text(v: Stringable, o: A | None) -> str:
    return str(v) + "/" + str(o)


# The compiler's winner is declared first (the CPython dispatch stub runs the
# first variant that matches).
@dispatch
def pick(v: Stringable) -> str:
    return "pick proto " + str(v)


@dispatch
def pick(v: int64) -> str:
    return "pick int64 " + str(v)


@dispatch
def pick2(v: int64) -> str:
    return "pick2 int64 " + str(v)


@dispatch
def pick2(v: Stringable) -> str:
    return "pick2 proto " + str(v)


def free_fn(x: int32 | None, o: A | None, p: Ptr[A], u: int32 | str,
            t: tuple[A, int32], xs: list[int32], h: Holder, j: J,
            sv: StrView, s: String, c: char) -> None:
    # free function: every operation over name / field / call / narrowed
    print("free str", str(x), str(o), str(p), str(u), str(t), str(xs), str(j))  # tpyc: ok
    print("free repr", repr(x), repr(o), repr(p), repr(u), repr(t), repr(j))  # tpyc: ok
    print("free fstr", f"{x} {o} {p} {u} {t} {xs} {j} {o!r} {u!r}")  # tpyc: ok
    print("free proto", text(x), text(o), text(p), text(u), text(t), rtext(o))  # tpyc: ok
    print("free generic", gtext(3), gtext(Color.RED), gtext(A(2)))  # tpyc: ok
    print("free fresh", text((1, "a")), text(A(5)), str((1, "a")))  # tpyc: ok
    print("free field", str(h.o), str(h.x), repr(h.pair), text(h.o), text(h.x))  # tpyc: ok
    print("free call", str(h.get_a()), repr(h.get_a()), text(h.get_a()))  # tpyc: ok
    print("free scalars", str(sv), str(s), str(c), repr(c), text(3), text(sv))  # tpyc: ok
    print("free enum", str(Color.RED), repr(Color.RED), str(Num.ONE), text(Num.ONE),  # tpyc: ok
          f"{Color.RED}", rtext(Color.RED))
    print("free record", str(A(1)), str(S()), repr(S()).startswith("<__main__.S object at 0x"),  # tpyc: ok
          text(S()), str(Plain()).startswith("<__main__.Plain object at 0x"),
          f"{Plain()}".startswith("<__main__.Plain object at 0x"),
          f"{Plain()!r}".startswith("<__main__.Plain object at 0x"),
          f"{Plain()!s}".startswith("<__main__.Plain object at 0x"))
    print("free none", str(None), repr(None), repr(True), text(None))  # tpyc: ok
    ys = [4, 5]
    sp = Span[int32](ys)
    print("free span", str(sp), repr(sp), f"{sp}")  # tpyc: ok
    if x is not None:
        print("free narrowed", str(x), repr(x), text(x))  # tpyc: ok
    if o is not None:
        print("free narrowed", str(o), text(o))  # tpyc: ok


class Shower:
    def __init__(self) -> None:
        pass

    def show(self, o: A | None, u: int32 | str) -> None:
        # method: parameters at native and user protocol slots
        print("method", str(o), repr(u), text(o), f"{o}")  # tpyc: ok


class Built:
    label: str

    def __init__(self, o: A | None, u: int32 | str) -> None:
        # constructor
        self.label = str(o) + " " + repr(u) + " " + text(o)  # tpyc: ok


def mk(n: int32) -> Own[A | None]:
    if n > 0:
        return A(n)
    return None


def gen(n: int32, x: int32 | None) -> Iterator[str]:
    # generator (an Optional parameter of a frame is
    # BUGS.md#frame-optional-param-const-caller)
    o = mk(n)
    yield str(o)  # tpyc: ok
    yield repr(x) + " " + text(o)  # tpyc: ok


async def coro(n: int32, u: int32 | str) -> str:
    # async
    o = mk(n)
    await asyncio.sleep(0)
    return str(o) + " " + repr(u) + " " + text(u) + " " + text(o)  # tpyc: ok


class Missing(Exception, ReturnException):
    pass


@error_return(Missing)
def er(o: A | None, x: int32 | None) -> str:
    # @error_return body
    if x is None:
        raise Missing
    return str(o) + " " + text(x)  # tpyc: ok


def positions(o: A | None, x: int32 | None, u: int32 | str) -> None:
    Shower().show(o, u)
    print("ctor", Built(o, u).label)
    n = o.n if o is not None else 0
    print("gen", list(gen(n, x)))
    print("async", asyncio.run(coro(n, u)))
    # comprehension
    print("comp", [str(o) for _ in range(2)], [text(x) for _ in range(1)], [repr(u) for _ in range(1)])  # tpyc: ok
    # closure
    def inner() -> str:
        return str(o) + " " + text(u)  # tpyc: ok
    print("closure", inner())
    # try / finally
    try:
        print("try", repr(o), text(x))  # tpyc: ok
    finally:
        print("finally", str(u))  # tpyc: ok
    # match arm
    match u:
        case str():
            print("match str", repr(u), text(o))  # tpyc: ok
        case _:
            print("match other", str(u), text(o))  # tpyc: ok
    try:
        r = er(o, x)
        print("error_return", r)
    except Missing:
        print("error_return raised")


def mutation() -> None:
    # a user protocol parameter binds the caller's object: name / field / call
    b = Box(1)
    sh = Shelf()
    bump_it(b)  # tpyc: ok
    bump_it(sh.box)  # tpyc: ok
    bump_it(sh.get_box())  # tpyc: ok
    print("mutation", b, sh.box, repr(b), text(sh.box))
    # a value-type record at a parameter its callee may write is copied:
    # replacing the field holding it does not reach the callee (CPython reads 1)
    cb = CellBox()
    print("replaced", replaced_read(cb.cell, cb), cb.cell)  # tpyc: ok
    # ... and so is a scalar at a Stringable parameter, as at its int32 twin
    # (field and element; CPython reads 1)
    b2 = Box(1)
    print("replaced scalar", text_after(b2.n, b2), b2.n)  # tpyc: ok
    ys = [1]
    print("replaced element", text_after_elem(ys[0], ys), ys[0])  # tpyc: ok


def pointer_form() -> None:
    # a pointer-form Optional at a TPy-bodied protocol parameter passes as held
    a = A(3)
    o: A | None = a if a.n > 0 else None
    n: A | None = None if a.n > 0 else a
    print("pointer form", text(o), text(n), opt_text(o, n), opt_text(n, o))  # tpyc: ok
    a.n = 4
    print("pointer form aliased", text(o), rtext(o))  # tpyc: ok


def overload_pick() -> None:
    # a protocol match ranks strict: int32 picks the Stringable variant
    print(pick(int32(3)), pick("s"))  # tpyc: ok
    # ... and an exact int64 match still wins (its own set: the CPython
    # dispatch stub runs the first variant that matches)
    print(pick2(int64(3)))  # tpyc: ok


class Sib:
    p: Ptr[A]
    c: Color
    x: int32 | None
    s: str
    raw: bytes

    def __init__(self, p: Ptr[A]) -> None:
        self.p = p
        self.c = Color.RED
        self.x = 1
        self.s = "aaa"
        self.raw = b"r\x02"


def ptr_after(v: Stringable, h: Sib, q: Ptr[A]) -> str:
    # the field the first argument was read from is replaced first
    h.p = q
    return str(v)


def enum_after(v: Representable, h: Sib) -> str:
    h.c = Color.BLUE
    return repr(v)


def opt_after(v: Stringable, h: Sib) -> str:
    h.x = None
    return str(v)


def gen_text(v: Representable) -> Iterator[str]:
    yield repr(v)


async def coro_text(v: Representable) -> str:
    await asyncio.sleep(0)
    return repr(v)


def n_after(v: Representable, h: A) -> str:
    h.n = 9
    return repr(v)


def grown_after(v: Stringable, xs: list[str]) -> str:
    for _ in range(1000):
        xs.append("x")
    return str(v)


def conditionals() -> None:
    # a select of two places is no copy: it is copied as a place is
    a = A(3)
    b = A(4)
    print("conditional value", n_after(a.n if a.n > 0 else b.n, a))  # tpyc: ok
    xs = ["first-original-long-string-beyond-the-small-buffer", "second"]
    print("conditional view", grown_after(xs[0] if a.n > 0 else xs[1], xs))  # tpyc: ok
    # a call result is copied too: it may be a reference into the caller's
    # storage (`max` / `min` return an operand)
    print("conditional call", n_after(mk_n(), a))  # tpyc: ok
    a.n = 3
    print("max", n_after(max(a.n, 0), a))  # tpyc: ok
    a.n = 3
    print("min", n_after(min(a.n, a.n), a))  # tpyc: ok
    a.n = 3
    c = a.n > 0
    print("conditional calls", n_after(mk_n() if c else mk_m(), a))  # tpyc: ok
    # a str local with owned storage passes as the view its `str` twin takes, no copy
    s = "ab"
    s = s + "c"
    print("str local", text(s), s)  # tpyc: ok


def mk_n() -> int32:
    return 5


def mk_m() -> int32:
    return 6


def siblings() -> None:
    # each argument takes its concrete twin's form: a copy of a Ptr, an enum
    # and a value Optional
    a = A(1)
    b = A(9)
    h = Sib(take_ptr(a))
    print("sibling ptr", ptr_after(h.p, h, take_ptr(b)))  # tpyc: ok
    print("sibling enum", enum_after(h.c, h))  # tpyc: ok
    print("sibling opt", opt_after(h.x, h))  # tpyc: ok
    # a bytes field and element take the bytes view
    bs = [b"ab\x01"]
    print("sibling bytes", text(h.raw), rtext(bs[0]))  # tpyc: ok


class Text(Stringable, Protocol):
    pass


def gen_sub(v: Text) -> Iterator[str]:
    yield str(v)


def gen_len(v: Sized) -> Iterator[int32]:
    yield len(v)


def gen_iter(src: Iterable[int32]) -> Iterator[int32]:
    for v in src:
        yield v


def forward(it: Iterator[int32]) -> Iterator[int32]:
    yield next(it, -1)
    yield next(it, -1)


async def frames_async() -> None:
    # async: the caller's record is shared
    r = A(1)
    task = asyncio.create_task(coro_text(r))  # tpyc: ok
    r.n = 7
    print("async record", await task)


def frames() -> None:
    # generator: the caller's record is shared; the mutation happens after
    # the frame is created (a value argument at a generator / async protocol
    # parameter is BUGS.md#frame-protocol-value-arg)
    r = A(1)
    it = gen_text(r)  # tpyc: ok
    r.n = 7
    print("frame record", [s for s in it])
    # a subprotocol parameter shares the record too
    r2 = A(1)
    it2 = gen_sub(r2)  # tpyc: ok
    r2.n = 8
    print("frame subprotocol record", [s for s in it2])
    # a list at a generator Sized parameter is shared
    ys = [1, 2]
    il = gen_len(ys)  # tpyc: ok
    # as at a `list[int32]` parameter (BUGS.md#frame-held-container-mutation-warns)
    ys.append(3)  # tpyc: warning(/Mutation of 'ys' while borrowed/)
    print("frame sized list", [n for n in il])
    # iterators are shared: the caller and the generator interleave
    xs = [1, 2, 3, 4]
    li = iter(xs)
    gi = gen_iter(li)
    print("frame list iter", next(gi, -1), next(li, -1), [v for v in gi])
    arr: Array[int32, 4] = [10, 20, 30, 40]
    sp: Span[int32] = arr
    si = SpanIter(sp)
    gs = forward(si)
    print("frame spaniter", next(si, -1), next(gs, -1), next(si, -1), next(gs, -1))
    asyncio.run(frames_async())


def scalars_generic() -> None:
    print("float bool", text(1.5), text(True), rtext(Plain()).startswith("<__main__.Plain object at 0x"))  # tpyc: ok
    # (a Ptr rvalue at a generic is BUGS.md#generic-call-ptr-rvalue-cxx-error)
    a = A(3)
    pa = take_ptr(a)
    print("generic tuple ptr", gtext((1, "a")), gtext(pa))  # tpyc: ok


def main() -> None:
    a = A(1)
    h = Holder()
    free_fn(2, A(1), take_ptr(a), 3, (A(4), 5), [1, 2], h, ["a", ["b"]],
            "sv", String("st"), "c")
    free_fn(None, None, take_ptr(a), "u", (A(4), 5), [], h, "j",
            "", String(""), "d")
    print("holder", repr(h))
    positions(A(1), 2, 3)
    positions(None, None, "s")
    mutation()
    pointer_form()
    overload_pick()
    siblings()
    conditionals()
    frames()
    scalars_generic()


main()
