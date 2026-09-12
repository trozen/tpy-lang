from b import B
from tpy import int32

class A:
    n: int32
    other: B  # tpyc: error(/Cyclic import/)
    def __init__(self, n: int32, o: B) -> None:
        self.n = n
        self.other = o
