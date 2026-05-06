from b import B
from tpy import Int32, Fn

def helper() -> Int32:
    return 42

def consume(maker: Fn[[], B]) -> B:  # tpyc: error(/Cyclic import/)
    return maker()
