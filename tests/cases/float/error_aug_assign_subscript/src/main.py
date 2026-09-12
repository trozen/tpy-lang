# Subscript aug-assign errors when result type is wider than element type
from tpy import int32

def test() -> None:
    items: list[int32] = [10, 20, 30]
    items[0] *= 1.5  # tpyc: error(/Type mismatch.*expected int32/)

test()
