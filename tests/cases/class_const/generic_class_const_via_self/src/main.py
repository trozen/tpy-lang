# Phase 9: `self.MAX` inside a method of a generic class. self has type
# `C[T]` (with T a TypeParamRef in template scope), so codegen emits
# `C<T>::MAX` -- valid in the template body.
from typing import Final
from tpy import Int32


class Bounded[T]:
    LIMIT: Final[Int32] = 7

    def __init__(self) -> None:
        pass

    def at_limit(self, n: Int32) -> bool:
        return n >= self.LIMIT


def main() -> None:
    b = Bounded[Int32]()
    print(b.at_limit(5))
    print(b.at_limit(7))
    print(b.at_limit(8))


main()
