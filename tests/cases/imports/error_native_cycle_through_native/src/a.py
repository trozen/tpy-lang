from tpy import int32
from hub import Hub
from b import B

class A:
    # b's record by value: a and b stay C++ peers through the native hub.
    other: B  # tpyc: error(/Cyclic import: field 'A.other' stores 'B' from 'b' by value.*Cycle: a <-> b <-> hub/)

    def __init__(self, o: B) -> None:
        self.other = o
