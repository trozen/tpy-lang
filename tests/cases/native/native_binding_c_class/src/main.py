# Test @native(binding="C") on a class -- C struct import with aggregate init
# Verifies aggregate initialization and native name in generated code
# tpy: include("native_types.hpp")
from tpy.extern import native
from tpy import Int32, Ptr

@native("SDL_Rect", binding="C")
class Rect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32

def use_rect(r: Ptr[Rect]) -> Int32:
    return r.w * r.h

def main() -> None:
    r: Rect = Rect(Int32(0), Int32(0), Int32(800), Int32(600))
    print(r.w)
    print(use_rect(r))

main()
