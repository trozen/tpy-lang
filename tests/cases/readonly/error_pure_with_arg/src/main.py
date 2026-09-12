# @pure does not accept arguments (unlike @readonly which takes an optional bool).
from tpy import int32, pure

@pure(True)  # tpyc: error(/@pure does not take arguments/)
def add(a: int32, b: int32) -> int32:
    return a + b
