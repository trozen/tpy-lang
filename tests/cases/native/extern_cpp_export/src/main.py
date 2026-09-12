from tpy.extern import native
from tpy import int32

@native("my_ns::exported_func")
def my_func(x: int32) -> int32: ...

@native
def global_export(x: int32) -> int32: ...
