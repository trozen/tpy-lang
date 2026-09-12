from tpy import int32
from mod_d import d_value

def c_value() -> int32:
    return d_value() + int32(20)
