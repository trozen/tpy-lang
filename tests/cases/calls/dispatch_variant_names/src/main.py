# Each variant of a free overload set has its own C++ name, so the emitted
# call names the variant sema resolved and C++ overload resolution never
# re-decides it. Sections: literal set, mixed set with a defaulted Literal
# parameter, a user function named like a variant, cross-module, function
# reference, typing.overload, Literal vs bool, list shape vs bare T, and the
# sets that keep their names (native, methods).
import math
from typing import Callable, Literal, Protocol, overload
from tpy import auto_readonly, dispatch, int32
import variant_lib
from variant_lib import Box, grow as enlarge, mode_bits


class Dog:
    def __init__(self, nm: str) -> None:
        self.nm = nm


# --- literal: "a-b" and "a_b" spell the same readable suffix ---
@dispatch
def pick(m: Literal["a-b"]) -> str:
    return "dash"


@dispatch
def pick(m: Literal["a_b"]) -> str:
    return "underscore"


@dispatch
def pick(m: Literal["r", "rb"], d: Dog) -> str:
    return "mode " + m + " " + d.nm


def literal_section() -> None:
    print("literal:", pick("a-b"))  # the dash variant, not its suffix twin
    print("literal:", pick("a_b"))  # the underscore variant
    d = Dog("Rex")
    print("literal:", pick("rb", d))  # a literal variant beside other params
    d.nm = "Max"
    print("literal:", pick("r", d))


# --- mixed set: a plain variant beside a defaulted Literal parameter ---
@dispatch
def g(x: int32) -> str:
    return "int " + str(x)


@dispatch
def g(x: str, mode: Literal["r", "rb"] = "r") -> str:
    return "str " + x + " " + mode


def mixed_section() -> None:
    print("mixed:", g(1))  # tpyc: ok -- the int32 variant
    print("mixed:", g("a"))  # tpyc: ok -- the str variant, mode defaulted
    print("mixed:", g("a", "rb"))  # tpyc: ok -- the str variant, mode given


# --- a user function named like a variant's readable C++ name: the variant
# takes `describe__Dog__v0`, the user function keeps `describe__Dog` ---
class Cat:
    def __init__(self, nm: str) -> None:
        self.nm = nm


@dispatch
def describe(x: Dog) -> str:
    return "dog " + x.nm


@dispatch
def describe(x: Cat) -> str:
    return "cat " + x.nm


def describe__Dog(x: Dog) -> str:
    return "user " + x.nm


# a class named like a variant: the variant takes `tag__Dog__v0`
class tag__Dog:
    k: int

    def __init__(self) -> None:
        self.k = 3


@dispatch
def tag(x: Dog) -> str:
    return "tag " + x.nm


@dispatch
def tag(x: int) -> str:
    return "tag int"


def name_clash_section() -> None:
    d = Dog("Rex")
    print("clash:", describe(d))  # tpyc: ok -- the renamed Dog variant
    print("clash:", describe(Cat("Tom")))
    print("clash:", describe__Dog(d))  # tpyc: ok -- the user function
    d.nm = "Max"
    print("clash:", describe(d), describe__Dog(d))
    print("clash:", tag(d), tag(1), tag__Dog().k)  # tpyc: ok -- class beside the set


# --- cross-module: dotted call and from-import alias ---
def cross_module_section() -> None:
    b = Box(1)
    print("cross:", variant_lib.grow(b))  # `m.f(x)` names the Box variant
    b.w += 10
    print("cross: w", b.w)
    xs = [1]
    print("cross:", enlarge(xs))  # an alias names the list variant
    xs.append(5)
    print("cross: xs", xs)
    print("cross:", mode_bits("r"), mode_bits("w"))  # an imported Literal typing.overload set


# --- function reference: the variant matched to the Callable slot ---
@dispatch
def label(x: int) -> str:
    return "int " + str(x)


@dispatch
def label(x: str) -> str:
    return "str " + x


def apply(f: Callable[[int], str], v: int) -> str:
    return f(v)


def reference_section() -> None:
    print("ref:", apply(label, 7))  # `label` names the int variant


# --- typing.overload: stubs plus one implementation ---
@overload
def kind(x: int) -> str: ...
@overload
def kind(x: Dog) -> str: ...
def kind(x: int | Dog) -> str:
    if isinstance(x, int):
        return "int"
    return "dog " + x.nm


# a user def named like a typing.overload specialization: the
# specialization takes `kind__int__v0`
def kind__int(x: int) -> str:
    return "user int"


def typing_overload_section() -> None:
    print("overload:", kind(3))  # the int specialization
    print("overload:", kind__int(5))  # tpyc: ok -- the user function
    d = Dog("Rex")
    print("overload:", kind(d))  # the Dog specialization
    d.nm = "Max"
    print("overload:", kind(d))


# --- a Literal[1] variant beside a bool one: True is not the literal 1 ---
@dispatch
def flag(x: Literal[1]) -> str:
    return "one"


@dispatch
def flag(x: bool) -> str:
    return "bool"


def guard_section() -> None:
    print("guard:", flag(True), flag(1))  # tpyc: ok


# --- a generic over a list shape beside a bare T: the shape variant takes
# only a list. Declared first on purpose: the other order hits
# BUGS.md#overload-rank-protocol-vs-generic ---
@dispatch
def wrap[T](xs: list[T]) -> str:
    return "shape " + str(len(xs))


@dispatch
def wrap[T](x: T) -> str:
    return "bare"


def shape_section() -> None:
    xs: list[int32] = [4, 5]
    print("shape:", wrap(xs), wrap(3))  # tpyc: ok
    xs.append(6)
    print("shape:", wrap(xs))


# --- a generic over a list shape returning T beside a protocol variant:
# the shape variant wins for a list ---
class HasLen(Protocol):
    def __len__(self) -> int: ...


@dispatch
def head(x: HasLen) -> int32:
    return -1


@dispatch
def head[T](xs: list[T]) -> T:
    return xs[0]


def head_section() -> None:
    xs: list[int32] = [7, 8]
    print("head:", head(xs))  # tpyc: ok
    xs[0] = 9
    print("head:", head(xs))


# --- names kept: a native variant set and an @auto_readonly method pair ---
class Shelf:
    top: Box

    def __init__(self) -> None:
        self.top = Box(1)

    @auto_readonly
    def first(self) -> Box:
        return self.top


def peek(s: Shelf) -> int:
    return s.first().w


def kept_section() -> None:
    print("kept:", math.log(1.0), math.log(8.0, 2.0))  # native + one bodied variant
    s = Shelf()
    s.first().w = 4  # the mutable clone of the pair
    print("kept:", peek(s))  # the const clone of the pair


def main() -> None:
    literal_section()
    mixed_section()
    name_clash_section()
    cross_module_section()
    reference_section()
    typing_overload_section()
    guard_section()
    shape_section()
    head_section()
    kept_section()


main()
