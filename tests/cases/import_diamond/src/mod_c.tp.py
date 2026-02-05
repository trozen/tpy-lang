from tpy import Int32
from mod_d import d_value

def c_value() -> Int32:
    return d_value() + Int32(20)
