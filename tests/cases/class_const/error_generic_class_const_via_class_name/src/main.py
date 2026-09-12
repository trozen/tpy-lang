# Error: bare class-name access on a generic class can't render the
# parameterized C++ qname (no type-args at the access site). Force users
# to access through an instance.
from typing import Final
from tpy import int32


class C[T]:
    MAX: Final[int32] = 10


def main() -> None:
    print(C.MAX)  # tpyc: error(/cannot access class constant 'MAX' on generic class 'C' through the bare class name/)


main()
