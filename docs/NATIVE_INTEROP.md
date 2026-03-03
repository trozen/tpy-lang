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
| 2 | `@native` class — import C++ class (fields) | **Done** |
| 2 | `@native` class — stub methods | **Done** |
| 3 | `@native_c` class — import C struct (fields) | **Done** |
| 4 | `@extern_c` class — export C struct | TODO |
| 5 | C header generation (`--emit-c-header`) | TODO |
| — | Include directive (`# tpy: include(...)`) | TODO |
| — | Link directive (`# tpy: link(...)`) | TODO |
| — | Opaque handle types | TODO |
| — | Field renaming (`# tpy: native(...)`) | TODO |
| — | Callback function pointers | Open |
| — | `@native` enum / constants | Open |
| — | Auto-bindgen from C headers | Open |
| — | Variadic C functions (`printf` etc.) | Open |
| — | `native_c_global()` — import C global variable | **Done** |
| — | `native_global()` — import C++ global variable | **Done** |
| — | String/buffer marshaling | Open |
| — | Conditional platform linking | Open |
| — | Inline C/C++ escape hatch | Open |

---

## API

Three decorators, all imported from `tpy.extern`:

```python
from tpy.extern import native, native_c, extern_c
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
# lib.py
from tpy.extern import native_c
from tpy import Int32

@native_c
def abs(x: Int32) -> Int32: ...

@native_c("clock")
def get_clock() -> Int32: ...
```

```python
# main.py
from tpy.extern import extern_c
from tpy import Int32
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
| `native_*_global()` inside a function | `can only be used at module level` |
| `native_*_global()` without type annotation | `requires a type annotation` |
| `native_*_global()` with non-string arg | `argument must be a string literal` |
| `native_*_global()` with >1 args | `takes 0 or 1 arguments` |

## Supported types

Native functions can use any type that has a direct C++ mapping:

| TPy type | C/C++ type |
|----------|-----------|
| `Int32` | `int32_t` |
| `float` | `double` |
| `bool` | `bool` |
| `None` (return) | `void` |

Planned: `str`, `Ptr[T]`, `ReadOnlyPtr[T]`, opaque handle types.

---

## Phase 2: `@native` classes

Import existing C++ classes so TPy code can declare their fields and pass them to native functions. No struct definition is generated — the compiler trusts the external type exists.

```python
from tpy.extern import native
from tpy import Int32, Float

# Bare @native — Python and C++ names match
@native
class Vec2:
    x: Int32
    y: Int32
    def sum(self) -> Int32: ...
    def dot(self, other: Vec2) -> Int32: ...
    @staticmethod
    def zero() -> Vec2: ...

# @native with rename — fully qualified C++ name
@native("b2::Vec2")
class Vec2:
    x: Float
    y: Float
    def length(self) -> Float: ...

# Opaque handle — no fields, just ...
@native("SDL_Window")
class Window:
    ...
```

### Stub methods

Methods on `@native` classes must be stubs (`...` body). They declare the method signature so TPy code can call it — the actual implementation lives in the C++ header.

```python
@native
class Vec2:
    x: Float
    y: Float
    def length(self) -> Float: ...              # instance method
    def dot(self, other: Vec2) -> Float: ...    # method with record param
    @staticmethod
    def zero() -> Vec2: ...                     # static method
    @native("mag")
    def magnitude(self) -> Float: ...           # method with C++ rename
```

Methods with real bodies (not `...`) produce a parse error.

### Generated C++

No struct definition. Construction uses C++ constructor call syntax:

```cpp
// Vec2(1, 2) → Vec2(1, 2)   (C++ constructor)
Vec2 v = Vec2(1, 2);
v.length();          // instance method call
Vec2::zero();        // static method call

// b2::Vec2(1.0, 2.0) → b2::Vec2(1.0, 2.0)
b2::Vec2 v = b2::Vec2(1.0, 2.0);
```

The native name also resolves inside composite types:

```python
@native_c
def get_vec(p: Ptr[Vec2]) -> Int32: ...
# → int32_t get_vec(b2::Vec2* p);
```

### Not yet supported

- **`# tpy: include()`** — header include directives
- **`# tpy: link()`** — link directives

## Include and link directives (planned)

When importing native functions or classes, the compiler needs to know which C/C++ headers to include and which libraries to link. Module-level pragmas declare these dependencies:

```python
# tpy: include("box2d/box2d.h")
# tpy: include(<SDL2/SDL.h>)
# tpy: link("box2d")
# tpy: link("SDL2")

from tpy.extern import native, native_c
from tpy import Int32

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

## Phase 3: `@native_c` classes

Import C structs. Similar to `@native` classes but with C linkage — no methods, no namespaces, POD-only. Construction uses aggregate initialization (`{}` syntax).

```python
from tpy.extern import native_c
from tpy import Int32

# Bare @native_c — Python and C names match
@native_c
class Point:
    x: Int32
    y: Int32

# @native_c with rename — C name differs from Python name
@native_c("SDL_Rect")
class Rect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32
```

### Generated C++

No struct definition. Construction uses aggregate initialization:

```cpp
// Point(5, 6) → Point{5, 6}   (aggregate init, POD)
Point p = Point{5, 6};

