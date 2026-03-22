# Compile-Time Module Parameters — Design

## Progress

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | CPython prototype: `compile_param()`, `instantiate()`, sub-module propagation, factory registry | Done (POC) |
| 1 | `compile_param()` recognition in sema, `constexpr` codegen, `bool` params | Planned |
| 2 | `instantiate()` support: compiler generates separate TU per param combination | Planned |
| 3 | Sub-module propagation: dependency graph duplicated per instance | Planned |
| 4 | `Int32`/`Int64` compile params | Planned |
| 5 | Auto-registration: `@register` + static initializer codegen | Planned |

### Future Extensions

| Feature | Notes |
|---------|-------|
| `str` literal params | `constexpr std::string_view` -- backend selection, platform tags |
| Enum literal params | `constexpr EnumType` -- mode flags |
| Compile-time expressions | `if HIGH_PRECISION:` branch elimination guaranteed (not just optimizer-dependent) via `if constexpr` |
| Dead code stripping | Sema skips analysis of dead branches, codegen omits them entirely |
| Config-driven instantiation | `tpyc.toml` or `options.json` declares instances, no `instantiate()` call needed in source |

---

## Overview

`compile_param()` declares a module-level constant whose value is provided at
instantiation time. A single Python source file is compiled into multiple C++
translation units, each with a different `constexpr` value. This enables
write-once logic with zero-overhead specialization -- no runtime branches on
the hot path.

### Motivating example: physics solver

A game simulation needs different physics solvers running in the same frame --
high-precision for gameplay-critical bodies (player, projectiles), low-precision
for visual effects (debris, particles). The logic is the same, but precision
and capacity differ:

```python
# engine/solver/__init__.py
from tpy import compile_param, Int32

HIGH_PRECISION: bool = compile_param("HIGH_PRECISION")
MAX_BODIES: Int32 = compile_param("MAX_BODIES")

class Solver:
    def __init__(self):
        self.positions = Array[float, MAX_BODIES]()

    def step(self, dt: float) -> None:
        if HIGH_PRECISION:
            self.integrate_rk4(dt)   # 4th-order Runge-Kutta
        else:
            self.integrate_euler(dt) # simple Euler, good enough for debris
```

The caller instantiates multiple specialized versions:

```python
# main.py
from tpy import instantiate

# Critical bodies: high precision, small count
critical = instantiate("engine.solver", HIGH_PRECISION=True, MAX_BODIES=64)

# Particles/debris: low precision, large count
particles = instantiate("engine.solver", HIGH_PRECISION=False, MAX_BODIES=4096)

# Background/ambient: low precision, medium count
ambient = instantiate("engine.solver", HIGH_PRECISION=False, MAX_BODIES=256)

critical_solver = critical.Solver()
particle_solver = particles.Solver()
ambient_solver = ambient.Solver()
```

All three versions share the same Python source. In CPython, `instantiate()`
creates independent module objects with different param values. In tpyc, it
generates three C++ translation units -- each with `constexpr` values, dead
branches eliminated, and `Array` sizes fixed at compile time.

---

## Design Principles

1. **One source, N binaries.** The parameterized module is written and maintained
   once. The compiler (or CPython runtime) handles duplication.

2. **CPython compatible.** `compile_param()` and `instantiate()` are regular
   Python functions. The same source runs in CPython (with runtime branching)
   and tpyc (with compile-time specialization). No syntax extensions.

3. **Explicit over magic.** Parameters are declared by name
   (`compile_param("HIGH_PRECISION")`), instances are created explicitly
   (`instantiate(..., HIGH_PRECISION=True)`). No implicit specialization.

4. **Sub-module propagation.** When a parameterized module imports sub-modules
   that also use `compile_param()`, each instance gets its own copy of the
   entire sub-module tree. Parameters flow down automatically.

5. **Zero overhead in compiled output.** Compile params become `constexpr`.
   Branches on them are eliminated at compile time. Integer params can be used
   as template arguments (e.g. `Array[T, N]`). The generated code is identical
   to hand-written specialized versions.

---

## `compile_param()` — Parameter Declaration

### Syntax

