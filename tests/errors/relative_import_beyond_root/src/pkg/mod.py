from tpy import Int32
from ...outside import helper  # tpyc: error(/beyond top-level package/)

def test() -> Int32:
    return Int32(42)
