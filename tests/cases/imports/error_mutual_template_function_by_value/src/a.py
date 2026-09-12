from b import B
from tpy import int32, Fn

def helper() -> int32:
    return 42

def consume(maker: Fn[[], B]) -> B:  # tpyc: error(/Cyclic import/)
    return maker()