```python
from tpy import compile_param, Int32

HIGH_PRECISION: bool = compile_param("HIGH_PRECISION")
MAX_BODIES: Int32 = compile_param("MAX_BODIES")
```

The string argument must match the variable name. This is redundant for the
compiler (which knows the assignment target) but serves as:
- Self-documentation in the source
- Validation (compiler error if string doesn't match variable name)
- CPython runtime lookup key

### Constraints

- Must appear at module level (not inside functions or classes)
- Must be assigned to a simple name (not `self.x`, not `a, b = ...`)
- The type annotation is required (compiler uses it to validate `instantiate()`)
- The variable is immutable after initialization (reassignment is a compile error)

### Supported types

| Type | C++ | Phase | Notes |
|------|-----|-------|-------|
| `bool` | `constexpr bool` | 1 | Branch elimination, feature flags |
| `Int32` | `constexpr int32_t` | 4 | Array sizes, buffer capacity, iteration bounds |
| `Int64` | `constexpr int64_t` | 4 | Large sizes |

### Future types

| Type | C++ | Notes |
|------|-----|-------|
| `str` literal | `constexpr std::string_view` | Backend selection, platform tags |
| Enum literal | `constexpr EnumType` | Mode selection |

### Integer params as template arguments

Integer compile params can be used wherever a compile-time integer is expected,
most notably as `Array` size parameters:

```python
MAX_BODIES: Int32 = compile_param("MAX_BODIES")

class Solver:
    def __init__(self):
        self.positions = Array[float, MAX_BODIES]()
        self.velocities = Array[float, MAX_BODIES]()
```

In C++ this becomes `std::array<double, MAX_BODIES>` where `MAX_BODIES` is
`constexpr` -- the array is stack-allocated with a fixed size known at compile
time. Each instantiation gets a different size.

---

## `instantiate()` — Module Instantiation

### Syntax

```python
from tpy import instantiate

critical = instantiate("engine.solver", HIGH_PRECISION=True, MAX_BODIES=64)
particles = instantiate("engine.solver", HIGH_PRECISION=False, MAX_BODIES=4096)
```

`instantiate()` takes a dotted module path (string) and keyword arguments
matching the module's `compile_param()` declarations. It returns a module
object.

### Compiler behavior (tpyc)

When the compiler encounters `instantiate()`:

1. Resolve the target module path
2. Verify all `compile_param()` declarations are satisfied (correct names, types)
3. Compile the target module (and its sub-module tree) with the given param
   values substituted as `constexpr` constants
4. Generate a unique namespace for each instance (see Codegen section)
5. Bind the result to the local variable (`critical`)

Member access on the result (`critical.Solver`) resolves to the specialized
namespace.

### CPython behavior

`instantiate()` re-executes the module source in a fresh module object with
`compile_param()` calls returning the provided values. Sub-module imports within
the package are intercepted via `sys.meta_path` and also re-executed fresh.

The original module in `sys.modules` is not affected -- each `instantiate()`
call produces an independent copy.

### Validation

| Condition | Error |
|-----------|-------|
| Unknown param name | `error: 'FOO' is not a compile_param of 'engine.solver'` |
| Missing param | `error: compile_param 'MAX_BODIES' not provided in instantiate()` |
| Wrong type | `error: compile_param 'HIGH_PRECISION' expects bool, got Int32` |
| Direct import of parameterized module | `error: module 'engine.solver' has compile_params; use instantiate()` |

---

## Sub-Module Propagation

When a parameterized module imports sub-modules that also declare
`compile_param()`, the parameter values propagate automatically.

```
engine/solver/
    __init__.py          # HIGH_PRECISION, MAX_BODIES
    collision.py         # HIGH_PRECISION, MAX_BODIES
    integrator.py        # HIGH_PRECISION
```

A single `instantiate("engine.solver", HIGH_PRECISION=True, MAX_BODIES=64)`
creates independent copies of all three modules, each with the matching
param values.

### Rules

- Sub-modules may declare a subset of the parent's params (not all sub-modules
  need every param)
- A sub-module cannot declare params that the parent doesn't declare (the
  instantiation site controls the param set)
