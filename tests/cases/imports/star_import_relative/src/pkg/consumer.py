# Relative star import from sibling module
from tpy import Int32
from .defs import *

def compute() -> Int32:
    v = Vec2(Int32(3), Int32(4))
    return double(v.x)
