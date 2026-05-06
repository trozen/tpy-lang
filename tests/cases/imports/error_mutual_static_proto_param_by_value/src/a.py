from b import B
from tpy import Int32
from typing import Sized

def helper() -> Int32:
    return 42

# Static (non-@dynamic) protocol param `s: Sized` makes this a
# template emitted in the header. The by-value `b: B` peer reference
# alongside it must trigger the gate.
def measure(s: Sized, b: B) -> Int32:  # tpyc: error(/Cyclic import/)
    return Int32(len(s)) + b.payload
