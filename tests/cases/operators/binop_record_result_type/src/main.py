# Binop result typing must follow the sema-resolved dunder when an operand is
# a record -- not the numeric rules (div->float, float/BigInt precedence).
from __future__ import annotations
from typing import overload
from tpy import ValueType


class Frac(ValueType):
    num: int
    den: int

    def __init__(self, num: int, den: int) -> None:
        self.num = num
        self.den = den

    @overload
    def __truediv__(self, other: Frac) -> float: ...
    @overload
    def __truediv__(self, other: int) -> Frac: ...
    def __truediv__(self, other: Frac | int) -> float | Frac:
        if isinstance(other, Frac):
            return (self.num * other.den) / (self.den * other.num)
        return Frac(self.num, self.den * other)

    def __str__(self) -> str:
        return str(self.num) + "/" + str(self.den)


class Vec(ValueType):
    x: float
    y: float

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def __mul__(self, k: float) -> Vec:
        return Vec(self.x * k, self.y * k)

    def __rmul__(self, k: float) -> Vec:
        return Vec(self.x * k, self.y * k)

    def __str__(self) -> str:
        return "Vec(" + str(self.x) + ", " + str(self.y) + ")"


class Acc(ValueType):
    total: int

    def __init__(self, total: int) -> None:
        self.total = total

    def __add__(self, n: int) -> Acc:
        return Acc(self.total + n)

    def __str__(self) -> str:
        return "Acc(" + str(self.total) + ")"


def main() -> None:
    f = Frac(1, 2)
    f2 = Frac(1, 3)

    # Bare-context div with a record-returning overload arm.
    print(f / 2)            # 1/4
    # Declaration is another consumer of the derived type.
    q = f / 2               # tpyc: type(Frac)
    print(q.den)            # 4
    # Context form (member access on the binop result).
    print((f / 2).num)      # 1
    # The float-returning arm really yields float.
    r = f / f2              # tpyc: type(float)
    print(r)                # 1.5

    # Float-operand precedence must not override __mul__/__rmul__ -> Vec.
    v = Vec(1.0, 2.0)
    print(v * 2.0)          # Vec(2.0, 4.0)
    print(2.0 * v)          # Vec(2.0, 4.0)

    # BigInt-operand precedence must not override __add__ -> Acc.
    a = Acc(10)
    n: int = 5
    print(a + n)            # Acc(15)

    # Inverse: all-numeric division still types as float.
    print(7 / 2)            # 3.5
    ai: int = 7
    bi: int = 2
    print(ai / bi)          # 3.5


main()
