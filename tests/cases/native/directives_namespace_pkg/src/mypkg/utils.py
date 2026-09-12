# Child module should inherit namespace from __init__.py: mypkg::utils
from tpy import int32

def add(a: int32, b: int32) -> int32:
    return a + b
