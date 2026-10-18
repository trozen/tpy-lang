# Callables whose parameter defaults name constants where the def is
# written; main.py calls them from scopes that bind those names to other
# values, or do not bind them at all.
import enum
from dataclasses import dataclass, field
from typing import ClassVar, Final, Iterator, Protocol, overload
from tpy import int32, int64, StrView, dispatch
import consts

K: Final[int32] = 7
_K: Final[int32] = 5
S: Final[str] = "hello"
PAIR: Final[tuple[int32, int32]] = (7, 2)


class E(enum.Enum):
    A = 1
    B = 2


class C:
    X: Final[int32] = 11
    K: Final[int32] = 17
    S: Final[str] = "class hello"
    n: int32

    # The class body's K (17), not the module's: a default binds in the
    # scope its def is written in.
    def __init__(self, a: int32 = K, b: int32 = 0) -> None:
        self.n = a * 10 + b

    def m(self, x: int32 = K, y: int32 = 0) -> int32:
        return x * 10 + y

    @classmethod
    def cm(cls, x: int32 = X, y: int32 = 0) -> int32:
        return x * 10 + y

    @staticmethod
    def sm(x: int32 = X, y: int32 = 0) -> int32:
        return x * 10 + y


class Late:
    def __init__(self) -> None:
        pass

    # The module's K (7): the class body binds its own K only after the def.
    def m(self, x: int32 = K, y: int32 = 0) -> int32:
        return x * 10 + y

    K: Final[int32] = 17


class Holder:
    K: Final[int32] = 7


class LateDotted:
    def __init__(self) -> None:
        pass

    # The module's Holder (7): the class body binds its nested Holder only
    # after the def.
    def m(self, x: int32 = Holder.K, y: int32 = 0) -> int32:
        return x * 10 + y

    # The module's enum E (B = 2), for the same reason.
    def e(self, k: E = E.B, y: int32 = 0) -> int32:
        return k.value * 10 + y

    class Holder:
        K: Final[int32] = 17

    class E(enum.Enum):
        A = 5
        B = 6


@dataclass
class LateRec:
    # The module's Holder (7) for a field default, as for LateDotted.m.
    n: int32 = Holder.K

    class Holder:
        K: Final[int32] = 13


class LateCtor:
    def __init__(self) -> None:
        pass

    # tpy's int64 (7): the class body binds its own int64 only after the
    # def.
    def m(self, x: int64 = int64(K), y: int32 = 0) -> int64:
        return x * 10 + y

    @staticmethod
    def int64(v: int32) -> int64:
        return int64(v + 100)


@dataclass
class Pt:
    x: int32 = 4


# The factories are this module's Pt and the builtin dict, whatever the
# constructing scope names Pt or dict.
@dataclass
class Made:
    p: Pt = field(default_factory=Pt)
    ys: dict[int32, int32] = field(default_factory=dict)
    c: int32 = 0


class W:
    K: int32

    def __init__(self) -> None:
        self.K = 1000

    # The module's K (7): an instance field's annotation binds nothing in
    # the class body, and the C++ default argument must not read the field.
    def m(self, x: int32 = K, y: int32 = 0) -> int32:
        return x * 10 + y


class LateField:
    def __init__(self) -> None:
        pass

    # The module's K (7): the class body binds its field K only after the
    # def.
    def m(self, x: int32 = K, y: int32 = 0) -> int32:
        return x * 10 + y

    K: int32 = 11


# The factory is the builtin list: the class body binds its own `list`
# only after the field naming it.
@dataclass
class MadeLater:
    xs: list[int32] = field(default_factory=list)
    list: int32 = 3


class CV:
    V: ClassVar[int32] = 13


class Box[T]:
    F: Final[str] = "box F"

    def __init__(self, v: T) -> None:
        pass


class Derived(C):
    # The module's K (7): a class body does not see its bases' constants,
    # so C's K (17) is not this default's.
    def m2(self, x: int32 = K, y: int32 = 0) -> int32:
        return x * 10 + y


# The module's K (7), not the parameter named K before it.
def shadowed(K: int32, x: int32 = K) -> int32:
    return K * 10 + x


@dataclass
class Rec:
    n: int32 = C.X


class G[T]:
    def __init__(self) -> None:
        pass

    def g(self, v: T, x: int32 = K, y: int32 = 0) -> int32:
        return x * 10 + y


class P(Protocol):
    def step(self, x: int32 = K, y: int32 = 0) -> int32: ...


class Stepper:
    def __init__(self) -> None:
        pass

    def step(self, x: int32 = K, y: int32 = 0) -> int32:
        return x * 10 + y


def f(a: int32, b: int32 = K, c: int32 = 0) -> int32:
    return a * 100 + b * 10 + c


def fp(b: int32 = _K, c: int32 = 0) -> int32:
    return b * 10 + c


def fw(b: int64 = int64(K), c: int32 = 0) -> int64:
    return b * 10 + c


def fe(e: E = E.B, c: int32 = 0) -> int32:
    return e.value * 10 + c


def fx(x: int32 = C.X, c: int32 = 0) -> int32:
    return x * 10 + c


def fm(x: int32 = consts.Q, c: int32 = 0) -> int32:
    return x * 10 + c


def ft(t: tuple[int32, int32] = PAIR, c: int32 = 0) -> int32:
    return t[0] * 10 + c


# A ClassVar default reads the slot at the call where CPython binds it at
# the def (BUGS.md#classvar-default-read-at-call); nothing writes CV.V, so
# the two agree here.
def fcv(x: int32 = CV.V, c: int32 = 0) -> int32:
    return x * 10 + c


@dispatch
def d(x: int32, k: int32 = K, y: int32 = 0) -> int32:
    return k * 10 + y


@dispatch
def d(x: str) -> int32:
    return 0


@overload
def ov(x: int32, k: int32 = K, y: int32 = 0) -> int32: ...
@overload
def ov(x: str, k: int32 = K, y: int32 = 0) -> int32: ...
def ov(x: int32 | str, k: int32 = K, y: int32 = 0) -> int32:
    if isinstance(x, str):
        return 0
    return k * 10 + y


def view(x: str = S, n: int32 = 0) -> StrView:
    return x


def gen(s: StrView = S, tail: int32 = 0) -> Iterator[StrView]:
    yield s


# A class constant read through an instance is the class's static storage,
# not the instance's.
def read(c: C) -> StrView:
    return c.S
