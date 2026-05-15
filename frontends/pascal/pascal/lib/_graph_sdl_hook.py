# tpy: include("<tpy/pascal_graph_sdl.hpp>")
# tpy: link("SDL2")
"""Internal: SDL2 display hook for the Pascal Graph unit.

Loaded transparently by the Pascal frontend plugin when `sdl` is on
(`--dsl-opt pascal.sdl=on`, or `auto` mode with libsdl2-dev present).
Users never write `uses _graph_sdl_hook;` -- the plugin injects an
import of this module into any program that uses Graph and inserts
calls to the helpers below at the appropriate Pascal call sites:

    initgraph(...)      ->  ... ; _tpy_pascal_sdl_open_window()
    delay(...)          ->  ... ; _tpy_pascal_sdl_present()
    setvisualpage(...)  ->  ... ; _tpy_pascal_sdl_present()
    closegraph;         ->  _tpy_pascal_sdl_show_canvas() ; closegraph()
                            ... ; _tpy_pascal_sdl_close_window()

The window opens at initgraph, is refreshed by delay/setvisualpage
(TP7's natural frame-pacing primitives) so animation is visible,
shown blocking at closegraph, then torn down. Programs that compile
with `sdl=off` never import this module and stay SDL2-link-free.
"""

from tpy.extern import native
from tpy import Int32

from graph import _ctx as _graph_ctx


@native("tpy::pascal::sdl_open_window")
def _sdl_open_window(width: Int32, height: Int32) -> None: ...


@native("tpy::pascal::sdl_present")
def _sdl_present(width: Int32, height: Int32,
                 pixels: list[Int32]) -> None: ...


@native("tpy::pascal::sdl_show_blocking")
def _sdl_show_blocking(width: Int32, height: Int32,
                       pixels: list[Int32]) -> None: ...


@native("tpy::pascal::sdl_close_window")
def _sdl_close_window() -> None: ...


def _tpy_pascal_sdl_open_window() -> None:
    """Open (or no-op) the SDL window for the current canvas.
    Called right after `initgraph` so the window appears before
    any drawing happens -- otherwise the user just sees a frozen
    terminal for the duration of the program."""
    if _graph_ctx.is_open():
        _sdl_open_window(_graph_ctx.width, _graph_ctx.height)


def _tpy_pascal_sdl_present() -> None:
    """Push the current canvas to the SDL window and pump pending
    events. Called after `delay` / `setvisualpage` (TP7's natural
    frame-completion points). Non-blocking; the program keeps
    running."""
    if _graph_ctx.is_open():
        _sdl_present(_graph_ctx.width, _graph_ctx.height,
                     _graph_ctx.pixels)


def _tpy_pascal_sdl_show_canvas() -> None:
    """Present the final canvas and block until the user closes
    the window or presses any key. Called just before `closegraph`
    so the user gets to see the result before teardown."""
    if _graph_ctx.is_open():
        _sdl_show_blocking(_graph_ctx.width, _graph_ctx.height,
                           _graph_ctx.pixels)


def _tpy_pascal_sdl_close_window() -> None:
    """Tear down the SDL window. Idempotent. Called right after
    `closegraph` so a program that runs `initgraph` again gets a
    clean slate."""
    _sdl_close_window()


__all__ = (
    "_tpy_pascal_sdl_open_window",
    "_tpy_pascal_sdl_present",
    "_tpy_pascal_sdl_show_canvas",
    "_tpy_pascal_sdl_close_window",
)
