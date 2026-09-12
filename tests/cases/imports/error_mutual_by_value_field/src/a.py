from b import B
from tpy import int32

class A:
    val: int32
    other: B  # tpyc: error(/Cyclic import/)
    def __init__(self, v: int32, o: B) -> None:
        self.val = v
        self.other = o
