# An integer type argument may be any value of 0..2**64-1, at every use of
# `N`: a numeric read, a type argument, an array size, a builtin constructor.
from tpy import Array, int8, int32, int64, uint64
from tplib import FixStr


class Wide[N: int]:
    def run(self) -> None:
        # A wider declaration converts from `N` itself, not through int32.
        b: int = N  # tpyc: ok
        print("wide int", b)


class Wide64[N: int]:
    def run(self) -> None:
        # A wide read is one conversion from `N` and keeps the full value.
        c: int64 = N  # tpyc: ok
        d: float = N  # tpyc: ok
        # An optional int64 keeps the full value, as the scalar does.
        o: int64 | None = N  # tpyc: ok
        print("wide int64", c, d, o)


class Unsigned[N: int]:
    def run(self) -> None:
        # The largest argument at uint64: one plain conversion from `N`.
        u: uint64 = N  # tpyc: ok
        print("uint64", u)


class Narrow[N: int]:
    def run(self) -> None:
        # One checked conversion from `N` straight to int8, not via int32.
        x: int8 = N  # tpyc: ok
        # An optional payload converts as the scalar does: one int8 check.
        m: int8 | None = N  # tpyc: ok
        # Printed through int(): an int8 | None prints its raw byte
        # (BUGS.md#print-optional-int8-raw-byte).
        if m is not None:
            print("int8", x, int(m))


class Sizes[N: int]:
    # `N` is only an array size and a type argument here: never refused.
    def empty(self, a: Array[int32, N] | None, s: FixStr[N] | None) -> bool:
        return a is None and s is None


class Passes[N: int]:
    # `N` is only passed on as a type argument of a class that never reads it.
    def empty(self, s: Sizes[N] | None) -> bool:
        return s is None


class Floats[N: int]:
    def run(self) -> None:
        # An optional float and an optional int hold the largest argument.
        f: float | None = N  # tpyc: ok
        i: int | None = N  # tpyc: ok
        print("optional", f, i)


class Built[N: int]:
    def run(self) -> None:
        # A builtin constructor naming `N` in its element type takes only
        # the range rule, not the annotation position rules.
        xs = list[Array[int32, N]]()  # tpyc: ok
        print("built", len(xs))


def identity[T](x: T) -> T:
    return x


class Pair[A, B]:
    def __init__(self, a: A, b: B) -> None:
        # A generic A / B stored into owned storage copies where CPython aliases.
        self.a = a  # tpyc: warning(/may copy A/)
        self.b = b  # tpyc: warning(/may copy B/)


def outer[T](x: T) -> None:
    def inner() -> None:
        # Explicit type arguments in a nested def name the enclosing
        # function's type parameter (not CPython: see no_cpython.txt).
        y = identity[T](x)  # tpyc: ok
        p = Pair[T, _](x, 3)  # tpyc: ok
        print("explicit", y, p.b)
    inner()


class Edge[N: int]:
    def run(self) -> None:
        # A default-int read of the largest int32 fits its checked conversion.
        print("edge", N + 0, float(N))  # tpyc: ok


def main() -> None:
    Wide[18446744073709551615]().run()  # tpyc: ok
    Wide64[3000000000]().run()  # tpyc: ok
    Unsigned[18446744073709551615]().run()  # tpyc: ok
    Narrow[100]().run()  # tpyc: ok
    print("sized", Sizes[3000000000]().empty(None, None))  # tpyc: ok
    print("passes", Passes[18446744073709551615]().empty(None))  # tpyc: ok
    Floats[18446744073709551615]().run()  # tpyc: ok
    Built[3]().run()  # tpyc: ok
    outer(7)
    Edge[2147483647]().run()  # tpyc: ok


main()
