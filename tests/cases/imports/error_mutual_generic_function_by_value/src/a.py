from b import B
from tpy import Int32

def helper() -> Int32:
    return 42

def first[T](x: T, b: B) -> T:  # tpyc: error(/Cyclic import/)
    return x
