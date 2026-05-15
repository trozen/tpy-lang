# Pascal SDL Demo

Manual demo for the optional SDL2 display path of the Graph unit. Not a
pytest case -- the regular test suite must stay free of system SDL2
dependency.

## Running

Install SDL2 dev headers/libs (one-time):

    sudo apt install libsdl2-dev          # Debian / Ubuntu
    sudo dnf install SDL2-devel           # Fedora
    brew install sdl2                     # macOS (Homebrew)

Then from the repo root:

    uv run tpy --dsl-plugin examples/frontends/pascal/pascal_frontend.py \
               examples/frontends/pascal/sdl_demo/main.pas

The plugin defaults to `--dsl-opt pascal.sdl=auto`, which probes for the
SDL2 dev headers and silently enables the SDL window when they're
present. Force one mode with `--dsl-opt pascal.sdl=on|off`.

A window opens with a small scene drawn via the Tier A/B/C primitives.
Press any key or close the window to exit. The same drawing also writes
`out.ppm` in the cwd, so `pascal.sdl=off` (or absence of libsdl2-dev in
auto mode) still gives you a viewable snapshot.

## What's exercised

- `uses Graph;` (legacy-compatible -- the SDL display is wired by the
  frontend plugin, not by an extra unit)
- `initgraph(0, 0, '')` -- default 320x200 canvas
- Tier A: `setcolor`, `bar`, `rectangle`, `line`, `getmaxx`/`getmaxy`
- Tier B: `outtextxy` (8x8 bitmap font), `fillellipse`
- `closegraph` -- under `pascal.sdl=on`, opens the SDL window and
  blocks for keypress; always flushes the PPM snapshot to `out.ppm`
