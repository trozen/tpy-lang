from tpy.extern import extern_c
from tpy import Int32
from lib import abs, get_clock

@extern_c
def app_init() -> None:
    abs(Int32(0))
    x: Int32 = get_clock()
    print(x)
