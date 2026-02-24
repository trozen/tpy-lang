from tpy.extern import native_c, extern_c
from tpy import Int32

@native_c
def puts(s: str) -> Int32: ...

@extern_c
def greet(name: str) -> None:
    puts(name)
