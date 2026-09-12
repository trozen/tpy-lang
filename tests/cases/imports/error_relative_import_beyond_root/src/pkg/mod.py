from tpy import int32
from ...outside import helper  # tpyc: error(/beyond top-level package/)

def test() -> int32:
    return int32(42)
