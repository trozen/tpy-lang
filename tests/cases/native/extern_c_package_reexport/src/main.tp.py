from tpy import extern_c, Int32
from mathlib import abs, get_clock

@extern_c
def app_init() -> None:
    abs(Int32(0))
    x: Int32 = get_clock()
    print(x)
