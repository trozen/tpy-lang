# A value read of an integer constant generic `N` is a value of the default
# int (int32 here), converted once where it is read; a widening sink converts
# straight from `N`. Positions: method, nested def, lambda (module level does
# not apply: `N` is class-scoped).
from tpy import Fn, dispatch, int32, int64, uint8


@dispatch
def pick(x: int32) -> str:
    return "int32"


@dispatch
def pick(x: int) -> str:
    return "int"


@dispatch
def take(x: uint8) -> str:
    return "uint8"


@dispatch
def take(x: int64) -> str:
    return "int64"


class Holder[T]:
    def __init__(self, x: T) -> None:
        # A bare `T` field may hold a reference type: the store warns.
        self.x = x  # tpyc: warning(/may copy T into field/)


def call(f: Fn[[], int32]) -> int32:
    return f()


class Box[N: int]:
    def run(self) -> None:
        # Operators compute in int32.
        print("ops", -N, ~N, N + 1, N < 5)  # tpyc: ok
        x = 0
        # The augmented operand is an int32.
        x += N  # tpyc: ok
        print("aug", x)
        # A generic record's `T` binds to int32.
        h = Holder(N)  # tpyc: ok
        print("holder", h.x)
        # The constructors take the int32 variant.
        print("ctor", float(N), str(N), int32(N))  # tpyc: ok
        # A wider declaration converts from `N` itself.
        c: int64 = N  # tpyc: ok
        print("decl", c)
        # Overloads rank `N` as an int32: it widens to int64, not to uint8.
        print("fn", pick(N), take(N))  # tpyc: ok

    def nested(self) -> None:
        def inner() -> int64:
            # A return at int64 converts from `N` itself.
            return N  # tpyc: ok

        print("nested", inner())
        # A lambda's body reads `N` as an int32.
        print("lambda", call(lambda: N * 2))  # tpyc: ok


class Small[M: int]:
    def run(self) -> None:
        # A narrowing constructor checks the int32 value.
        print("small", uint8(M))  # tpyc: ok


def main() -> None:
    Box[3]().run()
    Box[3]().nested()
    Box[16777217]().run()
    Small[200]().run()


main()
