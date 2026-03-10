# Deferred generic inference: conflicting type constraints from method calls
from tpy import Int32, Int64

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass
    def set(self, val: T) -> None:
        self.val = val

def main() -> None:
    c = Container()
    c.set(Int32(1))
    c.set(Int64(2))  # tpyc: error(/expected Int32, got Int64/)

main()
