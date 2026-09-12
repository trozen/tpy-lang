from tpy.extern import export
from tpy import int32
from mathlib import abs, get_clock

@export(binding="C")
def app_init() -> None:
    abs(int32(0))
    x: int32 = get_clock()
    print(x)
