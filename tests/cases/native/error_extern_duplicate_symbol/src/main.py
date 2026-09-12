from tpy.extern import native
from tpy import int32

@native(binding="C")
def my_func() -> None: ...

@native("my_func", binding="C")
def other_func() -> None: ...  # tpyc: error(/Duplicate extern symbol 'my_func'/)
