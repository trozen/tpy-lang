# Text rendering of Optional, Ptr and union values in f-strings and print:
# None / a null Ptr render "None", anything else its payload.
import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator
from tpy import (Ptr, Span, String, StrView, ReturnException, char,
                 error_return, int8, int32, readonly, take_ptr, uint8)


class A:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __repr__(self) -> str:
        return f"A({self.n})"


class B:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return f"B<{self.n}>"


class F:
    def __repr__(self) -> str:
        return "F()"

    def __format__(self, spec: str) -> str:
        return "F()"


class G(F):
    pass


class Negative(Exception, ReturnException):
    pass


class Color(Enum):
    RED = 1
    GREEN = 2


type Tree = int32 | list[Tree]


@dataclass
class D:
    s: str | None
    o: int32 | None


class Holder:
    p: Ptr[A]
    x: int32 | None

    def __init__(self, a: A, x: int32 | None) -> None:
        self.p = take_ptr(a)
        self.x = x
        # Constructor: an Optional parameter and a Ptr field.
        print(f"ctor: {x} {self.p}")  # tpyc: ok

    def show(self) -> None:
        # Method: an Optional field and a Ptr field, f-string and print.
        print(f"method: {self.x} {self.p} {self.x!r}")  # tpyc: ok
        print("method:", self.x, self.p)


def free(x: int32 | None, f: float | None, b: bool | None, s: str | None,
         o: A | None, xs: list[int32] | None) -> None:
    # Free function: Optional parameters of every payload kind.
    print(f"free: {x} {f} {b} {s} {o} {xs}")  # tpyc: ok
    print(f"free: {x!r} {f!r} {b!r} {s!r} {o!r}")  # tpyc: ok
    print(f"free: [{x!s:>6}] [{s!s:<4}]")  # tpyc: ok


def ptrs(p: Ptr[A], q: Ptr[float], t: Ptr[bool], i: Ptr[int8]) -> None:
    # Ptr parameters render their pointee, never an address.
    print(f"ptr: {p} {q} {t} {i}")  # tpyc: ok
    print(f"ptr: {p!r} {q!r}")  # tpyc: ok
    print("ptr:", p, q, t, i)


def null_ptrs(p: Ptr[A], q: Ptr[float]) -> None:
    # A null Ptr renders None.
    print(f"null: {p} {q} {p!r}")  # tpyc: ok
    print("null:", p, q)


def write_through(p: Ptr[A]) -> None:
    # A write through the pointer shows in the next render.
    p.n = 42
    print(f"write: {p}")  # tpyc: ok


def unions(u: float | str, r: A | B, n: int32 | str | None) -> None:
    # Unions: value members, reference members, with None.
    print(f"union: {u} {r} {n} {u!r} {n!r}")  # tpyc: ok
    # `!s` states that the spec pads the text: no warning.
    print(f"union: [{u!s:.2}]")  # tpyc: ok


def spec_text(u: float | str) -> None:
    # A spec without a conversion formats the str() text too, but warns:
    # CPython would format a held float as a number.
    print(f"spec: [{u:.2}] [{u:>5}]")  # tpyc: warning(/format spec on float \| str pads its str\(\) text; CPython passes it to the value's __format__/)


def spans(xs: Span[int32]) -> None:
    # A Span renders as the list it views.
    print(f"span: {xs} {xs!r}")  # tpyc: ok


def readonly_ptr(p: Ptr[readonly[A]]) -> None:
    # A readonly Ptr renders its pointee.
    print(f"readonly: {p}")  # tpyc: ok
    print("readonly:", p)  # tpyc: ok


def scalar_text_specs(f: float, n: int32, b: bool, c: char) -> None:
    # `!s` with a spec on a plain scalar pads its str() text, as CPython does.
    print(f"scalar: [{f!s:.2}] [{n!s:5}] [{b!s:>6}] [{c!s:>3}] [{c!s:.2}] [{c!s:^5s}]")  # tpyc: ok


def small_ints(x: int8 | None, y: uint8 | None) -> None:
    # An 8-bit Optional prints its number, not the byte.
    print("int8:", x, y)  # tpyc: ok


def empty_spec(n: int32 | None, big: int) -> None:
    # An empty spec is no spec: the plain text, no warning.
    print(f"empty: [{n:}] [{big:}]")  # tpyc: ok


def payloads(s: String | None, u: Ptr[A] | int32) -> None:
    # An Optional String payload; a union holding a Ptr, null or not.
    print(f"payload: {s} {u}")  # tpyc: ok


def held_format(o: F | None, p: Ptr[F], u: F | int32) -> None:
    # A record with __format__ held by an Optional, a Ptr or a union warns too.
    print(f"held: {o}")  # tpyc: warning(/F has __format__, which TPy does not call/)
    print(f"held: {p}")  # tpyc: warning(/F has __format__, which TPy does not call/)
    print(f"held: {u}")  # tpyc: warning(/F has __format__, which TPy does not call/)


def generic_print[T](p: Ptr[T], o: T | None) -> None:
    # A generic Ptr / Optional prints its payload as Python does.
    print("generic print:", p, o)  # tpyc: ok


def ptr_forms(s: Ptr[str], v: Ptr[StrView], u: Ptr[A | B],
              o: Ptr[int32 | None], r: Ptr[A | None], pp: Ptr[Ptr[int32]],
              a: Ptr[A], xs: Ptr[list[int32]]) -> None:
    # A Ptr prints as a pointer-repr Optional of its pointee does: a record
    # or a list streams through print_optional, a str, a union, an Optional
    # or a Ptr pointee prints its __str__ text. Only the record and list ones
    # can be set today (BUGS.md#take-ptr-union-local-rejects).
    print("ptr forms:", s, v, u, o, r, pp, a, xs)  # tpyc: ok


