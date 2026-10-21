# Operator dunder sets rank like a call and pin a literal operand to the pick;
# winners declared first (loser-first twin: calls/dispatch_rank_declared_shape_order).
from datetime import timedelta
from typing import Callable, Iterator, Literal
from tpy import dispatch, float32, int32, int64, Own, String


class L:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, xs: list[int32]) -> int32:
        return 32

    @dispatch
    def __add__(self, xs: list[int64]) -> int32:
        return 64


class N:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @dispatch
    def __mul__(self, x: int) -> int32:
        return 2

    @dispatch
    def __mul__(self, x: float) -> int32:
        return 1

    @dispatch
    def __rmul__(self, x: int) -> int32:
        return 20

    @dispatch
    def __rmul__(self, x: float) -> int32:
        return 10

    @dispatch
    def __iadd__(self, x: int) -> "N":
        self.hits += 2
        return self

    @dispatch
    def __iadd__(self, x: float) -> "N":
        self.hits += 1
        return self

    @dispatch
    def __lt__(self, x: int) -> bool:
        print("spellings: lt int")
        return True

    @dispatch
    def __lt__(self, x: float) -> bool:
        print("spellings: lt float")
        return True

    @dispatch
    def __gt__(self, x: int) -> bool:
        print("spellings: gt int")
        return True

    @dispatch
    def __gt__(self, x: float) -> bool:
        print("spellings: gt float")
        return True

    @dispatch
    def __eq__(self, x: int) -> bool:
        print("spellings: eq int")
        return False

    @dispatch
    def __eq__(self, x: float) -> bool:
        print("spellings: eq float")
        return False


class W:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    @dispatch
    def __add__(self, x: int) -> Own["W"]:
        return W(self.n + 2)

    @dispatch
    def __add__(self, x: float) -> Own["W"]:
        return W(self.n + 1)


class Opt:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: float | None) -> int32:
        return 1

    @dispatch
    def __add__(self, x: int32) -> int32:
        return 32


class Lit3:
    def __init__(self) -> None:
        pass

    def __add__(self, x: Literal[3]) -> int32:
        return 7


class LitWidth:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: int32) -> int32:
        return 32

    @dispatch
    def __add__(self, x: int64) -> int32:
        return 64

    @dispatch
    def __add__(self, x: int) -> int32:
        return 0


class Text:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: str) -> int32:
        return 1

    @dispatch
    def __add__(self, x: String) -> int32:
        return 2


class OptStr:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: str | None) -> int32:
        return 1

    @dispatch
    def __add__(self, x: bool) -> int32:
        return 2

    @dispatch
    def m(self, x: str | None) -> str:
        return "opt"

    @dispatch
    def m(self, x: bool) -> str:
        return "bool"


@dispatch
def f(s: str) -> str:
    return "str"


@dispatch
def f(s: String) -> str:
    return "String"


@dispatch
def cross(x: Literal["a"], y: int64) -> str:
    return "literal"


@dispatch
def cross(x: str, y: int32) -> str:
    return "plain"


class Holder:
    v: L

    def __init__(self) -> None:
        self.v = L()

    def add(self) -> int32:
        xs = [1, 2]
        # Method: the undecided list ranks at the default int, as a call's.
        return self.v + xs  # tpyc: ok


def free_function() -> None:
    v = L()
    xs = [1, 2]
    # Free function: `list[int32]` wins, so `xs` is a `list[int32]`.
    print("free_function:", v + xs)  # tpyc: ok


def method() -> None:
    print("method:", Holder().add())


def gen() -> Iterator[int32]:
    v = L()
    xs = [1, 2]
    # Generator: the same pick across a suspension.
    yield v + xs  # tpyc: ok


def generator() -> None:
    for r in gen():
        print("generator:", r)


def comprehension() -> None:
    v = L()
    xs = [1, 2]
    # Comprehension: the operator in the element expression.
    print("comprehension:", [v + xs for _ in range(2)])  # tpyc: ok


def lam() -> None:
    v = L()
    xs = [1, 2]
    # the lambda copies `v` and `xs`; nothing mutates them, so the output matches
    g: Callable[[], int32] = lambda: v + xs  # tpyc: warning(/copies the object 'v'/) warning(/copies the object 'xs'/)
    print("lambda:", g())


