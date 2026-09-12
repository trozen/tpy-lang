# Globals defined via tuple unpacking, exported for cross-module import
from tpy import int32

def get_bounds() -> tuple[int32, int32]:
    return (int32(10), int32(20))

lo, hi = get_bounds()
