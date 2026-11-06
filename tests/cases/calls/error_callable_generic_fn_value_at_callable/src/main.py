# An Fn parameter's value bound to a Callable whose return is the generic's
# type parameter is refused at every instantiation: the Callable keeps its
# own result, and the Fn may hand back a borrow.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import Fn, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


gp = P(1)


def get_gp() -> P:
    return gp


def use[T](f: Fn[[], T]) -> Callable[[], T]:
    # The subject: a borrow-returning Fn at Callable[[], T].
    g: Callable[[], T] = f  # tpyc: error(/it returns a borrow of T where the Callable keeps its own T/)
    return g


def main() -> None:
    g = use[P](get_gp)
    g().n += 10
    print(gp.n)


main()
