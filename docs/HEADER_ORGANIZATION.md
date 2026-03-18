# C++ Header Organization Design

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 0** | Single-header scheme (current); template functions in `.hpp` | Done |
| **Phase 1** | 4-header split (`_fwd.hpp`, `.hpp`, `_templ.hpp`, `_impl.hpp`) with conservative includes | Planned |
| **Phase 2** | Usage classification pre-pass for minimum include levels per dependency | Planned |
| **Phase 3** | Inline function heuristics for small non-template functions | Planned |

## Problem

TurboPython compiles each `.py` module to a `.hpp`/`.cpp` pair. Currently,
a module's `.hpp` contains everything: forward declarations, full record
definitions (including template records with all method bodies inline),
function declarations, and template function definitions.

When module A imports module B, it includes B's entire `.hpp` -- even if A
only uses a single non-generic function. At scale (~100kloc, hundreds of
modules), this causes unnecessary parse work in every translation unit that
transitively depends on B.

## Goals

1. **Minimize per-TU parse cost** -- each translation unit should only include
   what it actually needs from its dependencies.
2. **Keep test snapshots manageable** -- provide an option to emit a single
   header per module (current behavior) so test expected output does not bloat.

## File Scheme

Each module produces up to 5 files. The compiler omits files that would be
empty (e.g., `_templ.hpp` for a module with no template types).

### `module_fwd.hpp` -- Forward Declarations

Minimal file containing only forward declarations. Enough for pointers,
references, and incomplete-type contexts.

```cpp
#pragma once

namespace tpyapp::container {
struct Point;
template<typename T> struct Box;
} // namespace tpyapp::container
```

**Include this when:** a dependency's types appear only as pointer/reference
types (`Ptr[T]`, `Ptr[readonly[T]]`, function parameters passed by reference).

### `module.hpp` -- Main Header

Non-template record definitions (full structs with inline methods), function
declarations, small `inline` function definitions, extern declarations for
globals.

```cpp
#pragma once
#include "module_fwd.hpp"

namespace tpyapp::container {

struct Point {
  int32_t x;
  int32_t y;
  Point() = default;
  Point(int32_t x, int32_t y) : x(x), y(y) {}
};

// Non-generic function declaration
Point add_points(const Point& a, const Point& b);

// Small inline function -- body is trivial, all deps resolved
inline int32_t point_sum(const Point& p) { return p.x + p.y; }

extern std::string_view __name__;

void __tpy_init();
} // namespace tpyapp::container
```

**Include this when:** a dependency's non-template types are used by value
(record fields, local variables, return types), or its non-generic functions
are called.

### `module_templ.hpp` -- Template Type Layout

Template record definitions with fields and method **declarations** (no
bodies). Template function **declarations** (signature only). This provides
enough information for the C++ compiler to compute `sizeof(T)` and lay out
structs that embed template types, without paying the cost of parsing method
bodies.

```cpp
#pragma once
#include "module.hpp"

namespace tpyapp::container {

template<typename T>
struct Box {
  T value;

  // Method declarations only -- bodies in _impl.hpp
  Box() = default;
  explicit Box(const T& value);
  T get() const;
};

// Template function declaration only
template<typename T>
T identity(const T& x);

} // namespace tpyapp::container
```

**Include this when:** a dependency's template types are embedded by value in
a record field or used as a value-type local variable, but their methods are
not called directly in this context (e.g., only layout is needed).

### `module_impl.hpp` -- Template Bodies

Template method **definitions** and template function **definitions**. This is
the heaviest header -- only included by translation units that actually
instantiate templates.

```cpp
#pragma once
#include "module_templ.hpp"

namespace tpyapp::container {

template<typename T>
Box<T>::Box(const T& value) : value(value) {}

template<typename T>
T Box<T>::get() const { return value; }

template<typename T>
T identity(const T& x) { return x; }

} // namespace tpyapp::container
```

**Include this when:** a dependency's template functions are called, template
type methods are called, or template types are constructed.

### `module.cpp` -- Source

Non-template function definitions, global variable definitions, module
initialization, `main()` (entry point only).

```cpp
#include "module_impl.hpp"  // gets all headers transitively

namespace tpyapp::container {

std::string_view __name__;

Point add_points(const Point& a, const Point& b) {
  return Point(a.x + b.x, a.y + b.y);
}

void __tpy_init() {
  static bool initialized = false;
  if (initialized) return;
  initialized = true;
  __name__ = "container";
}
} // namespace tpyapp::container
```

## Include Hierarchy

