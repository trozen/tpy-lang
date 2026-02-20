# Test error when using typing.Optional without 'import typing'
from tpy import Int32

def foo(x: typing.Optional[Int32]) -> Int32:  # tpyc: error(/requires.*import typing/)
    return Int32(0)
