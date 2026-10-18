# A method default naming a field the class body gives a value before the
# def reads that field, as CPython binds it (119 110), never the module's
# Final of the same name; a field is no constant a default may name, so it
# is rejected (BUGS.md#class-field-default-binds-module-name).
from typing import Final
from tpy import int32

K: Final[int32] = 7


class C:
    K: int32 = 11

    def __init__(self) -> None:
        pass

    # The subject: `K` is the field above, not the module's Final.
    def m(self, x: int32 = K, y: int32 = 0) -> int32:  # tpyc: error(/Default parameter value 'K' must be a module-level Final\[T\] constant; the class body binds 'K' to a field/)
        return x * 10 + y


def main() -> None:
    c = C()
    print(c.m(y=9), c.m())


main()
