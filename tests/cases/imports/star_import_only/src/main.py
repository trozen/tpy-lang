# No explicit tpy import -- int32 comes only via star import from utils.
# Tests that re-exported stdlib types resolve correctly.
from utils import *

def main() -> int32:
    p = Point(int32(1), int32(2))
    result = add(p.x, p.y)
    print(result)
    return int32(0)

main()
