from b import B
from tpy import int32

def helper() -> int32:
    return 42

class A:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v
    # Generic method on a cycle-member record. Generic methods are
    # emitted inline in the .hpp; the by-value `b: B` peer reference
    # must be rejected.
    def merge[T](self, b: B, x: T) -> T:  # tpyc: error(/Cyclic import/)
        return x
