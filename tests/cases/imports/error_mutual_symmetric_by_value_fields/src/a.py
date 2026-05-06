from b import B
from tpy import Int32

class A:
    n: Int32
    other: B  # tpyc: error(/Cyclic import/)
    def __init__(self, n: Int32, o: B) -> None:
        self.n = n
        self.other = o
