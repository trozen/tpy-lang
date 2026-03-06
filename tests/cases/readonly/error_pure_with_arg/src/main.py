# @pure does not accept arguments (unlike @readonly which takes an optional bool).
from tpy import Int32, pure

@pure(True)  # tpyc: error(/@pure does not take arguments/)
def add(a: Int32, b: Int32) -> Int32:
    return a + b
