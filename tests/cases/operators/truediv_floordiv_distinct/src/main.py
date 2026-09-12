# Regression: a type defining BOTH __truediv__ and __floordiv__ with the same
# operand type must compile. C++ has one `/`, so codegen used to emit two
# `operator/` (ambiguating declaration). Now `//` lowers to a __floordiv__
# method call, decoupled from `/`. This is the shape timedelta needs.
from tpy import int32, ValueType


class Meters(ValueType):
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __truediv__(self, other: "Meters") -> float:
        return self.v / other.v

    def __floordiv__(self, other: "Meters") -> "Meters":
        return Meters(self.v // other.v)

    def __rfloordiv__(self, other: int32) -> "Meters":
        return Meters(other // self.v)


def main() -> None:
    a = Meters(12)
    b = Meters(4)
    print(a / b)          # 3.0  (true division -> float ratio)
    print((a // b).v)     # 3    (floor division -> Meters, distinct from /)
    print((13 // b).v)    # 3    (reflected floor division -> __rfloordiv__)


main()