// Rect(0, 0, 800, 600) → SDL_Rect{0, 0, 800, 600}
SDL_Rect r = SDL_Rect{0, 0, 800, 600};
```

Works with native functions using these types:

```python
@native_c("SDL_RenderFillRect")
def fill_rect(renderer: Ptr[Renderer], rect: ReadOnlyPtr[Rect]) -> Int32: ...
# → int32_t SDL_RenderFillRect(Renderer* renderer, const SDL_Rect* rect);
```

## Field renaming (planned)

When importing C/C++ types, field names often don't match Python conventions. Field renaming lets you use Pythonic names while mapping to the actual C/C++ field names.

### Syntax options

**Option A: Comment pragma (recommended)**

```python
@native_c("timespec")
class TimeSpec:
    seconds: Int32      # tpy: native("tv_sec")
    nanoseconds: Int32  # tpy: native("tv_nsec")
```

Consistent with the `# tpy: include(...)` / `# tpy: link(...)` pragma style. The comment is parsed by the compiler, not by Python — so the source file remains valid Python with correct type annotations.

**Option B: `native()` as default value**

```python
@native_c("timespec")
class TimeSpec:
    seconds: Int32 = native("tv_sec")
    nanoseconds: Int32 = native("tv_nsec")
```

More visible and refactoring-friendly (tools can rename it). But `native("tv_sec")` isn't actually a default value — it's a rename directive. This overloads the assignment syntax in a potentially confusing way, and prevents using actual default values.

**Option C: Wrapper type**

```python
@native_c("timespec")
class TimeSpec:
    seconds: Native[Int32, "tv_sec"]
    nanoseconds: Native[Int32, "tv_nsec"]
```

Type-level encoding, visible to type checkers. But `Native[Int32, "tv_sec"]` is noisy compared to `Int32`, and composing with other type wrappers (`Optional[Native[Int32, "tv_sec"]]`) gets unwieldy.

### Semantics

Field renaming applies to both `@native` and `@native_c` classes. In codegen:

- `ts.seconds` in Python → `ts.tv_sec` in C++
- `TimeSpec(seconds=Int32(0), nanoseconds=Int32(0))` → `timespec{0, 0}` (positional, field names not emitted)

Fields without a rename annotation use their Python name as-is (current behavior).

## Phase 4: `@extern_c` classes (planned)

Export a TPy class with C-compatible layout, so it can be used from C code. The class must only contain C-compatible fields. The compiler generates a C-compatible struct definition.

```python
from tpy.extern import extern_c
from tpy import Int32

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
tpyc --emit-c-header src/game.py -o game_api.h
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
tpyc --bindgen /usr/include/SDL2/SDL.h -o sdl.py
```

This would produce a `.py` file with `@native_c` declarations for all functions, structs, enums, and constants found in the header. The generated file is checked in and can be hand-edited.

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

Import extern C/C++ global variables. Three functions, imported from `tpy.extern`:

```python
from tpy.extern import native_c_global, native_global
from tpy import Int32

# C global (extern "C" linkage)
frame_count: Int32 = native_c_global("DG_FrameCount")

# C global without rename (Python name = C name)
tick: Int32 = native_c_global()

# C++ global (regular linkage, possibly namespaced)
score: Int32 = native_global("engine::score")

# C++ global without rename
lives: Int32 = native_global()
```

Generated C++ (header, before namespace):
```cpp
extern "C" int32_t DG_FrameCount;
extern "C" int32_t tick;
namespace engine { extern int32_t score; }
extern int32_t lives;
```

References to these variables in TPy code emit the C/C++ name directly:
```python
print(frame_count)   # → std::cout << DG_FrameCount << "\n";
print(score)         # → std::cout << engine::score << "\n";
```

Native global imports must be at module level (not inside functions) and require a type annotation. Unlike `@native`/`@native_c` decorators (hard-coded in the parser), these are regular functions registered in the `tpy` module and detected in sema via namespace resolution — shadowing works correctly.

### String and buffer marshaling

Automatic conversion between TPy types and C types at the FFI boundary:

- `str` → `const char*` (already works for parameters)
- `str` ← `const char*` (wrap returned C string — ownership question)
- `list[T]` → `T*, size_t` (two-parameter expansion)
- `Span[T]` → `T*, size_t` (mutable)
- `ReadOnlySpan[T]` → `const T*, size_t`
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
from tpy.extern import native_c
from tpy import Int32, CCallback

@native_c
def qsort(base: Ptr[None], count: Int32, size: Int32,
           cmp: CCallback[[ReadOnlyPtr[None], ReadOnlyPtr[None]], Int32]) -> None: ...

def my_compare(a: ReadOnlyPtr[None], b: ReadOnlyPtr[None]) -> Int32:
    return deref(cast(a, ReadOnlyPtr[Int32])) - deref(cast(b, ReadOnlyPtr[Int32]))

qsort(data, n, Int32(4), my_compare)
```

Design considerations:
- `CCallback` type maps to a C function pointer in codegen
- Only `@extern_c` or plain functions can be used as callbacks (no closures capturing state)
- Closures with state would need a `void* userdata` pattern (common in C APIs)

### Conditional platform linking

Different platforms need different libraries. A conditional directive selects the right one:

```python
# tpy: link("ws2_32", platform="windows")
# tpy: link("pthread", platform="linux")
# tpy: link("pthread", platform="macos")
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
from tpy.extern import native_c

@native_c
class WindowFlags:
    SHOWN: Int32          # = SDL_WINDOW_SHOWN
    FULLSCREEN: Int32     # = SDL_WINDOW_FULLSCREEN
    RESIZABLE: Int32      # = SDL_WINDOW_RESIZABLE
```

Design open: should these be real enum types with exhaustiveness checking, or just named integer constants?

