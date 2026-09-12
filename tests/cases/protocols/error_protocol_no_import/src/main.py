# Using a tpy protocol as type annotation without importing it should error.
from tpy import int32

def hash_it(x: Hashable) -> int32:  # tpyc: error(/requires: from tpy import Hashable/)
    return hash(x)
