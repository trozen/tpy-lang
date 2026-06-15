# A value-bounded generic param is passed `const W&`, so its address is
# `const W*` -- a mutable Ptr[W] slot is unsound and must be rejected (only
# the readonly form is allowed). Guards the value-bounded arm of the
# generic-param address-of coercion.
from tpy import Ptr, ValueType, Int32


class Holder[W: ValueType]:
    _p: Ptr[W]

    def __init__(self, x: W) -> None:
        self._p = x  # tpyc: error(/expected Ptr\[W\].*got W|Type mismatch/)


def main() -> None:
    print("unreachable")


main()
