# Test that # tpy: native_module suppresses .cpp generation
# tpy: native_module
# tpy: include("<SDL2/SDL.h>")
# tpy: link("SDL2")

from tpy.extern import native
from tpy import int32

@native(binding="C")
def SDL_Init(flags: int32) -> int32: ...
