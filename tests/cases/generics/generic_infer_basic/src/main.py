"""Test basic type inference for user-defined generic classes."""
from tpy import int32


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


# Inference from int literal -> Box[int]
box = Box(42)
print(box.value)

# Inference from int32 -> Box[int32]
x: int32 = 10
box32 = Box(x)
print(box32.value)
