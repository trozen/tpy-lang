# `raise Outer.EB[int32]("x")` builds the instantiation its type arguments
# spell, as the expression does: the argument must be an int32.
from tpy import Own, int32


class Outer:
    class EB[T](Exception):
        def __init__(self, v: Own[T]) -> None:
            super().__init__("eb")
            self.v = v


def main() -> None:
    raise Outer.EB[int32]("x")  # tpyc: error(/Type mismatch in argument .v.: expected int32, got str/)


main()
