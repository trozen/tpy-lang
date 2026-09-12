# Test @native(binding="C") on a class -- C struct import with aggregate init
# Verifies aggregate initialization and native name in generated code
# tpy: include("native_types.hpp")
from tpy.extern import native
from tpy import int32, Ptr

@native("SDL_Rect", binding="C")
class Rect:
    x: int32
    y: int32
    w: int32
    h: int32

def use_rect(r: Ptr[Rect]) -> int32:
    return r.w * r.h

def main() -> None:
    r: Rect = Rect(int32(0), int32(0), int32(800), int32(600))
    print(r.w)
    print(use_rect(r))

main()
