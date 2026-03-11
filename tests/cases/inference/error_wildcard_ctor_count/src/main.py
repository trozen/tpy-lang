# Error: wrong number of _ wildcard type arguments on constructor
from tpy import Int32

class Box[T]:
    val: T
    def __init__(self, val: T) -> None:
        self.val = val

def main() -> None:
    Box[_, _](Int32(1))  # tpyc: error(/expects 1 type argument/)

main()
