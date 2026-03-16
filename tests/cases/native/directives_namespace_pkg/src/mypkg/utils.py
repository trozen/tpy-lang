# Child module should inherit namespace from __init__.py: mypkg::utils
from tpy import Int32

def add(a: Int32, b: Int32) -> Int32:
    return a + b
