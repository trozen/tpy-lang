# Native Interop — Design Document

TurboPython can import existing C/C++ functions and export its own functions with C linkage. This enables embedding TPy code in C/C++ projects and calling external libraries without bindings or wrappers.

## Progress

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | `@native` — import C++ function | **Done** |
| 1 | `@native("ns::func")` — qualified C++ name | **Done** |
| 1 | `@native_c` — import C function | **Done** |
| 1 | `@native_c("symbol")` — renamed C symbol | **Done** |
| 1 | `@extern_c` — export TPy function | **Done** |
| 1 | `@extern_c("symbol")` — renamed export | **Done** |
| 1 | Cross-module native imports | **Done** |
| 1 | Package re-exports of native functions | **Done** |
| 1 | Duplicate symbol detection | **Done** |
| 1 | Conflicting decorator detection | **Done** |
| 1 | Body validation (stub vs real) | **Done** |
| 2 | `@native` class — import C++ class | TODO |
| 3 | `@native_c` class — import C struct | TODO |
| 4 | `@extern_c` class — export C struct | TODO |
| 5 | C header generation (`--emit-c-header`) | TODO |
| — | Include directive (`# tpy: include(...)`) | TODO |
| — | Link directive (`# tpy: link(...)`) | TODO |
| — | Opaque handle types | TODO |
| — | Callback function pointers | Open |
| — | `@native` enum / constants | Open |
| — | Auto-bindgen from C headers | Open |
| — | Variadic C functions (`printf` etc.) | Open |
| — | Native global variables | Open |
| — | String/buffer marshaling | Open |
| — | `pkg-config` integration | Open |
| — | Conditional platform linking | Open |
| — | Inline C/C++ escape hatch | Open |

---

## API

Three decorators, all imported from `tpy`:

```python
from tpy import native, native_c, extern_c
```

### `@native` — Import a C++ function

Declares a C++ function that exists elsewhere (a header, a library). The compiler emits a forward declaration; no body is generated. The function must have a `...` (stub) body.

```python
@native
def global_func(x: Int32) -> Int32: ...

@native("physics::calculate_force")
def calc_force(mass: float, accel: float) -> float: ...
```

Generated C++ (header, before the module namespace):
```cpp
int32_t global_func(int32_t x);
namespace physics { double calculate_force(double mass, double accel); }
```

The optional string argument specifies the C++ qualified name. Supports `namespace::function` syntax — the compiler splits on `::` and wraps the declaration in the appropriate namespace block.

### `@native_c` — Import a C function

