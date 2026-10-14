# A cross-platform GUI library -- direction

**Status: a direction, not a design, for the further future** -- and
extensions (below) further still. Nothing here is scheduled. It records
where the exploration stands so that work done in the meantime does not close
the path off, and lists what already exists that the library could build on.
Each layer gets its own design pass when it is picked up.

## Goal

Applications that feel like Electron ones -- the same look on every platform,
HTML/CSS-style layout -- without shipping a browser. TPy compiles to native
code, so it can provide the rendering and layout stack directly.

**The reference application is an editor of VS Code's class**, written in TPy
on this stack instead of Electron and TypeScript. It sets the bar for every
layer (see "What the reference application demands" below); a smaller app is
the first demo, not the target.

The library is layered; each layer is usable on its own:

| Layer | What it provides | Written in |
|---|---|---|
| 1. Platform | windows, input events, clipboard, DPI, IME, the GPU device and surface, system fonts, the accessibility bridge | `@native` bindings -- permanently |
| 2. Drawing and text | a 2D canvas (paths, fills, gradients, images), font parsing, text shaping, glyph rasterization; records a display list and renders it on the CPU, or on the GPU where one is available | TPy; C libraries as stopgaps |
| 3. Layout core | box and flex layout (CSS-like) as a function over a node interface; hit testing | TPy |
| 4. Modes | a retained element tree (DOM-like: style cascade, invalidation, incremental relayout) and an immediate-mode API (rebuilt each frame, stable IDs), both on layers 2 and 3 | TPy |
| 5. Framework | widgets, state and re-rendering on the retained tree | TPy |

Principles that decide the split:

- **It looks good and it is fast and responsive -- the bar, not a polish
  pass.** Looking good: anti-aliased shapes, careful text (subpixel
  positioning, correct gamma, good default fonts), crisp at every DPI, smooth
  animation, and a default theme that looks finished without an application
  styling it. Fast and responsive: the UI thread never blocks (I/O, child
  processes and heavy work run beside it), input reaches the screen within a
  frame, frames are paced evenly, and startup and memory stay small. Each is
  measured -- input-to-screen latency, frame time, startup time, memory -- from
  the first demo on, and a regression is a bug.
- **Native bindings only at the system boundary:** the OS and GPU APIs,
  windowing and input, accessibility, system font discovery. Everything else
  moves into TPy as far as it can. C libraries above that boundary (a
  rasterizer, a shaper, a layout engine) are stopgaps to get a working stack
  early, replaced layer by layer; text shaping (HarfBuzz) may stay longest,
  since OpenType shaping across every script is a large body of work. This is
  the rule that keeps Python-level composition in TPy stdlib code rather than in
  C++ runtime helpers, taken to its end -- and the library becomes a showcase of
  TPy's performance ceiling rather than a set of bindings.
- **The library never forces a GPU; an application may.** The CPU renderer is
  the baseline and is always present, so a simple application runs on any
  machine: software-only machines, VMs, remote desktops, headless rendering in
  tests. A GPU renderer is an accelerator over the same display list. An
  application whose core needs one (a 3D viewport, large-image editing) may
  require it; the library does not make that choice for every application. The
  CPU path is not only a fallback: a GPU path adds driver, compositor and
  synchronization latency, and GPU-rendered text applications on Linux can feel
  less responsive than CPU-rendered ones -- for a text-heavy application the CPU
  renderer may be the better default. So the CPU renderer is the one TPy grows
  first and the reference the GPU one is checked against, and input-to-screen
  latency is measured on both. Keeping the CPU path fast enough for an editor at
  a high refresh rate means repainting only what changed (damage tracking) and
  caching rasterized glyphs.
- **Both modes on shared primitives.** Retained and immediate mode differ in who
  owns the tree, not in what a frame needs. Both produce the same frame output
  -- a display list, hit-test regions and accessibility nodes keyed by stable
  IDs -- and the same back end consumes it. Layer 2 (the canvas) is immediate by
  nature; layer 3 lays out any tree that offers its node interface, so the
  retained DOM (persistent, laid out incrementally) and an immediate-mode frame
  (built fresh each frame, e.g. in an arena) drive the same algorithm. The
  accessibility requirement is what forces stable IDs on the immediate mode too.
- **The retained tree is the ownership test.** Parent links, handlers that hold
  state and polymorphic elements are the hardest test of TPy's ownership model
  on a real program.
- **Source stays valid Python**, as everywhere: application code type-checks
  and runs under ordinary tooling.

### What the reference application demands

An editor of VS Code's class fixes requirements that a demo would not:

- **Text above all.** Large documents with only the visible lines shaped and
  laid out; a glyph cache; font fallback across scripts, bidirectional text,
  ligatures (programming fonts), color emoji; caret, multi-cursor selection and
  IME composition inline.
- **Virtualized views.** A file tree or a list of 100k entries lays out and
  paints only what is on screen.
- **Smooth scrolling and typing latency** on the CPU renderer -- the damage
  tracking and glyph cache above.
