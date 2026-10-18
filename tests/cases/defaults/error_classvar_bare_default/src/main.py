# A method default naming a ClassVar of its own class body is rejected, even
# where a module Final of the same name exists: the class body binds K first,
# and a ClassVar is a mutable slot, not a constant a default may name (the
# restriction in docs/LANGUAGE_FEATURES.md, "Default parameter values").
from typing import ClassVar, Final
from tpy import int32

K: Final[int32] = 7


class C:
    K: ClassVar[int32] = 13

    def __init__(self) -> None:
        pass

    # The subject: `K` is the ClassVar above, not the module's Final.
    def f(self, x: int32 = K) -> int32:  # tpyc: error(/Default parameter value 'K' must be a module-level Final\[T\] constant/)
        return x


def main() -> None:
    print(C().f())


main()
