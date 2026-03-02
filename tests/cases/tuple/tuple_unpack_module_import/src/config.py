# Globals defined via tuple unpacking, exported for cross-module import
from tpy import Int32

def get_bounds() -> tuple[Int32, Int32]:
    return (Int32(10), Int32(20))

lo, hi = get_bounds()