- **Async I/O beside the UI**: language servers and other tools as
  subprocesses speaking JSON-RPC, file watching, a terminal panel -- on one
  event loop with the UI.
- **Theming** through the CSS-like style layer.
- **Accessibility** as a hard requirement (a screen-reader mode, keyboard-only
  use), not an add-on.
- **Extensions.** VS Code's value is its extension ecosystem. The preferred
  direction is sandboxed Wasm: an extension is TPy compiled by the Wasm backend
  of `docs/SELF_HOSTING.md` section 10 and reaches only the host functions the
  application exposes -- the same mechanism as sandboxed macros, so the
  application's extension API is a TPy interface module and both sides of the
  binding can be generated from it. The runtime is an existing one: wasmtime
  first (JIT, C API, exception handling; already probed for macros), WAMR (C,
  interpreter / ahead-of-time / JIT, small) as the alternative to measure.
  Compiled modules loaded as shared libraries are the unsandboxed fallback.
- **Startup time and memory** -- the measurable advantage over Electron, so
  they are tracked from the first demo.

### Other reference applications

Simple versions of an image editor, a 3D modeller and a spreadsheet each add
what the editor does not:

| Application | Adds |
|---|---|
| Image editor | large tiled images, layers and blend modes, filters (`Simd[T, N]` and threads become essential), a brush engine with pen pressure, undo that copies only the changed tiles, image codecs. May require a GPU. |
| 3D modeller | a 3D viewport (GPU realistically required; a software renderer can serve a simple one), mesh data structures (an arena with indices -- another ownership test), vector and matrix math, node editors, scripting (in TPy, sandboxed as extensions are) |
| Spreadsheet | a virtualized grid of millions of cells, a formula language (parser, evaluator, recalculation along a dependency graph of only what changed), number formatting, charts on the canvas |

What they share, beyond the editor's list:

- **Application-drawn surfaces inside the element tree** -- the viewport, the
  image canvas, a chart: an element whose content the application renders
  itself into a CPU buffer or a GPU texture. Layer 4 provides for it from the
  start.
- **Desktop application structure** -- docking panels, several windows, an
  undo/redo framework, pen and tablet input.
- **Domain libraries apart from the GUI library** -- imaging, 3D math and
  meshes, a formula engine: each a TPy library usable without a UI, and a
  performance showcase of its own.

A browser target is a separate line of work (Wasm, see `docs/SELF_HOSTING.md`
section 10) and does not constrain these choices.

## Candidate libraries

From the initial exploration (2026). Licenses are as published upstream at the
time; recheck when vendoring.

**Platform**

| Library | License | Notes |
|---|---|---|
| SDL3 | Zlib | windowing, input, gamepad, clipboard; broad platform coverage; its GPU API covers Vulkan, Metal and D3D12 behind one interface, so one binding could carry a TPy GPU renderer |
| GLFW | Zlib | leaner; windows and GL/Vulkan/Metal surfaces only |
| AccessKit (C bindings) | MIT / Apache-2.0 | accessibility tree for the platform screen readers; Rust underneath, C API on top |
| native per platform | -- | most work, best integration |

**Drawing** (stopgaps until the TPy renderer; also the references to measure it
against)

| Library | License | Notes |
|---|---|---|
| Skia | BSD | GPU (Metal/Vulkan/D3D/GL), full text stack; very large build |
| Blend2D | Zlib | CPU only, SIMD + JIT; fast, small, easy to vendor; no text shaping |
| ThorVG | MIT | software, OpenGL/ES, WebGL and WebGPU engines behind one API; between Blend2D and Skia in size |
| NanoVG | Zlib | tiny, OpenGL only, no complex text |
| Cairo | LGPL / MPL | mature; the license is awkward to vendor |

**Text**: HarfBuzz (shaping: scripts, ligatures, RTL) and FreeType (font
parsing, glyph rasterization). Skia bundles both; every other stack needs them
separately. FreeType's part is the first to move into TPy -- a glyph outline is
a path, rasterized by the same code as any other path; HarfBuzz is the likely
last.

**Layout** (references only -- layer 3 is TPy): Yoga (MIT, flexbox in C, React
Native's engine; retained), Clay (zlib, a single-header immediate-mode layout
library -- shows flex layout rebuilt every frame is cheap enough).

**Stacks considered** (as a starting point; the drawing and text pieces are
what later moves into TPy)

- A. Skia + Yoga + SDL3 -- GPU, full text, proven; heavy to vendor and build.
- B. Blend2D + SDL3 + HarfBuzz + FreeType (+ Yoga as a prototype) -- every piece
  fits the vendoring model below; CPU rendering hits a ceiling. The exploration
  leaned this way.
- C. NanoVG + Clay + GLFW -- the quickest demo, not a shippable target.
- ThorVG in place of Blend2D in B would keep B's footprint and add a GPU path.

