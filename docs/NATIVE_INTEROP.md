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
| `@native(binding="C")` -- import C function | **Done** |
| `@native(binding="C")` -- import C struct | **Done** |
| `@native(cpp_return_type=T)` -- declare wider C++ return for narrowing cast | **Done** |
| `@native` class -- import C++ class (fields, stub methods) | **Done** |
| `@native("factory", function=True)` on `__init__` -- factory-style constructor | **Done** |
| `@native("MyArena", indirecting=True)` -- attest heap indirection for cycle detection | **Done** |
| `native_field("cpp_name")` -- per-field C++ rename on `@native` classes | **Done** |
| `@native` enum -- import C++ `enum class` | **Done** |
| `native_member("cpp_name")` -- per-member C++ rename on `@native` enums | **Done** |
| `@export(binding="C")` -- export TPy function | **Done** |
| `native_global()` -- import C/C++ global variable | **Done** |
| `# tpy: native_module` | **Done** |
| `# tpy: include("header")` | **Done** |
| `# tpy: include("header", platform="linux")` | **Done** |
| `# tpy: link("lib")` | **Done** |
| `# tpy: link("lib", platform="linux")` | **Done** |
| Cross-module native imports | **Done** |
| Package re-exports of native functions | **Done** |
| Include propagation from native modules | **Done** |
| Auto-prefix `@native` with `cpp_namespace` (bare or rename without `::`) | **Done** |
| Absolute opt-out via `@native("::name")` or `@native("ns::name")` | **Done** |
| Call-site `::` qualification for `@native` functions | **Done** |
| Duplicate symbol detection | **Done** |
| `@export(binding="C")` class -- export C struct | Planned |
| C header generation (`--emit-c-header`) | Planned |
| Callback function pointers | Open |
| Auto-bindgen from C headers | Open |
| Variadic C functions | Open |

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

