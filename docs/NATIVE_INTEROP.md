# Native Interop -- Design Document

TurboPython can import existing C/C++ functions/types and export its own functions with C linkage. This enables embedding TPy code in C/C++ projects and calling external libraries without bindings or wrappers.

## Overview

Two decorators and module-level directives form the core of the system:

```python
from tpy.extern import native, export
```

- `@native` -- import a C/C++ entity (function, class, global) into TPy
- `@export(binding="C")` -- export a TPy function with C linkage
- `# tpy: native_module` -- mark a module as declaration-only (no generated code)

## Progress

| Feature | Status |
|---------|--------|
| `@native` -- import C++ function | **Done** |
| `@native("ns::func")` -- qualified C++ name | **Done** |
| `@native(binding="C")` -- import C function | **Done** (currently `@native_c`) |
| `@native(binding="C")` -- import C struct | **Done** (currently `@native_c`) |
| `@native` class -- import C++ class (fields, stub methods) | **Done** |
| `@export(binding="C")` -- export TPy function | **Done** (currently `@extern_c`) |
| `native_global()` -- import C/C++ global variable | **Done** (currently split across 3 functions) |
| `# tpy: native_module` | **Done** |
| `# tpy: include("header")` | **Done** |
| `# tpy: include("header", platform="linux")` | **Done** |
| `# tpy: link("lib")` | **Done** |
| `# tpy: link("lib", platform="linux")` | **Done** |
| Cross-module native imports | **Done** |
| Package re-exports of native functions | **Done** |
| Include propagation from native modules | **Done** |
| Auto-prefix bare `@native` with `cpp_namespace` | **Done** |
| Duplicate symbol detection | **Done** |
| `@export(binding="C")` class -- export C struct | Planned |
| C header generation (`--emit-c-header`) | Planned |
| Callback function pointers | Open |
| Native enums / constants | Open |
| Auto-bindgen from C headers | Open |
| Variadic C functions | Open |

### Migration from current API

| Current | New |
|---------|-----|
| `@native_c` / `@native_c("symbol")` | `@native(binding="C")` / `@native("symbol", binding="C")` |
| `@extern_c` / `@extern_c("symbol")` | `@export(binding="C")` / `@export("symbol", binding="C")` |
| `native_c_global("name")` | `native_global("name", binding="C")` |
| `native_c_global_array("name")` | `native_global("name", binding="C", array=True)` |
| `# tpy: native_module(forward=True)` | Dropped (include propagation replaces it) |
| `# tpy: cpp_include_path("path")` | Dropped |

---

## Importing: `@native`

Declares a C/C++ entity that exists elsewhere. The compiler uses it for type-checking and codegen but generates no code for the entity itself. The actual definition must be available via `# tpy: include()` or the external build system.

### Functions

```python
from tpy.extern import native

# C++ function (default binding)
@native("physics::calculate_force")
def calc_force(mass: float, accel: float) -> float: ...

# C function
@native("SDL_Init", binding="C")
def sdl_init(flags: Int32) -> Int32: ...

# Bare @native -- uses Python name as C++ name
@native
def global_func(x: Int32) -> Int32: ...
```

Functions must have a `...` (stub) body. The optional string argument specifies the C/C++ symbol name. Without it, the Python function name is used, prefixed by `cpp_namespace` if the module is a `native_module` (see below).

### Classes

Import existing C++ classes or C structs. No struct definition is generated -- the compiler trusts the external type exists.

```python
# C++ class with methods
@native("b2::Vec2")
class Vec2:
    x: float
    y: float
    def length(self) -> float: ...
    def dot(self, other: Vec2) -> float: ...
    @staticmethod
    def zero() -> Vec2: ...
    @native("mag")                      # method rename
    def magnitude(self) -> float: ...

# C struct (aggregate initialization)
@native("SDL_Rect", binding="C")
class Rect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32

# Opaque handle -- no fields
@native("SDL_Window")
class Window: ...
```