- Sub-modules outside the parameterized package are imported normally (shared,
  not duplicated)

### How it works in tpyc

The compiler already traces module dependency graphs for multi-module
compilation. For parameterized modules, the dependency graph is duplicated per
instance:

```
instantiate("engine.solver", HIGH_PRECISION=True, MAX_BODIES=64)
  -> solver/__init__.py     [HIGH_PRECISION=true, MAX_BODIES=64]
     -> solver/collision.py  [HIGH_PRECISION=true, MAX_BODIES=64]
     -> solver/integrator.py [HIGH_PRECISION=true]
     -> engine.math (not parameterized -- shared, compiled once)

instantiate("engine.solver", HIGH_PRECISION=False, MAX_BODIES=4096)
  -> solver/__init__.py     [HIGH_PRECISION=false, MAX_BODIES=4096]
     -> solver/collision.py  [HIGH_PRECISION=false, MAX_BODIES=4096]
     -> solver/integrator.py [HIGH_PRECISION=false]
     -> engine.math (same shared instance)
```

### How it works in CPython

`instantiate()` installs a temporary `sys.meta_path` finder that intercepts
imports within the parameterized package prefix. Each intercepted import loads
the source fresh and executes it with the current param context. Imports outside
the package fall through to normal import machinery.

After `instantiate()` returns, the finder is removed and `sys.modules` is
restored to its original state.

---

## Auto-Registration and Factory Dispatch

Parameterized modules can register themselves into a protocol-based factory.
Each instance registers under a unique name derived from its param values.

```python
# engine/solver/__init__.py
from tpy import compile_param, Int32
from engine.registry import register_solver

HIGH_PRECISION: bool = compile_param("HIGH_PRECISION")
MAX_BODIES: Int32 = compile_param("MAX_BODIES")

_prec_tag = "hifi" if HIGH_PRECISION else "fast"

@register_solver(f"solver_{_prec_tag}_{MAX_BODIES}")
class Solver:
    def step(self, dt: float) -> None:
        if HIGH_PRECISION:
            self.integrate_rk4(dt)
        else:
            self.integrate_euler(dt)
```

```python
# main.py
from tpy import instantiate
from engine.registry import create_solver

# Instantiate variants -- each auto-registers
instantiate("engine.solver", HIGH_PRECISION=True, MAX_BODIES=64)
instantiate("engine.solver", HIGH_PRECISION=False, MAX_BODIES=4096)
instantiate("engine.solver", HIGH_PRECISION=False, MAX_BODIES=256)

# Config-driven dispatch
critical_solver = create_solver("solver_hifi_64")
particle_solver = create_solver("solver_fast_4096")
```

### tpyc codegen for registration

The `@register_solver(name)` decorator compiles to a static initializer in the
generated TU. Each instance's TU has its own static init that runs before
`main()`:

```cpp
// solver_HP_true_MB_64.cpp
namespace engine::solver_HP_true_MB_64 {
    constexpr bool HIGH_PRECISION = true;
    constexpr int32_t MAX_BODIES = 64;
    // ... class Solver ...

    // Static registration (runs before main)
    static auto _reg = engine::registry::register_solver(
        "solver_hifi_64",
        []() -> std::unique_ptr<SolverBase> {
            return std::make_unique<Solver>();
        }
    );
}
```

### CPython behavior

Registration works identically -- the `@register_solver` decorator is a
normal Python decorator that adds to a global dict. Each `instantiate()` call
executes the module, which calls the decorator, which registers.

---

## C++ Code Generation

### Namespace naming

Each (module, params) combination gets a unique C++ namespace. The naming
scheme appends param values to the module namespace:

| Instance | C++ namespace |
|----------|---------------|
| `solver(HIGH_PRECISION=True, MAX_BODIES=64)` | `engine::solver_HP_true_MB_64` |
| `solver(HIGH_PRECISION=False, MAX_BODIES=4096)` | `engine::solver_HP_false_MB_4096` |
| `solver(HIGH_PRECISION=False, MAX_BODIES=256)` | `engine::solver_HP_false_MB_256` |

### Generated code structure

