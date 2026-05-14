# Pascal SDL Demo

Manual demo for the optional SDL2 display layer (M22). Not a pytest case
— the regular test suite must stay free of system SDL2 dependency.

## Running

Install SDL2 dev headers/libs (one-time):

    sudo apt install libsdl2-dev          # Debian / Ubuntu
    sudo dnf install SDL2-devel           # Fedora
    brew install sdl2                     # macOS (Homebrew)

Then from the repo root:

    uv run tpy examples/frontends/pascal/sdl_demo/main.pas

A window opens with a small scene drawn via the Tier A/B/C primitives.
Press any key or close the window to exit. The same drawing also writes
`out.ppm` in the build dir, so users without SDL2 can still run a
Pascal Graph program — they just won't get the interactive window.

## What's exercised

- `uses Graph, GraphSDL;`
- `initgraph(0, 0, '')` — default 320×200 canvas
- Tier A: `setcolor`, `bar`, `rectangle`, `line`, `getmaxx`/`getmaxy`
- Tier B: `outtextxy` (8×8 bitmap font), `fillellipse`
- `show` (from GraphSDL) — opens SDL window, blocks for keypress
- `closegraph` — flushes the PPM snapshot