All methods on a `@native` class must be stubs (`...` body). Non-native methods on a native class are an error.

**Construction:**
- C++ classes: constructor call syntax -- `Vec2(1.0, 2.0)` -> `b2::Vec2(1.0, 2.0)`
- C structs (`binding="C"`): aggregate init -- `Rect(0, 0, 800, 600)` -> `SDL_Rect{0, 0, 800, 600}`

### Global variables

```python
from tpy.extern import native_global

# C++ global
score: Int32 = native_global("engine::score")

# C global
frame_count: Int32 = native_global("DG_FrameCount", binding="C")

# C global array (decays to pointer)
data: Ptr[Int32] = native_global("shared_data", binding="C", array=True)

# Bare -- uses Python name, prefixed by cpp_namespace in native_module
tick: Int32 = native_global()
```

Global imports must be at module level and require a type annotation. Generated C++:

```cpp
// C++ global
namespace engine { extern int32_t score; }

// C global
extern "C" int32_t DG_FrameCount;

// C global array
extern "C" int32_t shared_data[];
```

References to imported globals emit the C/C++ name directly: `print(frame_count)` -> `std::cout << DG_FrameCount`.

### Generic classes

```python
@native("std::vector")
class StdVector(Generic[T]):
    def push_back(self, val: T) -> None: ...
    def size(self) -> Int32: ...
```

---

## Exporting: `@export`

Compiles a TPy function and emits it with C linkage so it can be called from C/C++ code.

```python
from tpy.extern import export

# Export with C linkage
@export("app_init", binding="C")
def init() -> None:
    print("initialized")

# Bare -- uses Python name as C symbol
@export(binding="C")
def app_tick(dt: float) -> None:
    update_state(dt)
```

The function must have a body (not `...`). The optional string argument specifies the C symbol name. `binding="C"` is required.

Generated C++:

```cpp
extern "C" void app_init() {
    std::cout << "initialized" << "\n";
}
```

### Classes (planned)

Export a TPy class with C-compatible layout:

```python
@export(binding="C")
class GameState:
    score: Int32
    level: Int32

    def reset(self) -> None:
        self.score = Int32(0)
        self.level = Int32(1)
```

Would generate:

```cpp
extern "C" {
    struct GameState { int32_t score; int32_t level; };
    void GameState_reset(GameState* self);
}
```

---

## Module directives

### `# tpy: native_module`

Marks a module as declaration-only. No `.hpp` or `.cpp` is generated. All `@native` entities are for type-checking only. `# tpy: include()` directives are propagated to importing modules.

Each module that should be declaration-only must have its own `# tpy: native_module` directive -- it does not propagate from package `__init__.py` to child modules.

**Auto-prefix with `cpp_namespace`:** When a `native_module` also has `# tpy: cpp_namespace("ns")`, bare `@native` entities (without an explicit name) are auto-prefixed with the namespace:

```python
# tpy: native_module
# tpy: cpp_namespace("engine::core")
# tpy: include("<engine/core/types.hpp>")

from tpy.extern import native, native_global

@native                          # -> engine::core::Session
class Session:
    pass

@native                          # -> engine::core::init_scope
def init_scope() -> None: ...

score: Int32 = native_global()   # -> engine::core::score

@native("other::Thing")          # explicit override
class Thing:
    pass
```

`@export` is not allowed in `native_module` modules (they are declaration-only).

### `# tpy: cpp_namespace("ns")`

Overrides the C++ namespace for the module's generated code (default: `tpyapp::module_name`). In `native_module` modules, also serves as the auto-prefix for bare `@native` entities.

### `# tpy: include("header")`

Adds an `#include` directive. Quoted paths use `#include "..."`, angle-bracket paths use `#include <...>`. Optional platform filter.

```python
# tpy: include("mylib/types.h")
# tpy: include(<SDL2/SDL.h>)
# tpy: include(<sys/time.h>, platform="linux")
```

