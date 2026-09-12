# Deferred generic inference: conflicting type constraints from method calls
from tpy import int32, int64

class Container[T]:
    val: T
    def __init__(self) -> None:
        pass
    def set(self, val: T) -> None:
        self.val = val

def main() -> None:
    c = Container()
    c.set(int32(1))
    c.set(int64(2))  # tpyc: error(/expected int32, got int64/)

main()
