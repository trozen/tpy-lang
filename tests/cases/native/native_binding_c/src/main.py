# Test @native(binding="C") for C function imports with explicit symbol names
# tpy: include("native_types.hpp")
from tpy.extern import native
from tpy import Int32

@native("native_abs", binding="C")
def my_abs(x: Int32) -> Int32: ...

@native("native_add", binding="C")
def add(a: Int32, b: Int32) -> Int32: ...

def main() -> None:
    print(my_abs(Int32(-42)))
    print(add(Int32(10), Int32(32)))

main()
