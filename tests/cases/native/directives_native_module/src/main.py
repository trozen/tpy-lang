# Test that `__tpy__` "mod": "native" suppresses .cpp generation
__tpy__ = {"mod": "native",
           "includes": ["<SDL2/SDL.h>"],
           "links": ["SDL2"]}

from tpy.extern import native
from tpy import int32

@native(binding="C")
def SDL_Init(flags: int32) -> int32: ...