Functions must have a `...` (stub) body. The optional string argument specifies the C/C++ symbol name. See [Rename resolution](#rename-resolution) below for how the string interacts with `cpp_namespace`.

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
    def zero() -> Own[Vec2]: ...        # factory: returns a fresh value (see below)
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

**Return convention -- `-> V` vs `-> Own[V]` (reference-type returns).** For a reference-type `V` (a class / container), the TPy return annotation declares the C++ return convention, and the compiler trusts it:

- `-> V` means the C++ method returns `V&` (a reference into the receiver or other stable storage). A call result bound to a local *aliases* that storage -- `p = obj.get()` binds `V*`/`V&`, and mutations through `p` reach the original (matching `dict.setdefault`, container `__getitem__`).
- `-> Own[V]` means the C++ method returns a fresh `V` by value (a factory / a moved-out value). The result is owned; binding it copies/moves, no aliasing.

This is the same contract user-defined methods follow, so there is no native special case. The consequence is a hard requirement on the binding author: a method whose C++ returns by value (e.g. a `static Vec2 zero()` factory) **must** be declared `-> Own[V]`. Declaring it bare `-> V` makes codegen bind a reference to a destroyed temporary -- a dangling pointer (or, for a `@nocopy` `V`, a C++ build error). There is currently no compiler check that a bare `-> V` native method actually returns `V&`; it is the author's contract to honor. (Free `@native` *functions* are presently the exception -- they always take value semantics regardless of the annotation; see BUGS.md.)

**Construction:**
- C++ classes: constructor call syntax -- `Vec2(1.0, 2.0)` -> `b2::Vec2(1.0, 2.0)`
- C structs (`binding="C"`): aggregate init -- `Rect(0, 0, 800, 600)` -> `SDL_Rect{0, 0, 800, 600}`

The class-level `@native(name)` is enough for both `MyClass(args)` (call form) and `e = MyClass(args)` (assignment form) -- both lower to `name(args)` directly. Annotating `__init__` with its own `@native` is only required when the constructor maps to a **different** C++ symbol than the class type itself (a factory function); see below.

**Factory-style constructors (`@native("factory", function=True)` on `__init__`).** When the Python class type doesn't have a directly-callable C++ constructor and instances are produced by a free-function factory, declare each constructor stub with its own `@native(..., function=True)`. The `function=True` flag tells codegen to emit a free-function call (`factory(args)`) instead of treating the name as a class type:

```python
# `bytes(...)` lowers to a factory call rather than vector construction
@native("std::vector<uint8_t>")
class bytes:
    @native("tpy::bytes_copy", function=True)
    def __init__(self, x: bytes) -> None: ...

    @native("tpy::bytes_from_size", function=True)
    def __init__(self, n: Int32) -> None: ...
```

Generated code: `bytes(other)` -> `tpy::bytes_copy(other)`, `bytes(10)` -> `tpy::bytes_from_size(10)`. Multiple `__init__` overloads each pick their own factory. Reach for this when the class's natural C++ constructor doesn't exist or doesn't match Python's call shape; otherwise the bare class-level `@native` is sufficient.

**Per-field rename (`native_field`).** When the external C/C++ field name differs from the Python name (e.g. C struct `sin_family`/`sin_port` conventions, or C++ `m_x` member-prefix conventions), use `native_field("cpp_name")` in the field's default-value slot:

```python
from tpy.extern import native, native_field

# C++ class: friendlier Python names over m_-prefixed C++ members
@native
class Vec2:
    x: Int32 = native_field("m_x")
    y: Int32 = native_field("m_y")

# C struct binding (e.g. under lib/tpy/_bindings/): expose POSIX field names
# under idiomatic Python names
@native("sockaddr_in", binding="C")
class SockAddrIn:
    family: UInt16 = native_field("sin_family")
    port:   UInt16 = native_field("sin_port")
```

Field reads and writes emit the renamed C/C++ member (`v.x` -> `v.m_x`, `a.port` -> `a.sin_port`). The rename is inherited: accessing the field through a TPy-level subclass of the `@native` class resolves it too (e.g. a user subclass of `OSError` reads `.errno` -> `error_number`); a subclass redeclaring the same field name shadows the rename and binds its own plain member (sema warns). Constructor calls are positional (aggregate init for C structs, constructor args for C++ classes) so the rename does not affect construction. `native_field` is rejected on non-`@native` classes and requires exactly one positional string literal argument.

### Global variables

```python
from tpy.extern import native_global

# C++ global
score: Int32 = native_global("engine::score")

# C global
frame_count: Int32 = native_global("DG_FrameCount", binding="C")

# C global array (decays to pointer)
data: Ptr[Int32] = native_global("shared_data", binding="C", array=True)

# Bare -- uses Python name as the global C++ symbol
# (native globals emit at :: regardless of the module's cpp_namespace,
# matching C ABI conventions for externally-linked variables)
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

### Enums

```python
# tpy: include("native_types.hpp")
from enum import Enum, auto
from tpy.extern import native, native_member


@native("cfg::Mode")
class Mode(Enum):
    NONE_MODE = native_member("None")  # C++ side has `None` (Python keyword)
    AUTO = native_member("Auto")
    MANUAL = native_member("Manual")


@native("ns::E")
class E(Enum):
    A = auto()                          # C++ name = TPy name (`A`)
    B = auto()


@native("ns::Tag")
class Tag(Enum):
    Alpha = 100                         # explicit values are verified
    Beta = 200                          # against the C++ side via
    Gamma = 300                         # per-member static_assert
```

`@native` enums bind to an existing C++ `enum class`; no enum declaration is generated and no `operator<<` is emitted (avoids conflict with user-provided one). The user's `# tpy: include(...)` directive must make the C++ enum type visible.

**Two value-declaration modes.** The TPy-declared value is either *implicit* (via `auto()` or `native_member()`) -- in which case the C++ side is the source of truth and `e.value` reads `static_cast<underlying>(e)` directly -- or *explicit*, in which case codegen emits a per-member `static_assert` pinning the TPy-declared value to the C++ side. A mismatch fails at C++ compile time with a clear "does not match" message rather than silently miscompiling. Use explicit values when you want to document the binding's expected values in TPy source; use `auto()` when you don't want to mirror them.

**`native_member("cpp_name")` aliases the TPy-side member name** when the C++ enumerator is a Python keyword (`None`, `True`, `False`), a TPy keyword, or follows a different naming convention. Member access (`Mode.NONE_MODE`) emits the C++ enumerator (`::cfg::Mode::None`); reflection (`m.name`, `Mode["NONE_MODE"]`) keeps the TPy-side name. Only valid inside `@native` enum bodies; takes a single positional string literal.

**Iteration via reflection.** `match`, `e.name`, `e.value`, `Mode(v)`, and `Mode["MEMBER"]` all work across the binding boundary via `tpy::EnumUtil<E>` generated alongside each `@native` enum.

**Restrictions.**
- Only `@native` is allowed on enum classes; other decorators and any `@native` kwargs (`binding=`, `function=`, `cpp_return_type=`) are rejected.
- `auto()` is idiomatic; `native_member("cpp_name")` is also implicit. Explicit integer values are accepted and are verified against the C++ side via a per-member `static_assert` in the generated `.cpp`. Mixing `auto()`/`native_member()` with explicit integers in the same body is rejected.
- Nested `@native` enums (inside a class body) are not supported. Declare them at module top level using the fully-qualified C++ name (e.g. `@native("ns::Container::Kind") class Kind(Enum): ...`) -- the qname encodes the C++ nesting, so TPy structure does not need to mirror C++ structure.
- `IntEnum` with an explicit mixin (`class E(Int8, Enum):`) selects the underlying integer type; it must match the C++ side's underlying type.
- Multiple TPy modules binding to the same C++ enum produce duplicate `EnumUtil<E>` definitions at link time (same constraint as duplicate `@native` records). Declare each binding in one TPy module and import from there.

### Generic classes

```python
@native("std::vector")
class StdVector(Generic[T]):
    def push_back(self, val: T) -> None: ...
    def size(self) -> Int32: ...
```

### Declaring heap indirection: `indirecting=True`

Cycle detection in recursive type aliases (`type Tree = Lit | Box[Tree]`) needs to know whether a wrapper type contains its element by value (would form an infinite-size cycle) or via pointer indirection (breaks the cycle). For TPy records the compiler infers this structurally by walking the record's fields -- a `_ptr: Ptr[T]` field is recognized as indirecting without any annotation. For `@native` records whose internal storage is opaque to TPy (no fields declared, or fields that hide the indirection in a C++ template), declare the kwarg:

```python
@native("my::Arena", indirecting=True)
class Arena[T]:  # C++ stores T behind a unique_ptr / arena handle
    ...
```

With this, `type Tree = Leaf | Arena[Tree]` compiles. Without it, the compiler treats `Arena[T]` as a by-value container and rejects the alias.

Used in the stdlib by `list`/`dict`/`set` (see `lib/tpy/tpy/_builtins/_{list,dict,set}.py`). Don't reach for it on records whose TPy field declarations already expose a `Ptr`-typed (or other indirecting) field -- the structural walk handles those.

### Narrowing C++ returns: `cpp_return_type=T`

`@native` declares an exact-match binding to a C++ symbol -- the TPy signature must match the C++ side. When the C++ side returns a wider type than the TPy declared return (e.g. `std::vector::capacity()` returns `size_t`, but the user wants an `Int32` view), use `cpp_return_type=T` to tell codegen the underlying type:

```python
@native("std::vector")
class Vec[T]:
    @property
    @native("capacity", cpp_return_type=UInt64)  # capacity() returns size_t
    def cap(self) -> Int32: ...

# Codegen emits:  static_cast<int32_t>(v.capacity())
```

Without `cpp_return_type`, the implicit narrowing would trip `-Wconversion` / `-Wsign-conversion` at the use site. With it, codegen wraps the call in `static_cast<DECLARED_TPY_RETURN>(...)` so the conversion is explicit. Works on both methods and free functions. The annotated value is a TPy type name (e.g. `UInt64`); the cast target is always the declared TPy return type.

For more involved transformations (computed expressions, multi-step conversions), use `@cpp_template` instead -- it gives full control over the emitted call expression.

### Inline C++ templates: `@cpp_template`

`@cpp_template("...")` (from `tpy.extern`) replaces a call with an inline C++ expression. The template body substitutes placeholders:

- `{self}` -- the receiver (methods only; an error in a free function)
- `{0}`, `{1}`, ... -- positional arguments
- `{cpp}` -- the C++ spelling of the (substituted) return type
- `{T}` / type-param names -- substituted with the inferred type argument

```python
@cpp_template("std::rotl<uint32_t>({0}, {1})")
def rotl32(x: UInt32, n: Int32) -> UInt32: ...
```

To emit a **literal** C++ brace (aggregate-init, a lambda body, a scope, a GCC statement-expression), double it -- `{{` -> `{` and `}}` -> `}`, matching Python's `str.format` convention:

```python
# Emits: []() { return a + b; }()
@cpp_template("[]() {{ return {0} + {1}; }}()")
def lambda_sum(a: Int32, b: Int32) -> Int32: ...
```

A lone unescaped brace (or an out-of-range `{N}`) is a compile-time diagnostic, not an internal error.

**A runtime-value placeholder (`{self}` or `{N}`) may appear at most once.** Substitution is a textual paste with no evaluate-once binding, so a repeated value placeholder would evaluate its argument twice -- side effects run twice, and a repeated side-effecting receiver makes `std::stable_sort({self}.begin(), {self}.end())` undefined (the two evaluations can view different containers). The compiler rejects this at parse time. Type placeholders (`{cpp}`, `{T}`) are inert and may repeat freely. When you need an evaluated argument more than once, bind a typed C++ helper with `@native` -- it evaluates each argument once by construction.

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

See [Rename resolution](#rename-resolution) below for how `@native` renames interact with `cpp_namespace`.

`@export` is not allowed in `native_module` modules (they are declaration-only).

### `# tpy: cpp_namespace("ns")`

Overrides the C++ namespace for the module's generated code (default: `tpyapp::module_name`). Also drives the [rename resolution](#rename-resolution) rule for `@native` entities in the module.

### `# tpy: include("header")`

Adds an `#include` directive. Quoted paths use `#include "..."`, angle-bracket paths use `#include <...>`. Optional platform filter.

```python
# tpy: include("mylib/types.h")
# tpy: include(<SDL2/SDL.h>)
# tpy: include(<sys/time.h>, platform="linux")
```

In `native_module` modules, includes propagate transitively into any consumer whose generated C++ reaches a type defined in this module -- whether named explicitly in the consumer's imports or surfaced only through field/method chains on imported types. Native-to-native chains follow the same rule: a hand-written native header that forward-declares a type from another native module does not need to be edited; the consumer header pulls in both. In regular modules, includes go into the module's own generated header.

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

### Rename resolution

The rename string interacts with the module's `cpp_namespace` directive (if any). The rule covers both the no-rename case (`@native`) and the explicit-rename case (`@native("name")`), and applies to module-level `@native` functions, records, and protocols. C-linkage (`binding="C"`) and `@builtin_type` records are exempt -- their symbol names are unchanged by this rule.

| Form | Interpretation | Emitted C++ (in `cpp_namespace("mylib")`) |
|------|----------------|-------------------------------------------|
| `@native` (no rename) | relative to `cpp_namespace` | `::mylib::PythonName` |
| `@native("foo")` (no `::` in string) | relative to `cpp_namespace` | `::mylib::foo` |
| `@native("a::b")` (contains `::`) | absolute -- `cpp_namespace` ignored | `::a::b` |
| `@native("::foo")` (leading `::`) | absolute to global scope | `::foo` |

**Rule of thumb:** any `::` in the rename string opts out of the namespace prefix. Use `@native("::libc_name")` to bind to libc or other global-scope symbols from inside a namespaced module (e.g. `@native("::socket")` in a `cpp_namespace("tpystd::_bindings::posix_socket")` module).

```python
# tpy: cpp_namespace("engine::core")
# tpy: include("<engine/core/types.hpp>")

from tpy.extern import native

@native                          # -> ::engine::core::Session
class Session: ...

@native                          # -> ::engine::core::init_scope
def init_scope() -> None: ...

@native("init_scope_v2")         # -> ::engine::core::init_scope_v2 (relative)
def init_scope2() -> None: ...

@native("other::Thing")          # -> ::other::Thing (absolute, contains ::)
class Thing: ...

@native("::global_helper")       # -> ::global_helper (absolute, leading ::)
def global_helper() -> None: ...
```

### Call-site qualification

All `@native` function calls (C++ linkage) emit their symbol absolute-qualified -- codegen prepends `::` at every call site. This prevents C++ unqualified lookup from binding to a namespace member, class method, or ADL hit before finding the intended external symbol. C-linkage `@native(binding="C")` and `@export(binding="C")` are exempt: their declarations are namespace-scoped `extern "C"`, so the prefix would force global-scope lookup and miss the declaration.

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

**Fixed-width arguments from unbounded values:** converting a BigInt (or any
wider value) into a fixed-width native argument (`Int64(x)`, `Int32(x)`, ...)
panics uncatchably when out of range. Stdlib code calling `@native` functions
must range-check the value and raise the appropriate catchable exception
BEFORE the conversion -- the same validate-in-BigInt-first discipline used
for record field stores applies to native-call arguments (see the timestamp
guard in `lib/tpy/datetime.py::_from_epoch_us` for the pattern; the missing
guard there was a review-caught uncatchable-panic bug).

---

## Other decorators

These are orthogonal to the import/export system and remain unchanged:

| Decorator | Purpose |
|-----------|---------|
| `cpp_template("...")` | Inline C++ template expansion |
| `native_preserves_refs` | Marks native method as not invalidating iterators |
| `copy_returns_warn` | Marks an `Own[V]` accessor that copies where its CPython namesake aliases; sema warns at call sites (silence with `copy()`) |
| `value_ptr_coercion` | Type coercion annotation |
| `virtual_raise` | Class marker: its hand-written C++ `__raise__` dispatches (is not `throw *this`), so `raise X(args)` routes through it instead of the fresh-throw peephole. Not inherited. Used by `OSError`'s errno -> subclass mapping |

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
