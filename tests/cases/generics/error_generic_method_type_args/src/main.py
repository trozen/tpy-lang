# Error: wrong number of explicit type arguments for a generic method
from tpy import int32

class Box[T]:
    val: T

    def __init__(self, val: T):
        self.val = val

    def transform[U](self, other: U) -> U:
        return other

def main() -> None:
    b = Box[int32](int32(10))
    b.transform[int32, str](int32(5))  # tpyc: error(/expects 1 type argument/)

main()
