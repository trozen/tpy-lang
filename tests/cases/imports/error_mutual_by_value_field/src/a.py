from b import B
from tpy import Int32

class A:
    val: Int32
    other: B  # tpyc: error(/Cyclic import/)
    def __init__(self, v: Int32, o: B) -> None:
        self.val = v
        self.other = o
