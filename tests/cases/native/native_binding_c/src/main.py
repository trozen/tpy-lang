# Test @native(binding="C") for C function imports with explicit symbol names
# tpy: include("native_types.hpp")
from tpy.extern import native
from tpy import int32

@native("native_abs", binding="C")
def my_abs(x: int32) -> int32: ...

@native("native_add", binding="C")
def add(a: int32, b: int32) -> int32: ...

def main() -> None:
    print(my_abs(int32(-42)))
    print(add(int32(10), int32(32)))

main()
