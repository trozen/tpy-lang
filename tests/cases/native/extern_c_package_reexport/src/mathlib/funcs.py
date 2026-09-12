from tpy.extern import native
from tpy import int32

@native(binding="C")
def abs(x: int32) -> int32: ...

@native("tpy_clock", binding="C")
def get_clock() -> int32: ...
