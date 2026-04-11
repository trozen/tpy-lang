from tpy.extern import export
from tpy import Int32
from mathlib import abs, get_clock

@export(binding="C")
def app_init() -> None:
    abs(Int32(0))
    x: Int32 = get_clock()
    print(x)
