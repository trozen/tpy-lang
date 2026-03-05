# Using a tpy protocol as type annotation without importing it should error.
from tpy import Int32

def hash_it(x: Hashable) -> Int32:  # tpyc: error(/requires: from tpy import Hashable/)
    return hash(x)
