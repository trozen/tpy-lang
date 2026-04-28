# Error: Final[T] without an initializer in a regular class body lands in
# the deferred instance-final phase. Use `Final[T] = value` for class constants.
from typing import Final
from tpy import Int32


class C:
    X: Final[Int32]  # tpyc: error(/Final\[T\] without an initializer in a class body is not yet supported/)
