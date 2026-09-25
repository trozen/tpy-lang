# A lambda body is its return value: it takes the Callable/Fn return type as
# its type context (`lambda: []`, `lambda: list()`, `lambda: int()` at int32,
# a str view at a str result, an Optional or union result), a type written
# inside it resolves, and its params shadow same-named enclosing params.
import asyncio
from typing import Callable, Iterator
from tpy import Fn, Own, int32


class Pt:
    x: int32

    def __init__(self) -> None:
        self.x = 0


def take(f: Callable[[], list[int]]) -> int:
    xs = f()
    xs.append(7)
    return len(xs)


def take_fn(f: Fn[[], list[int]]) -> int:
    xs = f()
    xs.append(7)
    return len(xs)


def later(s: str) -> Callable[[], str]:
    # The slice is a view of `s`; the result slot owns its string.
    return lambda: s[0:2]  # tpyc: ok


class Holder:
    make: Callable[[], list[int]]

    def __init__(self) -> None:
        # A type spelled inside a lambda body.
        self.make = lambda: list[int]()  # tpyc: ok

    def fill(self, f: Callable[[], dict[str, int]]) -> int:
        d = f()
        d["k"] = 1
        return len(d)


class Seeded:
    count: int

    def __init__(self, f: Callable[[], list[int]]) -> None:
        xs = f()
        xs.append(1)
        self.count = len(xs)


MOD: Callable[[], list[str]] = lambda: []  # tpyc: ok


def gen() -> Iterator[int32]:
    zero: Callable[[], int32] = lambda: int()  # tpyc: ok
    yield zero()


async def co() -> int:
    return take(lambda: list())  # tpyc: ok


def free() -> None:
    empty: Callable[[], list[int]] = lambda: []  # tpyc: ok
    xs = empty()
    xs.append(1)
    print("free", xs, take(lambda: []), take(lambda: list()), take_fn(lambda: []))  # tpyc: ok
    zero: Callable[[], int32] = lambda: int()  # tpyc: ok
    print("free int32", zero())
    ys = list(map(lambda x: list[int](), [1, 2]))  # tpyc: ok
    ys[0].append(5)
    print("free map", ys)
    print("free str", later("hello")())
    # A view-typed parameter returned bare copies into the owned result.
    ident: Callable[[str], str] = lambda t: t  # tpyc: ok
    ident_b: Callable[[bytes], bytes] = lambda b: b  # tpyc: ok
    print("free view name", ident("ab"), len(ident_b(b"xyz")))
    # An Optional or union result slot takes the body as one of its members.
    opt: Callable[[], int32 | None] = lambda: 3  # tpyc: ok
    # Not called: its result is read in the wrong form,
    # BUGS.md#callable-optional-record-result-borrow-form.
    opt_rec: Callable[[], Pt | None] = lambda: Pt()  # tpyc: ok
    uni: Callable[[], int32 | str] = lambda: "u"  # tpyc: ok
    u = uni()
    print("free optional", opt(), u)
    # A ternary over two view params copies into the owned result.
    pick: Callable[[str, str], str] = lambda s, t: s if len(s) > 1 else t  # tpyc: ok
    print("free ternary", pick("a", "bc"), pick("ab", "c"))


def shadowing(s: str, o: Own[str], n: str | None, c: bool) -> None:
    # Each lambda param shadows the enclosing param of the same name: `s` is
    # the view the lambda takes, `o` is not the enclosing Own[str], `n` is
    # not the enclosing Optional.
    pick: Callable[[str, str], str] = lambda s, t: s if c else t  # tpyc: ok
    first: Callable[[str, str], str] = lambda o, t: o if c else t  # tpyc: ok
    ident: Callable[[str], str] = lambda n: n  # tpyc: ok
    print("shadow", s, o, n, pick("x", "y"), first("p", "q"), ident("i"))


def method() -> None:
    h = Holder()
    mk = h.make
    xs = mk()
    xs.append(2)
    print("method field", xs)
    print("method arg", h.fill(lambda: {}), h.fill(lambda: dict()))  # tpyc: ok


def ctor() -> None:
    print("ctor", Seeded(lambda: []).count)  # tpyc: ok


def module_level() -> None:
    m = MOD()
    m.append("z")
    print("module", m)


def generator() -> None:
    print("generator", list(gen()))


def comprehension() -> None:
    print("comprehension", [take(lambda: []) for _ in range(2)])  # tpyc: ok


def closure() -> None:
    def inner() -> int:
        f: Callable[[], list[str]] = lambda: list[str]()  # tpyc: ok
        xs = f()
        xs.append("a")
        return len(xs)
    print("closure", inner())


def main() -> None:
    free()
    shadowing("s", "o", None, False)
    method()
    ctor()
    module_level()
    generator()
    print("async", asyncio.run(co()))
    comprehension()
    closure()


main()
