# Overload ranking places each candidate's arguments where the call's one
# binding places them, keywords included, at every call kind.
import asyncio
from typing import Callable, Protocol, TypedDict, Unpack
from tpy import dispatch, dynamic, int32, int64


class Opts(TypedDict):
    x: int32


class Maybe(TypedDict, total=False):
    y: int32


class Rest(TypedDict):
    args: int32


class Keyed(Protocol):
    def m(self, *, k: int32) -> int32: ...


class KeyedAt[T](Protocol):
    def m(self, *, k: T) -> T: ...


class KeyedInt(KeyedAt[int32], Protocol):
    pass


class Conformer:
    n: int32

    def __init__(self) -> None:
        self.n = 10

    def m(self, *, k: int32) -> int32:
        return self.n + k


@dispatch
def scaled(x: int32, *, scale: int32) -> str:
    return f"scale {x * scale}"


@dispatch
def scaled(x: int32, *, label: str) -> str:
    return f"label {label}{x}"


@dispatch
def tagged[T](key: str, *, value: T) -> T:
    return value


@dispatch
def tagged(key: int32) -> int32:
    return key


@dispatch
def choose[T](x: T) -> str:
    return "generic"


@dispatch
def choose(x: int64) -> str:
    return "int64"


@dispatch
def pad(s: str, width: int32 = 4) -> str:
    return f"pad {s}:{width}"


@dispatch
def pad(n: int32, width: int32 = 2) -> str:
    return f"padn {n}:{width}"


@dispatch
def apply_to(xs: list[int32], *, fn: Callable[[int32], int32]) -> int32:
    return fn(xs[0])


@dispatch
def apply_to(s: str, *, fn: Callable[[int32], int32]) -> int32:
    return fn(len(s))


@dispatch
def conv(fn: Callable[[int32], int32], x: int32) -> str:
    return f"int {fn(x)}"


@dispatch
def conv(fn: Callable[[str], str], x: str) -> str:
    return f"str {fn(x)}"


def region(x: int32, /, **kw: Unpack[Opts]) -> str:
    return f"region {x} {kw['x']}"


def rest(*args: int32, **kw: Unpack[Rest]) -> str:
    return f"rest {len(args)} {kw['args']}"


@dispatch
def half(x: float) -> str:
    return f"half {x}"


@dispatch
def half(x: str) -> str:
    return f"half-s {x}"


def keyed(p: Keyed) -> int32:
    return p.m(k=1)


def keyed_at(p: KeyedInt) -> int32:
    return p.m(k=2)


class Shape:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    @dispatch
    def grow(self, *, by: int32) -> str:
        return f"by {self.n + by}"

    @dispatch
    def grow(self, *, to: str) -> str:
        return f"to {to}"

    @dispatch
    def conf(self, x: int32, /, **kw: Unpack[Opts]) -> str:
        return f"conf {x} {kw['x']}"

    @dispatch
    def conf(self, s: str) -> str:
        return f"conf-s {s}"

    @dispatch
    def fconf(self, x: float, /, **kw: Unpack[Opts]) -> str:
        return f"fconf {x} {kw['x']}"

    @dispatch
    def fconf(self, s: str) -> str:
        return f"fconf-s {s}"

    @dispatch
    def opt(self, x: int32, **kw: Unpack[Maybe]) -> str:
        return f"opt {x + kw.get('y', 0)}"

    @dispatch
    def opt(self, s: str) -> str:
        return f"opt-s {s}"

    @dispatch
    def __getitem__(self, i: int32) -> str:
        return f"at {i}"

    @dispatch
    def __getitem__(self, k: str) -> str:
        return f"key {k}"

    @dispatch
    def __add__(self, other: int32) -> int32:
        return self.n + other

    @dispatch
    def __add__(self, other: str) -> int32:
        return len(other) * 10


