"""Test that inference upgrades IntLiteralType to concrete int type."""
from tpy import int32


class Same[T]:
    a: T
    b: T

    def __init__(self, a: T, b: T) -> None:
        self.a = a
        self.b = b


# Test order: literal first, int32 second -> should infer Same[int32]
x: int32 = 10
same1 = Same(1, x)
print(same1.a)
print(same1.b)

# Test order: int32 first, literal second -> should also infer Same[int32]
same2 = Same(x, 2)
print(same2.a)
print(same2.b)