Each file includes the previous level, forming a chain:

```
module_fwd.hpp  <--  module.hpp  <--  module_templ.hpp  <--  module_impl.hpp
                                                                    ^
                                                                    |
                                                                module.cpp
```

Including any file automatically pulls in all lighter levels via the chain.

## Per-Dependency Include Level Selection

When module A imports from module B, the compiler determines the minimum
include level needed **separately** for A's header and A's source file.

### Header context (what A's `.hpp` needs from B)

| Usage in A's header | Minimum level |
|----------------------|---------------|
| B's type as `Ptr[X]`, `Ptr[readonly[X]]` | `_fwd.hpp` |
| B's type as function parameter (passed by ref) | `_fwd.hpp` |
| B's non-template type as record field | `.hpp` |
| B's non-template type as function return (by value) | `.hpp` |
| B's template type as record field (e.g., `f: Box[int]`) | `_templ.hpp` |
| B's template type as function return (by value) | `_templ.hpp` |
| A re-exports B's inline function | `.hpp` |
| A re-exports B's template function | `_impl.hpp` |

### Source context (what A's `.cpp` needs from B)

| Usage in A's function bodies | Minimum level |
|------------------------------|---------------|
| Call B's non-generic function | `.hpp` |
| Access field of B's non-template record | `.hpp` |
| Call B's generic function | `_impl.hpp` |
| Call method on B's template type | `_impl.hpp` |
| Construct B's template type | `_impl.hpp` |
| Use B's type only by pointer/ref | `_fwd.hpp` |

The compiler picks `max(header_need, source_need)` per dependency per output
file (A's `.hpp` and A's `.cpp` may include B at different levels).

### Feasibility

The semantic analyzer already tracks all imported symbols, their types, and
whether they are generic. The codegen phase can add a **usage classification
pre-pass** that walks each module's AST and, for each dependency, records:

- Whether its types appear in record fields (needs layout)
- Whether its types appear in function signatures (usually ref -- fwd suffices)
- Whether its generic functions/methods are called (needs template bodies)
- Whether its non-generic functions are called (needs declarations)

Conservative fallback: when in doubt, include at a higher level. Correctness
over optimality -- over-including is safe, under-including causes compile
errors that are easy to diagnose and fix.

## Inline Function Heuristics

Small non-template functions are emitted `inline` in `module.hpp` rather than
as declarations-only with bodies in `module.cpp`. This enables cross-TU
inlining without relying on LTO, which may not optimize all cases equally.

Criteria for inlining (tentative):

- Function body is small (e.g., <= 3 statements)
- No complex control flow (no loops, no recursion)
- All dependencies are resolved in the header context (only uses types/functions
  already visible from included headers and the runtime)

Functions that do not meet these criteria remain as declarations in `.hpp`
with definitions in `.cpp`.

## Template Class Members

For **template records**, method bodies are separated from the struct
definition:

- `_templ.hpp` contains the struct with fields and method **declarations**
  (enough for layout computation and type checking)
- `_impl.hpp` contains out-of-line method **definitions** using
  `ClassName<T>::method()` syntax

For **non-template records**, methods remain inline in the struct definition
in `module.hpp` (the current behavior). Moving large non-template methods to
`.cpp` is a potential future optimization but not part of this design.

## Single-Header Mode for Testing

The test suite uses snapshot-based expected output. Splitting every module
into 4 headers would bloat test cases and make them harder to review.

A compiler flag (e.g., `--single-header` or an internal test mode) preserves
current behavior: one `.hpp` and one `.cpp` per module, with all declarations
and template definitions in the `.hpp`. This is the default for test snapshot
generation.

The multi-header mode is used for production builds where compilation speed
matters.

## Migration Path

1. **Phase 0 (now):** Fix the cross-module generic function bug by moving
   template function definitions from `.cpp` to `.hpp` within the current
   single-header scheme.

2. **Phase 1:** Implement the 4-header split for codegen. Initially, use a
   conservative include strategy (always include at `_impl.hpp` level) to
   ensure correctness, then refine.

3. **Phase 2:** Implement the usage classification pre-pass to select minimum
   include levels per dependency.

4. **Phase 3:** Implement inline function heuristics for small non-template
   functions.

## Notes

- Files that would be empty are not generated (e.g., a module with no
  templates skips `_templ.hpp` and `_impl.hpp`).
- `tpy.hpp` (the runtime header) is included by every module. GCC handles
  precompiled headers well, so this is not a concern for us.
- C++20 modules would be the ideal long-term solution but adoption is too low
  to rely on currently.
