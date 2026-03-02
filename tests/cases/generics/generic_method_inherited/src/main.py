# Generic method defined in parent class, called on child instance
from tpy import Int32

class Base[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def transform[U](self, other: U) -> U:
        return other

class Child[T](Base[T]):
    def __init__(self, val: T):
        super().__init__(val)

def main() -> None:
    c = Child[Int32](Int32(5))
    print(c.transform(42))
    print(c.transform("inherited"))

main()
