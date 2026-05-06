from b import B
from tpy import Int32

def helper() -> Int32:
    return 42

class A:
    val: Int32
    # Constructor takes a cycle-peer value-type by value but stores
    # only a primitive. There is no field of type B, so the field
    # walk wouldn't fire -- the constructor body itself emits inline
    # in the struct and needs B's complete layout.
    def __init__(self, b: B) -> None:  # tpyc: error(/Cyclic import/)
        self.val = b.payload