def ptr_tree(t: Ptr[Tree]) -> None:
    # A recursive-alias pointee streams through print_optional.
    print("ptr tree:", t)  # tpyc: ok


def inherited_format(g: G | None) -> None:
    # An inherited __format__ held by an Optional warns as well.
    print(f"inherit: {g}")  # tpyc: warning(/G has __format__, which TPy does not call/)


def none_param(n: None) -> None:
    # A None-typed parameter renders through the generic row.
    print(f"none: {n} {n!r}")  # tpyc: ok


def spec_warnings(r: B, data: bytes, anything: Any) -> None:
    # Compiled, never run: CPython raises TypeError for a non-empty spec on
    # these (object.__format__), where TPy pads the str() text and warns.
    print(f"[{r:>8}]")  # tpyc: warning(/format spec on B pads its str\(\) text/)
    print(f"[{data:>8}]")  # tpyc: warning(/format spec on bytes pads its str\(\) text/)
    print(f"[{anything:>8}]")  # tpyc: warning(/format spec on Any pads its str\(\) text/)
    print(f"[{None:>5}]")  # tpyc: warning(/format spec on None pads its str\(\) text/)


def gen(x: int32 | None, p: Ptr[A]) -> Iterator[str]:
    # Generator body.
    yield f"gen: {x} {p}"  # tpyc: ok


async def coro(x: int32 | None, p: Ptr[A]) -> str:
    # Async body.
    await asyncio.sleep(0)
    return f"async: {x} {p}"  # tpyc: ok


@error_return(Negative)
def checked(x: int32 | None) -> str:
    # @error_return body.
    if x is not None and x < 0:
        raise Negative
    return f"error_return: {x}"  # tpyc: ok


def misc(c: Color | None, tree: Tree, anything: Any) -> None:
    # Bare None, an Optional enum, a recursive alias, Any.
    print(f"misc: {None} {c} {tree} {anything} {None!r}")  # tpyc: ok


def generic[T](v: T) -> str:
    return f"{v}"


def main() -> None:
    a = A(1)
    f = 3.0
    t = True
    i: int8 = 7
    free(3, 2.5, True, "hi", a, [1, 2])
    free(None, None, None, None, None, None)
    ptrs(take_ptr(a), take_ptr(f), take_ptr(t), take_ptr(i))
    null_ptrs(Ptr[A](), Ptr[float]())
    write_through(take_ptr(a))
    print(f"write: {a}")
    unions(3.14159, B(2), None)
    unions("abc", a, 5)
    spec_text("abc")
    nums: list[int32] = [4, 5]
    spans(nums)
    readonly_ptr(take_ptr(a))
    h = Holder(a, 9)
    h.show()
    for line in gen(None, take_ptr(a)):
        print(line)
    print(asyncio.run(coro(1, take_ptr(a))))
    try:
        print(checked(None))
    except Negative:
        print("error_return: negative")
    # Comprehension element.
    opts: list[int32 | None] = [1, None]
    print([f"comp: {v}" for v in opts])  # tpyc: ok
    # Closure.
    label = "closure"

    missing: int32 | None = None

    def show() -> str:
        return f"{label}: {missing}"  # tpyc: ok

    print(show())
    # try / finally.
    try:
        print(f"try: {h.x}")  # tpyc: ok
    finally:
        print(f"finally: {h.p}")  # tpyc: ok
    # match arm.
    hx = h.x
    match hx:
        case None:
            print("match: none")
        case _:
            print(f"match: {hx} {h.p}")  # tpyc: ok
    tree: Tree = [1, [2, 3]]
    anything: Any = 5
    misc(Color.GREEN, tree, anything)
    misc(None, 4, anything)
    # A generic T at a scalar, a record and an Optional.
    nothing: int32 | None = None
    print("generic:", generic(4), generic(a), generic(nothing))
    # A dataclass repr over Optional fields.
    print(D("q", None))
    # A record defining __format__: TPy renders its str() text, and warns.
    print(f"format: {F()}")  # tpyc: warning(/F has __format__, which TPy does not call/)
    scalar_text_specs(3.14159, 42, True, "x")
    small_ints(5, 200)
    small_ints(None, None)
    empty_spec(7, 2**70)
    payloads(String("str"), take_ptr(a))
    payloads(None, Ptr[A]())
    payloads(None, 6)
    fmt = F()
    held_format(fmt, take_ptr(fmt), fmt)
    generic_print(take_ptr(f), f)
    generic_print(take_ptr(t), t)
    generic_print(take_ptr(i), i)
    lst: list[int32] = [8, 9]
    ptr_forms(Ptr[str](), Ptr[StrView](), Ptr[A | B](), Ptr[int32 | None](),
              Ptr[A | None](), Ptr[Ptr[int32]](), take_ptr(a), take_ptr(lst))
    ptr_forms(Ptr[str](), Ptr[StrView](), Ptr[A | B](), Ptr[int32 | None](),
              Ptr[A | None](), Ptr[Ptr[int32]](), Ptr[A](), Ptr[list[int32]]())
    ptr_tree(Ptr[Tree]())
    inherited_format(G())
    none_param(None)


# Module level: an Optional global and a Ptr global.
g_opt: int32 | None = None
g_a = A(5)
g_ptr: Ptr[A] = take_ptr(g_a)
print(f"global: {g_opt} {g_ptr}")  # tpyc: ok
print("global:", g_opt, g_ptr)
main()
