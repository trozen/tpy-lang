"""Test comprehensive generic inheritance edge cases.

Covers:
- Partial type substitution: Child[T](Parent[T, Int32])
- Multi-level inheritance: Base -> Middle -> Leaf
- Inherited field access with type params
- Inherited method with type param in parameters
"""
from tpy import Int32


class Base[T, U]:
    first: T
    second: U

    def __init__(self, first: T, second: U) -> None:
        self.first = first
        self.second = second

    def set_first(self, v: T) -> None:
        self.first = v

    def get_first(self) -> T:
        return self.first

    def get_second(self) -> U:
        return self.second


class Middle[T](Base[T, Int32]):
    def __init__(self, first: T, second: Int32) -> None:
        self.first = first
        self.second = second


class Leaf[T](Middle[T]):
    extra: str

    def __init__(self, first: T, second: Int32, extra: str) -> None:
        self.first = first
        self.second = second
        self.extra = extra

    def get_extra(self) -> str:
        return self.extra


# Test with Leaf[str]
leaf: Leaf[str] = Leaf[str]("hello", Int32(42), "bonus")

# Inherited method with type param in parameter (from Base)
leaf.set_first("world")

# Inherited method with forwarded type param return (from Base, through Middle)
print(leaf.get_first())

# Inherited method with concrete type param return (U=Int32 from Middle)
print(leaf.get_second())

# Own method
print(leaf.get_extra())

# Direct field access on inherited field
print(leaf.first)
print(leaf.second)
