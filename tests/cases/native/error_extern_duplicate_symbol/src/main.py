from tpy.extern import native_c
from tpy import Int32

@native_c
def my_func() -> None: ...

@native_c("my_func")
def other_func() -> None: ...  # tpyc: error(/Duplicate extern symbol 'my_func'/)