Same as `@native` but with C linkage (`extern "C"`). The declaration is emitted inside the module namespace (C symbols are globally unique, so namespace placement doesn't matter for linking).

```python
@native_c
def abs(x: Int32) -> Int32: ...

@native_c("clock")
def get_clock() -> Int32: ...
```

Generated C++ (header):
```cpp
namespace tpy_user::main {
extern "C" int32_t abs(int32_t x);
extern "C" int32_t clock();
}
```

The optional string argument provides the C symbol name when it differs from the Python function name. Calls to `get_clock()` in Python emit `clock()` in C++.

### `@extern_c` — Export a TPy function with C linkage

Compiles a TurboPython function and emits it with `extern "C"` linkage so it can be called from C/C++ code. The function must have a real body (not `...`).

```python
@extern_c
def app_init() -> None:
    print("app_init called")

@extern_c("app_tick")
def game_tick(time: Int32) -> None:
    print(time)
```

Generated C++ (header + source):
```cpp
// Header
extern "C" void app_init();
extern "C" void app_tick(int32_t time);

// Source
extern "C" void app_init() {
  std::cout << "app_init called" << "\n";
}

extern "C" void app_tick(int32_t time) {
  std::cout << time << "\n";
}
```

## Symbol naming

All three decorators accept an optional string argument to specify the native symbol name:

```python
@native_c("SDL_Init")       # Python name: init_sdl, C symbol: SDL_Init
def init_sdl(flags: Int32) -> Int32: ...

@extern_c("Helper_Add")     # Python name: helper_add, C symbol: Helper_Add
def helper_add(x: Int32) -> Int32:
    return x + Int32(1)
```

When calling `helper_add()` from TPy code, the compiler emits `Helper_Add()` in C++.

## Cross-module imports

Native C functions imported in one module can be used in another via normal Python imports. The compiler re-declares the `extern "C"` symbol in each module that uses it:

```python
# lib.tp.py
from tpy import native_c, Int32

@native_c
def abs(x: Int32) -> Int32: ...

@native_c("clock")
def get_clock() -> Int32: ...
```

```python
# main.tp.py
from tpy import extern_c, Int32
from lib import abs, get_clock

@extern_c
def app_init() -> None:
    abs(Int32(0))
    x: Int32 = get_clock()
    print(x)
```

Both `lib.hpp` and `main.hpp` get `extern "C"` declarations for `abs` and `clock`. The linker resolves them to the same symbol.

## Errors

| Condition | Error |
|-----------|-------|
| `@native`/`@native_c` with real body | `must have '...' body` |
| `@extern_c` with `...` body | `must have a body` |
| Two linkage decorators on same function | `cannot have both @X and @Y` |
| Same native symbol declared twice | `Duplicate extern symbol 'name'` |
| Non-string argument to decorator | `@native_c() requires a single string argument` |

## Supported types

Native functions can use any type that has a direct C++ mapping:

| TPy type | C/C++ type |
|----------|-----------|
| `Int32` | `int32_t` |
| `float` | `double` |
| `bool` | `bool` |
| `None` (return) | `void` |

Planned: `str`, `Ptr[T]`, `ConstPtr[T]`, opaque handle types.

---

## Phase 2: `@native` classes (planned)

Import existing C++ classes so TPy code can construct them, call their methods, and pass them around. The class body contains only type annotations and stub methods — no code is generated.

```python
from tpy import native

@native("SDL_Window")
class Window:
    """Opaque handle — no fields exposed."""
    ...

@native("b2::Vec2")
class Vec2:
    x: float
    y: float

    def length(self) -> float: ...
    def normalize(self) -> None: ...

@native("b2::World")
class World:
    def create_body(self, pos: Vec2) -> Ptr[Body]: ...
    def step(self, dt: float, vel_iters: Int32, pos_iters: Int32) -> None: ...
```

### Generated C++

No struct definition — the compiler trusts the external type exists and emits the correct qualified name:

```cpp
// Calls use the native C++ name directly
b2::Vec2 v{1.0, 2.0};
v.normalize();
double len = v.length();

b2::World world;
auto* body = world.create_body(v);
world.step(0.016, 8, 3);
```

### Design considerations

- **Opaque vs transparent**: A `@native` class with only `...` body is opaque (pointer-only, no field access). A class with field annotations is transparent (fields are accessible, layout must match C++).
- **Construction**: `Vec2(1.0, 2.0)` → aggregate initialization or constructor call depending on whether the native type has constructors.
- **Ownership**: By default, `@native` class instances are treated as non-value types (pointer-local model). `Own[Window]` for by-value semantics.
- **Include directive**: See "Include and link directives" section below.

## Include and link directives (planned)

When importing native functions or classes, the compiler needs to know which C/C++ headers to include and which libraries to link. Module-level pragmas declare these dependencies:

```python
# tpy: include("box2d/box2d.h")
# tpy: include(<SDL2/SDL.h>)
# tpy: link("box2d")
# tpy: link("SDL2")

from tpy import native, native_c, Int32

@native("b2::Vec2")
class Vec2:
    x: float
    y: float

@native_c
def SDL_Init(flags: Int32) -> Int32: ...
```

**`# tpy: include(...)`** — emits a `#include` directive in the generated C++ header. Quoted paths use `#include "..."`, angle-bracket paths use `#include <...>`.

**`# tpy: link(...)`** — passes `-l<name>` to the linker. The compiler collects link directives from all modules and forwards them when building the final binary.

Both directives are module-scoped: they apply to the file they appear in. Transitive dependencies (module A imports module B which needs libfoo) are resolved automatically — the compiler collects all link/include directives from the dependency graph.

### Alternative: decorator parameter

An alternative syntax puts the include on the decorator itself:

```python
@native("b2::Vec2", include="box2d/box2d.h")
class Vec2: ...
```

The pragma approach is preferred because a single header often covers multiple declarations, avoiding repetition.

## Phase 3: `@native_c` classes (planned)

Import C structs. Similar to `@native` classes but with C linkage — no methods, no namespaces, POD-only.

```python
from tpy import native_c

@native_c("SDL_Rect")
class Rect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32
```

Generated C++ uses `extern "C"` compatible types only. Methods on C structs would be free functions:

```python
@native_c("SDL_RenderFillRect")
def fill_rect(renderer: Ptr[Renderer], rect: ConstPtr[Rect]) -> Int32: ...
```

## Phase 4: `@extern_c` classes (planned)

Export a TPy class with C-compatible layout, so it can be used from C code. The class must only contain C-compatible fields. The compiler generates a C-compatible struct definition.

```python
from tpy import extern_c, Int32

@extern_c
class GameState:
    score: Int32
    level: Int32

    def reset(self) -> None:
        self.score = Int32(0)
        self.level = Int32(1)
```

This would generate a `extern "C"` struct plus free functions for methods:

```cpp
extern "C" {
  struct GameState {
    int32_t score;
    int32_t level;
  };

  void GameState_reset(GameState* self);
}
```

## Phase 5: Header generation (planned)

Generate a standalone C header (`.h`) from all `@extern_c` declarations in a module, so C projects can include it directly:

```bash
tpyc --emit-c-header src/game.tp.py -o game_api.h
```

Output:
```c
#ifndef GAME_API_H
#define GAME_API_H

#include <stdint.h>

void app_init(void);
void app_tick(int32_t time);

typedef struct GameState {
    int32_t score;
    int32_t level;
} GameState;

void GameState_reset(GameState* self);

#endif
```

---

## Future ideas

### Auto-bindgen from C headers

Instead of manually writing `@native_c` declarations, parse C headers and generate them automatically. Inspired by Rust's `bindgen` and Zig's `@cImport` which can directly consume C headers.

```bash
tpyc --bindgen /usr/include/SDL2/SDL.h -o sdl.tp.py
```

This would produce a `.tp.py` file with `@native_c` declarations for all functions, structs, enums, and constants found in the header. The generated file is checked in and can be hand-edited.

A more ambitious approach (Zig-style) would let the compiler parse headers at compile time:

```python
# tpy: cimport("SDL2/SDL.h")
# Makes all symbols from SDL.h available as native_c
```

### Variadic C functions

C functions like `printf`, `snprintf`, `ioctl` accept variable arguments. This requires special handling since the compiler needs to emit the correct C variadic call.

```python
@native_c
def printf(fmt: str, *args: ...) -> Int32: ...  # syntax TBD
```

Design considerations:
- Type safety: the compiler can't check variadic args against the format string
- Could limit to known safe patterns (e.g. only allow literal format strings)
- Alternative: don't support variadics, provide typed wrappers instead

### Native global variables

Import extern C/C++ global variables and constants, not just functions.

```python
from tpy import native_c, Int32

@native_c
SCREEN_WIDTH: Int32      # extern int32_t SCREEN_WIDTH;

@native_c("stderr")
err_stream: Ptr[FILE]    # extern FILE* stderr;
```

This maps to `extern` variable declarations in C++. Read-only by default; mutation would require explicit opt-in.

### String and buffer marshaling

Automatic conversion between TPy types and C types at the FFI boundary:

- `str` → `const char*` (already works for parameters)
- `str` ← `const char*` (wrap returned C string — ownership question)
- `list[T]` → `T*, size_t` (two-parameter expansion)
- `Span[T]` → `const T*, size_t`
- `bytes` → `uint8_t*, size_t` (future, when `bytes` type exists)

The compiler could automatically split a `list[Int32]` parameter into pointer + length when calling a C function that expects them:

```python
@native_c
def process_data(data: Span[Int32], count: Int32) -> None: ...

# Calling with a list automatically passes .data() and .size()
process_data(my_list)  # sugar for process_data(my_list.data(), len(my_list))
```

This is similar to C#'s marshaling attributes or Cython's typed memoryviews.

### Callback function pointers

Pass TPy functions as C callbacks. The compiler generates a C-compatible function pointer from a TPy function.

```python
from tpy import native_c, Int32, CCallback

@native_c
def qsort(base: Ptr[None], count: Int32, size: Int32,
           cmp: CCallback[[ConstPtr[None], ConstPtr[None]], Int32]) -> None: ...

def my_compare(a: ConstPtr[None], b: ConstPtr[None]) -> Int32:
    return deref(cast(a, ConstPtr[Int32])) - deref(cast(b, ConstPtr[Int32]))

qsort(data, n, Int32(4), my_compare)
```

Design considerations:
- `CCallback` type maps to a C function pointer in codegen
- Only `@extern_c` or plain functions can be used as callbacks (no closures capturing state)
- Closures with state would need a `void* userdata` pattern (common in C APIs)

### `pkg-config` integration

Automatically discover include paths and linker flags from system packages, similar to Go's `#cgo pkg-config:` directive:

```python
# tpy: pkg-config("sdl2", "opengl")
```

This runs `pkg-config --cflags sdl2 opengl` and `pkg-config --libs sdl2 opengl` and passes the results to the C++ compiler and linker. Avoids hardcoding platform-specific paths.

### Conditional platform linking

Different platforms need different libraries. A conditional directive selects the right one:

```python
# tpy: link("ws2_32", platform="windows")
# tpy: link("pthread", platform="linux")
# tpy: link("pthread", platform="macos")
```

Or combined with pkg-config:
```python
# tpy: pkg-config("sdl2", platform="linux")
# tpy: framework("SDL2", platform="macos")     # macOS framework
```

### Inline C/C++ escape hatch

For cases where writing a full native wrapper is overkill, allow inline C/C++ code blocks. Inspired by Nim's `{.emit.}` and D's string mixins.

```python
from tpy import inline_cpp, Int32

def fast_popcount(x: Int32) -> Int32:
    return inline_cpp("__builtin_popcount({x})")
```

This is a last resort — it breaks portability and type safety. But it's useful for one-off intrinsics, inline assembly, or compiler builtins that don't warrant a full native declaration.

### Native enums and constants

Import C/C++ enums as TPy types:

```python
from tpy import native_c

@native_c
class WindowFlags:
    SHOWN: Int32          # = SDL_WINDOW_SHOWN
    FULLSCREEN: Int32     # = SDL_WINDOW_FULLSCREEN
    RESIZABLE: Int32      # = SDL_WINDOW_RESIZABLE
```

Design open: should these be real enum types with exhaustiveness checking, or just named integer constants?