```cpp
// Generated: solver_HP_true_MB_64.hpp
#pragma once
namespace engine::solver_HP_true_MB_64 {

constexpr bool HIGH_PRECISION = true;
constexpr int32_t MAX_BODIES = 64;

class Solver {
public:
    std::array<double, MAX_BODIES> positions;
    std::array<double, MAX_BODIES> velocities;
    void step(double dt);
};

} // namespace engine::solver_HP_true_MB_64
```

```cpp
// Generated: solver_HP_true_MB_64.cpp
#include "solver_HP_true_MB_64.hpp"
namespace engine::solver_HP_true_MB_64 {

void Solver::step(double dt) {
    // With HIGH_PRECISION = true, the optimizer eliminates the else branch.
    // MAX_BODIES = 64 allows the compiler to unroll loops optimally.
    if (HIGH_PRECISION) {
        integrate_rk4(dt);
    } else {
        integrate_euler(dt);
    }
}

} // namespace engine::solver_HP_true_MB_64
```

The C++ optimizer (with `-O1` or higher) eliminates dead branches on
`constexpr bool`. No `if constexpr` is needed -- plain `if` on a `constexpr`
condition is sufficient for branch elimination. `if constexpr` could be used
in a future phase for guaranteed dead code stripping at the sema/codegen level.

Integer params as `constexpr` also let the C++ compiler optimize loops (unroll
for small counts, vectorize for large counts) and stack-allocate fixed-size
arrays.

### Binding at the instantiation site

```cpp
// Generated: main.cpp
namespace critical  = engine::solver_HP_true_MB_64;
namespace particles = engine::solver_HP_false_MB_4096;
namespace ambient   = engine::solver_HP_false_MB_256;

int main() {
    auto critical_solver  = critical::Solver();
    auto particle_solver  = particles::Solver();
    auto ambient_solver   = ambient::Solver();
    // ...
}
```

---

## CPython Runtime Implementation

The CPython implementation lives in `lib/cpy/tpy/` alongside other CPython
stubs. It provides `compile_param()` and `instantiate()` as regular functions.

### `compile_param(name)`

Uses thread-local storage to read the current instantiation context:

```python
_context = threading.local()

def compile_param(name: str):
    params = getattr(_context, "params", None)
    if params is None:
        raise RuntimeError(
            f"compile_param('{name}') called outside instantiation context. "
            f"Use instantiate() to create instances of this module."
        )
    return params[name]
```

### `instantiate(module_path, **params)`

1. Template phase: import the module with a sentinel context (allows
   `compile_param()` to return `None`) to discover source files
2. Save and clear `sys.modules` entries for the package
3. Install a `sys.meta_path` finder for sub-module interception
4. Set `_context.params` to the provided values
5. Re-execute the module source in a fresh `types.ModuleType`
6. Remove the finder, restore `sys.modules`
7. Return the fresh module

The original module is fully restored in `sys.modules` after each call,
so multiple `instantiate()` calls produce independent copies.

---

## Interaction with Other Features

### `@noalloc`

Compile params and `@noalloc` are orthogonal. A parameterized module can use
`@noalloc` on its functions -- each generated TU inherits the annotation.
A solver with `@noalloc` on `step()` and `MAX_BODIES` as a compile param
produces a fully stack-allocated, branch-free hot path.

### Generics

Compile params operate at the module level; generics operate at the
type/function level. They compose naturally:

```python
HIGH_PRECISION: bool = compile_param("HIGH_PRECISION")

class Solver(Generic[T]):
    def integrate(self, bodies: Span[T], dt: float) -> None:
        if HIGH_PRECISION:
            self.rk4(bodies, dt)
        else:
            self.euler(bodies, dt)
```

Each instance gets its own monomorphized generic -- the two mechanisms
are independent.

### Multi-module compilation

The existing `compiler.py` orchestrates module discovery and dependency
resolution. Compile params extend this: when `instantiate()` is encountered,
the target module tree is added to the compilation plan N times (once per
param combination), each producing a separate set of TUs.

---

## Implementation Plan

### Phase 1: `compile_param()` in sema + codegen (bool)

