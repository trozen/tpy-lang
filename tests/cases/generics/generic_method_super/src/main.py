# Generic method called via super() inside a child class
from tpy import int32

class Base[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def transform[U](self, other: U) -> U:
        return other

class Child[T](Base[T]):
    def __init__(self, val: T):
        super().__init__(val)

    def wrap[U](self, other: U) -> U:
        return super().transform(other)

def main() -> None:
    c = Child[int32](int32(5))
    print(c.wrap(42))
    print(c.wrap("hello"))

main()