def spellings() -> None:
    v = N()
    # Binary, reflected and explicit spellings rank the literal at `int`.
    print("spellings:", v * 3, 3 * v, v.__mul__(3))  # tpyc: ok
    # In-place: `__iadd__(int)` runs.
    v += 3  # tpyc: ok
    print("spellings: iadd", v.hits)
    # Comparison, chained comparison, and `!=` derived from `__eq__`.
    lt = v < 3  # tpyc: ok
    chained = v > 3 > 1  # tpyc: ok
    ne = v != 3  # tpyc: ok
    print("spellings:", lt, chained, ne)
    # A walrus middle operand binds once; the pin converts it where `>` runs.
    walrus = v > (t := 3) > 1  # tpyc: ok
    print("spellings:", walrus, t)
    flag = v.hits > 0
    # A conditional middle operand binds unconverted, pinned where `>` runs.
    cond = v > (3 if flag else 3) > 1  # tpyc: ok
    print("spellings:", cond)


def aug_fallback() -> None:
    w = W(0)
    # `+=` with only `__add__`: `w = w + 3` runs `__add__(int)`.
    w += 3  # tpyc: ok
    print("aug_fallback:", w.n)


def optional_pin() -> None:
    o = Opt()
    # The float literal takes the Optional variant, pinned as an Optional.
    print("optional_pin:", o + 3.0)  # tpyc: ok
    ostr = OptStr()
    # A str literal at an Optional str slot is pinned as an Optional too.
    print("optional_pin:", ostr + "x", ostr.m("y"))  # tpyc: ok


def timedelta_ops() -> None:
    d = timedelta(microseconds=1)
    big: int = 9007199254740993
    # 2**53 + 1 at `__mul__(int)` in both spellings: exact, as in CPython.
    print("timedelta:", d * 9007199254740993, d.__mul__(9007199254740993))  # tpyc: ok
    print("timedelta:", d * 9007199254740993 == d.__mul__(9007199254740993))  # tpyc: ok
    print("timedelta:", (d * big) / 9007199254740993)  # tpyc: ok
    print("timedelta:", d * 3, d * 1.5, d / 4)  # tpyc: ok


def builtin_rows() -> None:
    x = 1.5
    y = float32(1.5)
    k: int64 = 7
    n: int = 10
    a: int32 = 4
    # Builtin numeric operators keep their picks: a literal takes the
    # other operand's type.
    print("builtin:", x * 1000, 2 * x, x ** 2)  # tpyc: ok
    print("builtin:", y * 3, y * 2.0, 2.0 * y)  # tpyc: ok
    print("builtin:", 1 + 2.0, 2.0 ** 70)  # tpyc: ok
    print("builtin:", k + 3, 3 << k, n + 3, a + n)  # tpyc: ok


def literal_values(mode: Literal["r", "w"], one: Literal["a"]) -> None:
    # A Literal value takes its own base type over another conversion.
    print("literal:", "text:" + mode)  # tpyc: ok
    t = Text()
    # The same at a user dunder: `str` over `String`.
    print("literal:", t + mode)  # tpyc: ok
    print("literal:", f(one))  # tpyc: ok
    print("literal:", cross(one, 3))  # tpyc: ok


def lit3() -> Literal[3]:
    return 3


def literal_param() -> None:
    v = Lit3()
    # A Literal operand at a Literal parameter.
    print("literal:", v + lit3())  # tpyc: ok
    lw = LitWidth()
    # A `Literal[3]` value takes its base `int32` over `int64` and `int`.
    print("literal:", lw + lit3())  # tpyc: ok


def main() -> None:
    free_function()
    method()
    generator()
    comprehension()
    lam()
    spellings()
    aug_fallback()
    optional_pin()
    timedelta_ops()
    builtin_rows()
    literal_values("r", "a")
    literal_param()


v_mod = L()
xs_mod = [1, 2]
# Module level: the same pick for a global operand.
print("module:", v_mod + xs_mod)  # tpyc: ok
main()
