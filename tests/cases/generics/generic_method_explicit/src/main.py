# Method-level type parameter with explicit type arguments
from tpy import int32

class Box[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def transform[U](self, other: U) -> U:
        return other

def main() -> None:
    b = Box[int32](int32(10))
    r1 = b.transform[int](42)
    print(r1)
    r2 = b.transform[str]("world")
    print(r2)

main()
