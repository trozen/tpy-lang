from tpy.extern import native
from tpy import Int32

@native(binding="C")
def abs(x: Int32) -> Int32: ...

@native("tpy_clock", binding="C")
def get_clock() -> Int32: ...
