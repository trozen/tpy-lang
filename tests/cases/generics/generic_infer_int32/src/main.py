"""Test that inference upgrades IntLiteralType to concrete int type."""
from tpy import Int32


class Same[T]:
    a: T
    b: T

    def __init__(self, a: T, b: T) -> None:
        self.a = a
        self.b = b


# Test order: literal first, Int32 second -> should infer Same[Int32]
x: Int32 = 10
same1 = Same(1, x)
print(same1.a)
print(same1.b)

# Test order: Int32 first, literal second -> should also infer Same[Int32]
same2 = Same(x, 2)
print(same2.a)
print(same2.b)
