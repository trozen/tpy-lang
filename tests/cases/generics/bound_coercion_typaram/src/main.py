# The Ptr[U] -> Ptr[T] coercion via a type-param bound (`U: T`) is accepted
# in a generic method body -- the shape a single-allocation Rc factory needs.
# Calling such a factory is gated on generic inference (separate work), so
# this pins only the body-level acceptance of the coercion.
from __future__ import annotations
from tpy import Ptr, Own


class Holder[T]:
    _p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self._p = p

    @staticmethod
    def make[U: T](p: Ptr[U]) -> Own[Holder[T]]:
        return Holder[T](p)  # tpyc: ok


def main() -> None:
    print("compiled")


main()
