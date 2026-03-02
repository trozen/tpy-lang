# Method-level type parameter inferred from arguments
from tpy import Int32

class Box[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def transform[U](self, other: U) -> U:
        return other

def main() -> None:
    b = Box[Int32](Int32(10))
    r1 = b.transform(42)
    print(r1)
    r2 = b.transform("hello")
    print(r2)
    r3 = b.transform(True)
    print(r3)

main()
