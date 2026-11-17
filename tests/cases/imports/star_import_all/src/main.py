# Star import respects __all__ -- only public_add and Pair are imported; the
# part_* modules pin which `__all__` spellings compile.
from tpy import int32
from helpers import *
import part_overwritten
import part_all_none
import part_all_dynamic

def main() -> int32:
    p = Pair(int32(10), int32(20))
    result = public_add(p.a, p.b)
    print(result)
    part_overwritten.main()
    part_all_none.main()
    part_all_dynamic.main()
    return int32(0)

main()