**Scope:** Recognize `compile_param("NAME")` in semantic analysis. Generate
`constexpr` declaration in C++. Single param, `bool` only. No `instantiate()`
yet -- value provided via CLI or `options.json`.

**Key changes:**

| File | Change |
|------|--------|
| `tpyc/modules/tpy.py` | Register `compile_param` as a built-in function |
| `tpyc/sema/expressions.py` | Recognize `compile_param()` call, record param in module context |
| `tpyc/sema/context.py` | Store declared compile params and their bound values |
| `tpyc/codegen_cpp/generator.py` | Emit `constexpr` declaration for each compile param |
| `lib/cpy/tpy/__init__.py` | CPython stub for `compile_param()` |

### Phase 2: `instantiate()` support

**Scope:** Recognize `instantiate()` calls. Compile the target module tree
multiple times with different param values. Generate separate TU sets.

**Key changes:**

| File | Change |
|------|--------|
| `tpyc/compiler.py` | Detect `instantiate()` calls, expand compilation plan with per-instance module sets |
| `tpyc/sema/expressions.py` | Analyze `instantiate()` call, validate params against target module's declarations |
| `tpyc/codegen_cpp/generator.py` | Namespace naming with param suffix, binding at instantiation site |
| `lib/cpy/tpy/__init__.py` | CPython `instantiate()` implementation (from POC) |

### Phase 3: Sub-module propagation

**Scope:** When a parameterized module imports sub-modules within its package,
duplicate the sub-module tree per instance.

**Key changes:**

| File | Change |
|------|--------|
| `tpyc/compiler.py` | Dependency graph duplication per (module, params) tuple |
| `tpyc/parse/imports.py` | Track which imports are within a parameterized package |

### Phase 4: `Int32`/`Int64` compile params

**Scope:** Extend `compile_param()` to accept integer types. Integer params
can be used as `Array` size arguments and loop bounds.

**Key changes:**

| File | Change |
|------|--------|
| `tpyc/sema/expressions.py` | Accept `Int32`/`Int64` type annotations on `compile_param()` |
| `tpyc/sema/context.py` | Store integer param values, validate against type |
| `tpyc/codegen_cpp/generator.py` | Emit `constexpr int32_t`/`int64_t` declarations |
| `tpyc/codegen_cpp/types.py` | Resolve integer compile params in `Array[T, N]` template args |

### Phase 5: Auto-registration codegen

**Scope:** `@register` decorator generates static initializer in each TU.

**Key changes:**

| File | Change |
|------|--------|
| `tpyc/sema/statements.py` | Recognize `@register` pattern |
| `tpyc/codegen_cpp/records.py` | Generate static registration block |
| `runtime/cpp/include/tpy/` | Registry runtime support header |

---

## Test Plan

| Test | Description |
|------|-------------|
| `compile_param/basic_bool` | Single `compile_param("X")` bool, value substituted correctly |
| `compile_param/basic_int` | Single `compile_param("N")` Int32, used as Array size |
| `compile_param/multi_param` | Module with both bool and Int32 params |
| `compile_param/error_missing` | `compile_param("X")` without `instantiate()` providing X |
| `compile_param/error_direct_import` | Direct import of parameterized module without `instantiate()` |
| `compile_param/error_wrong_type` | `instantiate(..., X=42)` when X is declared as `bool` |
| `compile_param/error_name_mismatch` | `Y: bool = compile_param("X")` -- name doesn't match |
| `compile_param/two_instances` | Two `instantiate()` calls with different bool values, verify independence |
| `compile_param/three_instances` | Three instances with different int values (like the solver example) |
| `compile_param/submodule` | Params propagate to sub-module within package |
| `compile_param/submodule_partial` | Sub-module uses subset of parent's params |
| `compile_param/shared_dep` | Non-parameterized dependency is compiled once, shared |
| `compile_param/branch_elimination` | Verify generated C++ uses `constexpr`, optimizer can eliminate branches |
| `compile_param/array_size` | Int32 compile param used as `Array[T, N]` size |
| `compile_param/registration` | Auto-registration via decorator, factory lookup |
| `compile_param/cpython_compat` | Same source runs in CPython via `instantiate()` |
