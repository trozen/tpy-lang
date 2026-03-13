# Subscript aug-assign errors when result type is wider than element type
from tpy import Int32

def test() -> None:
    items: list[Int32] = [10, 20, 30]
    items[0] *= 1.5  # tpyc: error(/Type mismatch.*expected Int32/)

test()
