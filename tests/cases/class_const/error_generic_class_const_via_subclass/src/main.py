# Error: a non-generic subclass of a generic class can't access an
# inherited class constant -- the type-args at the access site are lost.
# Phase 9 v1 doesn't yet plumb the parent's concrete instantiation through
# the subclass; access through a `C[T]` instance instead.
from typing import Final
from tpy import Int32


class C[T]:
    MAX: Final[Int32] = 10


class Child(C[Int32]):
    pass


def main() -> None:
    obj = Child()
    print(obj.MAX)  # tpyc: error(/class constant 'MAX' on generic class 'C' cannot be accessed through subclass 'Child'/)


main()
