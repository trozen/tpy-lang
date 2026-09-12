from b import B
from tpy import int32

def helper() -> int32:
    return 42

def first[T](x: T, b: B) -> T:  # tpyc: error(/Cyclic import/)
    return x
