from tpy import Int32
from mod_d import d_value

def b_value() -> Int32:
    return d_value() + Int32(10)