With the TPy-first direction the starting stack matters less than the
boundary: SDL3 (window, input, GPU device) and AccessKit are the permanent
bindings; the drawing library is chosen for how quickly it gets a demo up and
how good a reference it is for the TPy renderer.

## What already exists to build on

In this repository:

| Piece | Where | Use |
|---|---|---|
| `@native` with `binding="C"` | `docs/NATIVE_INTEROP.md` | binding layer 1 and the stopgap libraries directly, without their headers |
| Vendoring model | `runtime/cpp/third_party/<lib>/`, `<lib>.vendor.json`, `scripts/vendor_<lib>.py`, facade header `runtime/cpp/include/tpy/stdlib/<lib>_h.hpp`, `tpyc --<lib>=bundled\|system\|auto\|none`, `"links": [{"lib": "<lib>", "managed": True}]` | shipping SDL3 / Blend2D / HarfBuzz / FreeType like PCRE2, zlib and mbedtls today |
| Raw binding layer | `lib/tpy/_bindings/` | where the raw C bindings go; the user-facing modules wrap them |
| `Rc[T]` / `Weak[T]` | `lib/tpy/tplib/rc.py` | element tree: children owned, parent links weak |
| `@dynamic` protocols | `docs/DYNAMIC_PROTOCOL_DESIGN.md` | polymorphic elements and widgets in one tree |
| Closures, `Callable` | `docs/CLOSURES_CALLABLE_DESIGN.md` | event handlers |
| `@dataclass` and class macros | `docs/MACRO_DESIGN.md` | declarative widget and style definitions |
| asyncio (TPy, over a small C++ bridge) | `lib/tpy/asyncio/`, `_bindings/posix_epoll.py` | one event loop for UI events, timers and I/O; the executor would take the platform's event source |
| Threads, `Arc`, `Mutex`, channels | `tpy.thread`, `tpy.sync`, `tplib/arc.py`, `tplib/channel.py` | a render or I/O thread beside the UI thread |
| `@noalloc` | `docs/LANGUAGE_FEATURES.md` | proving the per-frame paths (layout pass, paint) allocation-free, once its enforcement lands (planned) |
| `json` | `lib/tpy/json.py`, `tplib/json` | themes, settings, JSON-RPC to language servers |
| `subprocess`, `os`, `termios`/`tty` | `lib/tpy/subprocess.py`, `lib/tpy/os/` | language servers and tools as child processes; a terminal panel |

In the example repositories:

- `tpy-examples/shedskin/doom/pygame.py` -- an SDL2 binding through `@native`
  declarations without SDL's headers: window, renderer, texture upload, keyboard
  state. The pattern for layer 1.
- `tpy-another-world` -- a polygon rasterizer and a terminal renderer in TPy,
  a first data point for a TPy CPU renderer.

## Known gaps

- **C callbacks** (`docs/NATIVE_INTEROP.md` "Callback function pointers",
  status Open). Needed for Yoga's measure function, SDL event watchers and
  HarfBuzz/FreeType hooks.
- **String and buffer marshalling at a C boundary** (same document): `str` and
  `bytes` are rejected in `binding="C"` signatures, so a window title or a
  shaping buffer needs a hand-written conversion today.
- **`Simd[T, N]`** (TODO.md, the vector type the scalar naming rule reserves):
  a CPU rasterizer, compositing and image filters in TPy need it to come near
  Blend2D or Skia.
- **Shaders**: a GPU renderer needs shader programs, which are not TPy today
  (hand-written shader source, or a later compile-to-shader direction).
- **MSVC** cannot build the generated C++ (statement expressions); Windows goes
  through Clang or MinGW.

## Open questions

1. **The stopgap renderer** -- the CPU renderer is the baseline (see
   "The library never forces a GPU"), so the question is only which library fills it until
   the TPy one exists: Blend2D (CPU only, fastest there) or ThorVG (CPU now, a
   GPU engine later behind the same API). Skia is the reference for quality,
   not a fit for the vendoring model.
2. **Platform priority** -- Linux first? macOS needs Metal for a GPU path.
3. **Scope** -- a stdlib module bundled with tpyc, or a separate TPy package.
4. **First demo** on the way to the editor -- a button that responds to clicks,
   a virtualized scrolling list, a single-file text view.
5. **The extension runtime** -- wasmtime or WAMR, measured on a real extension
   (startup, call overhead across the host boundary, binary size).
6. **Accessibility, IME, DPI scaling** -- expensive to retrofit; the layer-1 API
   carries them from the start, even if unimplemented.

## First steps, when picked up

1. Decide question 1.
2. Sketch on paper what an application looks like in TPy, in both modes, and
   the frame output they share (display list, hit regions, accessibility nodes,
   stable IDs) -- independent of the renderer.
3. Bind SDL3's window and events to validate the interop shape, including a
   callback.
4. "Hello rectangle" on the CPU renderer: a window, a canvas, one shaped line
   of text, with the measurements of the first principle in place.
5. A layout core in TPy over a node interface, driven by a small retained tree
   and a small immediate-mode frame.
