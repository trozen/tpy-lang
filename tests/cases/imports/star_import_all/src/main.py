# Star import respects __all__ -- only public_add and Pair are imported
from tpy import Int32
from helpers import *

def main() -> Int32:
    p = Pair(Int32(10), Int32(20))
    result = public_add(p.a, p.b)
    print(result)
    return Int32(0)

main()
