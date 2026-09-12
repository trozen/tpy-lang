# Relative star import from sibling module
from tpy import int32
from .defs import *

def compute() -> int32:
    v = Vec2(int32(3), int32(4))
    return double(v.x)
