# No explicit tpy import -- Int32 comes only via star import from utils.
# Tests that re-exported stdlib types resolve correctly.
from utils import *

def main() -> Int32:
    p = Point(Int32(1), Int32(2))
    result = add(p.x, p.y)
    print(result)
    return Int32(0)

main()
