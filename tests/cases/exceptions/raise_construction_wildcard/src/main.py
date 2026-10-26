# `raise X[_, U](args)` leaves the `_` to inference as the expression does,
# whether the class is named bare or through a dotted path.
from typing import Protocol
from tpy import Own, dynamic, readonly


@dynamic
class Shows(Protocol):
    @readonly
    def show(self) -> str: ...


class Carrier(Exception, Shows):
    """The non-generic base the generic exceptions are caught by
    (BUGS.md#generic-exception-except-clause); `show` reads the payload."""

    @readonly
    def show(self) -> str:
        return "carrier"


class Pair[T, U](Carrier):
    def __init__(self, a: Own[T], b: Own[U]) -> None:
        super().__init__("pair")
        self.a = a
        self.b = b

    @readonly
    def show(self) -> str:
        return str(self.a) + " " + str(self.b)


class Outer:
    class Pair[T, U](Carrier):
        def __init__(self, a: Own[T], b: Own[U]) -> None:
            super().__init__("outer-pair")
            self.a = a
            self.b = b

        @readonly
        def show(self) -> str:
            return str(self.a) + " " + str(self.b)


def dotted_wildcard() -> None:
    # the expression twin: `int` where the argument alone infers int32
    x = Outer.Pair[_, int](1, 2)  # tpyc: ok
    x.b += 2147483647
    print("dotted-expr", x.a, x.b)
    # dotted: the wildcard is inferred, the written `int` kept (past int32)
    raise Outer.Pair[_, int](1, 2147483648)  # tpyc: ok


def bare_wildcard() -> None:
    # bare: the same
    raise Pair[_, int](3, 2147483649)  # tpyc: ok


def main() -> None:
    try:
        dotted_wildcard()
    except Carrier as e:
        print("dotted", e.show())
    try:
        bare_wildcard()
    except Carrier as e:
        print("bare", e.show())


main()
