from __future__ import annotations
# Body-level acceptance of `Ptr[U] -> Ptr[T]` via a type-param bound `U: T`
# in a generic method (calling the factory is covered by bound_factory_inference).
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
