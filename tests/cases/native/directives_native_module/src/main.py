# Test that # tpy: native_module suppresses .cpp generation
# tpy: native_module
# tpy: include("<SDL2/SDL.h>")
# tpy: link("SDL2")

from tpy.extern import native_c
from tpy import Int32

@native_c
def SDL_Init(flags: Int32) -> Int32: ...