def free_function() -> None:
    v: int32 = 7
    # A keyword picks the variant that declares it.
    print("fn", scaled(2, scale=3), scaled(2, label="a"))  # tpyc: ok
    # A keyword-only argument is inference evidence.
    print("fn", tagged("k", value=5), tagged(4))  # tpyc: ok
    # The positional and the keyword spelling pick the same variant.
    print("fn", choose(v), choose(x=v))  # tpyc: ok
    # A default left out, a literal at the slot it binds by keyword.
    print("fn", pad("a"), pad(5, width=7))  # tpyc: ok
    # A lambda by keyword: the candidates agree on its parameter type ...
    print("fn", apply_to([4], fn=lambda n: n + 1), apply_to("ab", fn=lambda n: n * 10))  # tpyc: ok
    # ... or each candidate types it for itself.
    print("fn", conv(fn=lambda n: n + 1, x=3), conv(x="a", fn=lambda s: s + "b"))  # tpyc: ok
    # A keyword naming a positional-only parameter is a `**kwargs` field.
    print("fn", region(1, x=2))  # tpyc: ok
    # A keyword naming the `*args` parameter is a `**kwargs` field too.
    print("fn", rest(1, 2, args=3))  # tpyc: ok
    # An int keyword at a `float` parameter converts as its positional twin.
    print("fn", half(x=3), half(3))  # tpyc: ok


def method() -> None:
    s = Shape()
    # A keyword picks the method variant that declares it.
    print("method", s.grow(by=2), s.grow(to="big"))  # tpyc: ok
    # An overloaded method's `**kwargs` region, beside a positional-only
    # parameter of the field's name.
    print("method", s.conf(1, x=2), s.conf("z"))  # tpyc: ok
    # The region's int field named like a positional-only float parameter
    # stays an int (`1.0 2`): the keyword binds the region, not `x`.
    print("method", s.fconf(1.0, x=2))  # tpyc: ok
    # An overloaded method's `**kwargs` region called with no keyword:
    # the variant receives the empty region.
    print("method", s.opt(3), s.opt(3, y=1), s.opt("w"))  # tpyc: ok


def protocol() -> None:
    c = Conformer()
    # A protocol method's keyword-only parameter, declared on the protocol
    # and inherited at a type argument, takes its keyword.
    print("protocol", keyed(c), keyed_at(c))  # tpyc: ok
    c.n = 20
    print("protocol", keyed(c), keyed_at(c))


def subscript() -> None:
    s = Shape()
    # Each dunder invocation binds its key as its one argument.
    print("subscript", s[1], s["a"])  # tpyc: ok


def operator() -> None:
    s = Shape()
    # The operand binds the dunder's one parameter.
    print("operator", s + 2, s + "x")  # tpyc: ok


@dynamic
class Valued(Protocol):
    def value(self) -> int32: ...


class Seven:
    def value(self) -> int32:
        return 7


# Type section: an alias of an optional @dynamic protocol is valid where it
# is used, here a parameter: the alias body takes only the range rule.
type MaybeValued = Valued | None


def consume(p: MaybeValued) -> int32:  # tpyc: ok
    if p is None:
        return 0
    return p.value()


# Type section: a nested def naming its enclosing function's type parameter.
def nested[T](x: T) -> None:
    def inner() -> None:
        # A nested def names the enclosing function's type parameter.
        xs = list[T]()  # tpyc: ok
        ys: list[T] = []  # tpyc: ok
        # A generic T stored into owned storage copies where CPython aliases.
        xs.append(x)  # tpyc: warning(/may copy T/)
        ys.append(x)  # tpyc: warning(/may copy T/)
        print("nested", len(xs), len(ys))
    inner()


async def body() -> str:
    # A keyword picks the variant in an async body.
    return scaled(4, label="z")  # tpyc: ok


def main() -> None:
    free_function()
    method()
    protocol()
    subscript()
    operator()
    print("alias", consume(Seven()), consume(None))
    nested(3)
    print("async", asyncio.run(body()))


# A keyword picks the variant at module level.
print("module", scaled(3, scale=2))  # tpyc: ok
main()
