# tpy: native_module
# tpy: include("<tpy/pascal_graph_sdl.hpp>")
# tpy: link("SDL2")
"""Optional SDL2 display layer for the Pascal Graph unit.

Pascal source opts in via `uses GraphSDL;`. The unit ships one
user-visible procedure: `Show` opens an SDL2 window with the
current Graph canvas, blocks until the user closes it or presses
any key, then tears down. Drawing has already happened against
the Graph context's pixel buffer (M19/M20/M21 primitives, pure
TPy); this layer is purely display.

This module is intentionally opt-in: importing it triggers a
system `-lSDL2` link. Programs that don't `uses GraphSDL` (which
includes the regular Pascal test suite) compile without any SDL2
dependency. Users who do need it must have `libsdl2-dev`
installed (Debian/Ubuntu: `apt install libsdl2-dev`).

The `Show` semantics match the canonical TP7 idiom of "draw,
then `ReadKey; CloseGraph;`" -- call `Show;` after drawing and
before `CloseGraph` to display the canvas blocking-style.
"""

from tpy.extern import native
from tpy import Int32

from .graph import _ctx as _graph_ctx


@native("tpy::pascal::sdl_show_pixels")
def _sdl_show_pixels(width: Int32, height: Int32,
                     pixels: list[Int32]) -> None: ...


def show() -> None:
    """Display the current Graph canvas in an SDL2 window. Blocks
    until the user dismisses it. No-op when no canvas is open."""
    if _graph_ctx.is_open():
        _sdl_show_pixels(_graph_ctx.width, _graph_ctx.height,
                          _graph_ctx.pixels)