In `native_module` modules, includes are propagated to importing modules instead of going into a separate header. In regular modules, includes go into the module's own generated header.

### `# tpy: link("library")`

Passes `-l<name>` to the linker. Optional platform filter.

```python
# tpy: link("SDL2")
# tpy: link("ws2_32", platform="windows")
# tpy: link("pthread", platform="linux")
```

Link directives are collected from all modules in the dependency graph.

---

## Cross-module imports

Native entities imported in one module can be used in another via normal Python imports:

```python
# types.py (native_module)
# tpy: native_module
# tpy: cpp_namespace("game")
# tpy: include(<game/types.hpp>)

@native
class Player:
    name: str
    score: Int32
```

```python
# main.py (regular module)
from types import Player

def greet(p: Player) -> None:
    print(p.name)
```

The compiler propagates `<game/types.hpp>` to `main.hpp` so the C++ type is available.

---

## Symbol naming

All decorators accept an optional string argument for the C/C++ symbol name:

```python
@native("SDL_Init", binding="C")      # Python: sdl_init, C: SDL_Init
def sdl_init(flags: Int32) -> Int32: ...

@export("Helper_Add", binding="C")     # Python: helper_add, C: Helper_Add
def helper_add(x: Int32) -> Int32:
    return x + Int32(1)
```

When the string is omitted, the Python name is used. In `native_module` modules with `cpp_namespace`, bare `@native` names are auto-prefixed: `@native def foo` in a module with `cpp_namespace("ns")` -> `ns::foo`.

## Errors

| Condition | Error |
|-----------|-------|
| `@native` with real body | `must have '...' body` |
| `@export` with `...` body | `must have a body` |
| Both `@native` and `@export` on same function | `cannot have both @native and @export` |
| Same native symbol declared twice | `duplicate extern symbol 'name'` |
| Non-stub method on `@native` class | `methods on @native class must be stubs` |
| `native_global()` inside a function | `can only be used at module level` |
| `native_global()` without type annotation | `requires a type annotation` |
| `@export` in `native_module` | `@export not allowed in native_module` |

## Supported types

Native functions can use any type with a direct C++ mapping:

| TPy type | C/C++ type |
|----------|-----------|
| `Int8/16/32/64` | `int8_t/16/32/64_t` |
| `UInt8/16/32/64` | `uint8_t/16/32/64_t` |
| `float` / `Float64` | `double` |
| `Float32` | `float` |
| `bool` | `bool` |
| `str` | `std::string` (param: `std::string_view`) |
| `None` (return) | `void` |
| `Ptr[T]` | `T*` |
| `Ptr[readonly[T]]` | `const T*` |
| `Span[T]` | `std::span<T>` |

---

## Other decorators

These are orthogonal to the import/export system and remain unchanged:

| Decorator | Purpose |
|-----------|---------|
| `cpp_template("...")` | Inline C++ template expansion |
| `native_preserves_refs` | Marks native method as not invalidating iterators |
| `value_ptr_coercion` | Type coercion annotation |

---

## Future ideas

### C header generation

Generate a standalone C header from `@export(binding="C")` declarations:

```bash
tpyc --emit-c-header src/game.py -o game_api.h
```

### Auto-bindgen from C headers

Parse C headers and generate `@native` declarations automatically:

```bash
tpyc --bindgen /usr/include/SDL2/SDL.h -o sdl.py
```

### Callback function pointers

Pass TPy functions as C callbacks:

```python
@native("qsort", binding="C")
def qsort(base: Ptr[None], count: Int32, size: Int32,
           cmp: CCallback[[Ptr[readonly[None]], Ptr[readonly[None]]], Int32]) -> None: ...
```

### Native enums

Import C/C++ enum types with value mapping.

### Variadic C functions

Support for `printf`-style variadic calls (design TBD).

### String and buffer marshaling

Automatic conversion between TPy types and C types at the FFI boundary (e.g., `str` -> `const char*`, `list[T]` -> `T*, size_t`).
