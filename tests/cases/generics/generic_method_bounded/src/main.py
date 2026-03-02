# Method-level type parameter with a bound (protocol constraint)
from tpy import Int32, Comparable

class Wrapper[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def is_less[U: Comparable](self, a: U, b: U) -> bool:
        return a < b

def main() -> None:
    w = Wrapper[Int32](Int32(5))
    print(w.is_less(1, 2))
    print(w.is_less(10, 3))

main()
