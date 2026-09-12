# Test error when using typing.Optional without 'import typing'
from tpy import int32

def foo(x: typing.Optional[int32]) -> int32:  # tpyc: error(/requires.*import typing/)
    return int32(0)
