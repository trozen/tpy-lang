# Star import respects __all__ -- only public_add and Pair are imported
from tpy import int32
from helpers import *

def main() -> int32:
    p = Pair(int32(10), int32(20))
    result = public_add(p.a, p.b)
    print(result)
    return int32(0)

main()
