# Project DOOM: Running DOOM in TurboPython

The ultimate benchmark for any computing platform: *"but does it run DOOM?"*

This document tracks the plan to port [doomgeneric](https://github.com/ozkl/doomgeneric)
— a portable DOOM engine with a minimal platform API — to TurboPython, incrementally.

## Status

### Phase 1: TPy Platform Layer + C Engine

Write the 6 platform functions in TPy, link with the C DOOM engine.

| #    | Item                              | Effort | Status |
|------|-----------------------------------|--------|--------|
| 1.0  | `@extern_c` / `@extern_cpp` decorator | S    | TODO   |
| 1.1  | `tpy_sdl2` package (separate project) | S-M  | TODO   |
| 1.2  | `extern_c` for exporting TPy functions to C | S | TODO |
| 1.3  | `extern_c` for external globals (`DG_ScreenBuffer`) | S | TODO |
| 1.4  | Write 6 `DG_*` functions in TPy   | S      | TODO   |
| 1.5  | Build system: link TPy C++ with C objects | S | TODO |
| 1.6  | Run DOOM with TPy platform layer   | -      | TODO   |

### Phase 2: Compiler Features for C-to-TPy Porting

Features needed to translate DOOM's C code to TurboPython.

| #    | Feature                           | Effort | Status |
|------|-----------------------------------|--------|--------|
| 2.1  | Unsigned integers (`UInt32`, `UInt8`) | S-M | TODO |
| 2.2  | Pointer arithmetic (`ptr + n`, `ptr[n]`) | M | TODO |
| 2.3  | Type casting (`cast[T](x)`)       | S      | TODO   |
| 2.4  | Function pointers / `Callable`    | M      | TODO   |
| 2.5  | Unions (tagged or `@union`)        | M      | TODO   |
| 2.6  | `match`/`case` or `switch`        | S-M    | TODO   |
| 2.7  | `Enum` support                    | S      | TODO   |
| 2.8  | Ternary expressions               | XS     | TODO   |
| 2.9  | Struct self-pointers (`Ptr[Self]`) | S      | TODO   |
| 2.10 | `sizeof[T]()` builtin             | S      | TODO   |

### Phase 3: Port C Modules to TPy (incremental)

| #    | Module            | Lines | Difficulty | Status |
|------|-------------------|-------|------------|--------|
| 3.1  | `doomkeys.h`      | ~50   | Trivial    | TODO   |
| 3.2  | `tables.c`        | ~2K   | Easy       | TODO   |
| 3.3  | `m_fixed.c`       | ~100  | Easy       | TODO   |
| 3.4  | `m_bbox.c`        | ~50   | Easy       | TODO   |
| 3.5  | `m_misc.c`        | ~300  | Easy       | TODO   |
| 3.6  | `i_video.c`       | ~200  | Medium     | TODO   |
| 3.7  | `r_draw.c`        | ~1K   | Hard       | TODO   |
| 3.8  | `r_main.c`        | ~1.5K | Hard       | TODO   |
| 3.9  | `p_enemy.c`       | ~2K   | Hard       | TODO   |
| 3.10 | `g_game.c`        | ~2.3K | Hard       | TODO   |
| 3.11 | Everything else    | ~40K  | Hard       | TODO   |

> **Effort key:** XS = hours, S = a day, M = a few days, L = a week+

---

## Background

### Why doomgeneric?

[doomgeneric](https://github.com/ozkl/doomgeneric) is a fork of Chocolate Doom with
all platform-specific code consolidated behind a 6-function API. Existing ports
(SDL, X11, Windows, Emscripten, Linux framebuffer) are each ~200 lines. The engine
handles all game logic, rendering, and palette conversion internally — the platform
layer just blits a pixel buffer to the screen and provides keyboard input.

### Codebase stats

- ~95 `.c` files, ~97 `.h` files (~50K lines total)
- Engine renders to 8-bit indexed color internally (320x200)
- Converts to 32-bit XRGB8888 in a 640x400 `DG_ScreenBuffer`
- GPL-2.0 licensed

---

## Phase 1: TPy Platform Layer

The minimum viable goal: DOOM running with a TurboPython platform layer linked
against the C engine.

### The 6 Platform Functions

These are the only functions a doomgeneric port must implement:

```c
void     DG_Init();                                    // Create window
void     DG_DrawFrame();                               // Blit DG_ScreenBuffer to screen
void     DG_SleepMs(uint32_t ms);                      // Sleep
uint32_t DG_GetTicksMs();                              // Monotonic millisecond clock
int      DG_GetKey(int* pressed, unsigned char* key);  // Dequeue keyboard event
void     DG_SetWindowTitle(const char* title);         // Set window title (can be no-op)
```

The engine provides:
- `doomgeneric_Create(argc, argv)` — call once at startup
- `doomgeneric_Tick()` — call in a loop (one game frame per call)
- `DG_ScreenBuffer` — `uint32_t*` pointing to 640x400 pixels in XRGB8888 format

### The `@extern_c` / `@extern_cpp` Mechanism

The key compiler feature enabling all of this. A decorator for declaring foreign
functions — the compiler emits direct C/C++ calls instead of generating a body.

```python
from tpy.extern import extern_c

# Module-level directives: what to #include and link
extern_c.include("SDL2/SDL.h")
extern_c.link("SDL2")

# Bare @extern_c — uses the Python function name as the C symbol
@extern_c
def SDL_Init(flags: Int32) -> Int32: ...

@extern_c
def SDL_Delay(ms: Int32) -> None: ...

# With explicit C name — for providing a Pythonic API
@extern_c("SDL_GetTicks")
def get_ticks() -> Int32: ...

@extern_c("SDL_CreateWindow")
def create_window(title: str, x: Int32, y: Int32, w: Int32, h: Int32, flags: Int32) -> Ptr[SDLWindow]: ...
```

- `@extern_c` = C linkage (`extern "C"`, no name mangling)
- `@extern_cpp` = C++ linkage (name mangling, for C++ libraries)
- Body is `...` (standard Python stub/Protocol convention)
- The TPy function signature provides type checking — no separate argtypes/restype
- `extern_c.include(...)` emits `#include` in the generated C++
- `extern_c.link(...)` passes `-l` to the linker

This also works for **exporting** TPy functions to C (needed for the DOOM platform
functions) and for **declaring external globals**:

```python
# Export a TPy function with C linkage (callable from C code)
@extern_c
def DG_Init() -> None:
    init_sdl()
    # ... actual TPy implementation ...

# Declare an external C global (defined in doomgeneric.c)
DG_ScreenBuffer: Ptr[Int32] = extern_c.var("DG_ScreenBuffer")
```

### SDL2 Integration

`tpy_sdl2` is a separate package (its own repo, not part of tpyc) that provides SDL2
bindings using `@extern_c`. It's pure TurboPython — just function declarations:

```python
# tpy_sdl2/sdl2.py
from tpy.extern import extern_c
from tpy import Int32, Ptr

extern_c.include("SDL2/SDL.h")
extern_c.link("SDL2")

INIT_VIDEO: Int32 = Int32(0x00000020)
QUIT: Int32 = Int32(0x100)
KEYDOWN: Int32 = Int32(0x300)
KEYUP: Int32 = Int32(0x301)
# ...

@extern_c
def SDL_Init(flags: Int32) -> Int32: ...

@extern_c
def SDL_Quit() -> None: ...

@extern_c
def SDL_CreateWindow(title: str, x: Int32, y: Int32, w: Int32, h: Int32, flags: Int32) -> Ptr[SDLWindow]: ...

@extern_c
def SDL_CreateRenderer(window: Ptr[SDLWindow], index: Int32, flags: Int32) -> Ptr[SDLRenderer]: ...

@extern_c
def SDL_CreateTexture(renderer: Ptr[SDLRenderer], fmt: Int32, access: Int32, w: Int32, h: Int32) -> Ptr[SDLTexture]: ...

@extern_c
def SDL_UpdateTexture(texture: Ptr[SDLTexture], rect: Ptr[SDLRect], pixels: Ptr[Int32], pitch: Int32) -> Int32: ...

@extern_c
def SDL_RenderCopy(renderer: Ptr[SDLRenderer], texture: Ptr[SDLTexture], src: Ptr[SDLRect], dst: Ptr[SDLRect]) -> Int32: ...

@extern_c
def SDL_RenderPresent(renderer: Ptr[SDLRenderer]) -> None: ...

@extern_c
def SDL_PollEvent(event: Ptr[SDLEvent]) -> Int32: ...

@extern_c("SDL_GetTicks")
def get_ticks() -> Int32: ...

@extern_c("SDL_Delay")
def delay(ms: Int32) -> None: ...
```

Anyone can write bindings for any C library using this pattern — no compiler changes
needed beyond the initial `@extern_c` support.

### What the TPy Platform Layer Looks Like

```python
# doomgeneric_tpy.py
from tpy.extern import extern_c
from tpy import Int32, Ptr, Array
from tpy_sdl2 import (
    SDL_Init, SDL_CreateWindow, SDL_CreateRenderer, SDL_CreateTexture,
    SDL_UpdateTexture, SDL_RenderCopy, SDL_RenderPresent, SDL_PollEvent,
    get_ticks, delay, INIT_VIDEO, KEYDOWN, KEYUP
)

extern_c.include("doomgeneric.h")

# External C global (defined in doomgeneric.c)
DG_ScreenBuffer: Ptr[Int32] = extern_c.var("DG_ScreenBuffer")

# Keyboard event queue (same pattern as all doomgeneric ports)
key_queue: Array[Int32, 16]
key_queue_read: Int32 = Int32(0)
key_queue_write: Int32 = Int32(0)

@extern_c
def DG_Init() -> None:
    SDL_Init(INIT_VIDEO)
    # create window, renderer, texture ...

@extern_c
def DG_DrawFrame() -> None:
    # pump SDL events, enqueue keys
    SDL_UpdateTexture(texture, null, DG_ScreenBuffer, Int32(640 * 4))
    SDL_RenderCopy(renderer, texture, null, null)
    SDL_RenderPresent(renderer)

@extern_c
def DG_SleepMs(ms: Int32) -> None:
    delay(ms)

@extern_c
def DG_GetTicksMs() -> Int32:
    return get_ticks()

@extern_c
def DG_GetKey(pressed: Ptr[Int32], key: Ptr[Int32]) -> Int32:
    if key_queue_read == key_queue_write:
        return Int32(0)
    # dequeue and unpack ...
    return Int32(1)

@extern_c
def DG_SetWindowTitle(title: str) -> None:
    pass  # no-op for now
```

Note: `@extern_c` on `DG_Init` etc. serves double duty — these functions have a TPy
body (they're implemented in TPy) but are exported with C linkage so the C engine
can call them.

### Build System

Compile TPy output to `.o`, compile DOOM `.c` files to `.o`, link together with SDL2:

```
DOOM C sources (*.c)  ──→  gcc -c  ──→  doom_engine.o (multiple .o files)
TPy platform (*.py)    ──→  tpyc    ──→  platform.cpp ──→  g++ -c ──→  platform.o
                                                                          │
SDL2 library (libSDL2) ─────────────────────────────────────────────────┐ │
                                                                        ↓ ↓
                                                             g++ -o doom platform.o doom_*.o -lSDL2
```

---

## Phase 2: Compiler Features for DOOM Porting

Features needed to translate DOOM's C code into TurboPython. Ordered by how many
modules they unblock.

### 2.1 Unsigned Integers

DOOM uses unsigned types pervasively:

- `uint32_t` — angle math (Binary Angle Measurement uses full 32-bit unsigned range:
  `ANG90 = 0x40000000`), pixel data, bitwise flags
- `uint8_t` — palette indices, byte buffers
- `unsigned int` — general counters

**Suggested types:** `UInt32`, `UInt8`, possibly `UInt16`.

Maps to C++ `uint32_t`, `uint8_t`, `uint16_t`. Needs: arithmetic, bitwise ops,
comparison, coercion rules with signed types.

### 2.2 Pointer Arithmetic

The renderer's inner loops are built on pointer arithmetic:

```c
*dest = dc_colormap[dc_source[(frac >> FRACBITS) & 127]];
dest += SCREENWIDTH;
frac += fracstep;
```

`ylookup[]` and `columnofs[]` are precomputed pointer offsets. Buffer traversal
everywhere uses `ptr++`, `ptr += n`, `ptr[n]`.

**Needed:** `Ptr[T] + n`, `Ptr[T] - n`, `ptr[n]` (subscript on pointer),
`Ptr[T] - Ptr[T]` (pointer difference). Maps directly to C++ pointer arithmetic.

### 2.3 Type Casting

DOOM casts freely between integer sizes and between pointer types:

```c
fixed_t result = (fixed_t)((long long)a * b >> FRACBITS);
pixel_t color = (pixel_t)palette[index];
byte *zone = (byte *)Z_Malloc(size);
```

**Suggested:** `cast[T](expr)` builtin function. Generates `static_cast<T>(expr)` or
`reinterpret_cast<T>(expr)` depending on types.

### 2.4 Function Pointers / Callable

The thinker system — DOOM's core game object update mechanism — is entirely
function-pointer-driven:

```c
typedef void (*actionf_v)();
typedef void (*actionf_p1)(void *);
typedef void (*actionf_p2)(void *, void *);

typedef union {
    actionf_v  acv;
    actionf_p1 acp1;
    actionf_p2 acp2;
} actionf_t;
```

Every active game object (`mobj_t`) has an `actionf_t function` field called each tick.

**Needed:** `Callable[[Ptr[T]], None]` type mapping to C++ function pointers or
`std::function`. For DOOM's pattern specifically, a function pointer is better
(no overhead).

### 2.5 Unions

DOOM uses unions in a few key places:

- `actionf_t` — three function pointer types (the thinker dispatch mechanism)
- Some WAD file structures

**Options:**
- Tagged union: `@union class ActionF: ...` with a tag field
- Raw union: `@union` with explicit tag management (closer to C semantics)
- For DOOM specifically, the `actionf_t` union could be modeled as a single
  `Callable` type since only one variant is used at a time

### 2.6 `match`/`case` or `switch`

DOOM uses `switch` extensively for key mapping, state machines, and action dispatch.
Many have 10-30+ cases.

Can be worked around with `if`/`elif` chains, but `match`/`case` would be much
cleaner and could compile to C++ `switch` for performance.

### 2.7 Enum

DOOM defines dozens of enums: game states, keys, weapons, ammo types, power-ups,
sprite indices, action types. Many are used as array indices.

Already on the self-hosting roadmap. Maps to `enum class` in C++.

### 2.8 Ternary Expressions

Used throughout DOOM's code. Already on the self-hosting roadmap. Maps to `? :`.

### 2.9 Struct Self-Pointers

DOOM's linked lists use struct self-references:

```c
typedef struct thinker_s {
    struct thinker_s *prev;
    struct thinker_s *next;
    actionf_t function;
} thinker_t;
```

`mobj_t` has `snext`/`sprev` (sector list) and `bnext`/`bprev` (blockmap list).

**Needed:** Allow `Ptr[Self]` or `Ptr[MyRecord]` in record field definitions.
Forward references in type annotations.

### 2.10 `sizeof[T]()` Builtin

Used by the zone memory allocator: `Z_Malloc(sizeof(mobj_t), ...)`.

Maps to C++ `sizeof(T)`. Useful for any low-level memory work.

---

## Phase 3: Porting C Modules to TPy

Modules ordered from easiest to hardest, based on C features used.

### Easy Targets

**`doomkeys.h`** (~50 lines) — Pure constants. Doom key code definitions.

```python
# doomkeys.py
from tpy import Int32

KEY_RIGHTARROW: Int32 = Int32(0xae)
KEY_LEFTARROW: Int32 = Int32(0xac)
KEY_UPARROW: Int32 = Int32(0xad)
KEY_DOWNARROW: Int32 = Int32(0xaf)
KEY_FIRE: Int32 = Int32(0xa3)
KEY_USE: Int32 = Int32(0xa2)
KEY_ENTER: Int32 = Int32(13)
KEY_ESCAPE: Int32 = Int32(27)
# ...
```

**`tables.c`** (~2K lines) — Precomputed trig lookup tables. Arrays of `Int32`
(fixed-point). No logic, just data.

```python
# tables.py
from tpy import Int32, Array

FINEANGLES: Int32 = Int32(8192)
finetangent: Array[Int32, 4096] = [Int32(0x00000000), Int32(0x000000c9), ...]
finesine: Array[Int32, 10240] = [Int32(0x00000000), Int32(0x000000c9), ...]
finecosine_offset: Int32 = Int32(2048)  # finecosine = &finesine[FINEANGLES/4]
```

**`m_fixed.c`** (~100 lines) — Fixed-point math. 5 functions using `Int32`
arithmetic and bitwise shifts:

```python
# m_fixed.py
from tpy import Int32

FRACBITS: Int32 = Int32(16)
FRACUNIT: Int32 = Int32(1 << 16)

def fixed_mul(a: Int32, b: Int32) -> Int32:
    # return (int)((long long)a * b >> FRACBITS)
    ...

def fixed_div(a: Int32, b: Int32) -> Int32:
    ...
```

**`m_bbox.c`** (~50 lines) — Bounding box operations. 4 functions, pure `Int32`
comparison and assignment.

### Medium Targets

**`m_misc.c`** (~300 lines) — Utilities: file I/O, string operations, config
parsing. Needs string methods and file access.

**`i_video.c`** (~200 lines) — Palette management and the `I_FinishUpdate` function
that converts 8-bit indexed to XRGB8888 in `DG_ScreenBuffer`. Needs pointer
arithmetic and unsigned integer support.

### Hard Targets

**`r_draw.c`** (~1K lines) — The column and span renderers. Inner loops with heavy
pointer arithmetic, fixed-point math, and lookup table indexing. Performance-critical.

**`r_main.c`** (~1.5K lines) — BSP traversal, view setup, angle calculations. Uses
unsigned angle math and trig lookup tables.

**`p_enemy.c`** (~2K lines) — Enemy AI. Function pointers, state machines, lots of
game state access.

**`g_game.c`** (~2.3K lines) — Main game loop, save/load, demo recording. Touches
everything.

---

## C Features DOOM Uses — Full Mapping

### Supported or trivial

| C Feature | TPy Equivalent | Notes |
|-----------|---------------|-------|
| `int32_t` arithmetic | `Int32` | Working |
| Bitwise ops | Same syntax | Working |
| Structs | Records/classes | Working |
| Fixed-size arrays | `Array[T, N]` | Working |
| Pointers | `Ptr[T]`, `ConstPtr[T]` | Working |
| Global variables | Module-level vars | Working |
| `for`/`while` loops | Same | Working |
| `if`/`else` | Same | Working |
| Functions | Same | Working |
| `void` return | `-> None` | Working |
| `#define` constants | Module-level `Int32` | Working |
| `static` file-scope vars | Module-level vars | Working |
| `#include` | `from module import ...` | Working |
| Multi-file compilation | Module system | Working |

### Needs new compiler features

| C Feature | Suggested TPy Feature | Effort |
|-----------|----------------------|--------|
| `uint32_t`, `uint8_t` | `UInt32`, `UInt8` types | S-M |
| Pointer arithmetic | `Ptr[T] + n`, `ptr[n]` | M |
| Type casting | `cast[T](expr)` | S |
| Function pointers | `Callable` / fn pointer type | M |
| Unions | `@union` or tagged union | M |
| `switch`/`case` | `match`/`case` | S-M |
| Enums | `Enum` support | S |
| Ternary `? :` | `x if cond else y` | XS |
| Struct self-refs | `Ptr[Self]` in records | S |
| `sizeof` | `sizeof[T]()` | S |
| `extern "C"` imports | `@extern_c` decorator | S |
| `extern "C"` exports | `@extern_c` on TPy functions | S |
| External globals | `extern_c.var("name")` | S |

### Needs source refactoring (not compiler features)

| C Pattern | TPy Workaround |
|-----------|---------------|
| `do-while` | `while True:` + conditional `break` |
| `goto` | Restructure with loops and flags (~5 uses) |
| Comma operator | Split into separate statements |
| Complex macros | Inline functions |
| `void*` generic pointers | `Ptr[UInt8]` with explicit casting |
| `setjmp`/`longjmp` | Not used in doomgeneric |

---

## Build & Link Strategy

### Phase 3 build pipeline (gradual migration)

As modules get ported from C to TPy:
- Replace individual `.c` files with `.py` equivalents
- The TPy-generated C++ implements the same functions with the same signatures
- Link everything together as before
- The C and TPy modules coexist, sharing headers/types via `extern "C"`

Each ported module can be tested independently by checking that DOOM still runs
correctly after the swap.
