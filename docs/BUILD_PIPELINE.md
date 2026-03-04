# Build Pipeline

## Current Architecture

```
Python source -> tpyc (codegen) -> C++ (.hpp/.cpp) -> g++/clang -> binary
```

- **tpyc codegen**: ~0.1s (Python AST -> C++ text)
- **C++ compilation**: ~1.2s per translation unit (the bottleneck)

Every generated `.cpp` file includes `tpy/tpy.hpp`, which expands to ~133k
preprocessed lines (469 transitive headers). This dominates compile time.

### Per-header cost breakdown

| Runtime header      | Preprocessed lines | Compile time |
|---------------------|--------------------|--------------|
| `printing.hpp`      | 105k               | 0.9s         |
| `bigint.hpp`        | 89k                | 0.7s         |
| `format.hpp`        | 78k                | 0.7s         |
| `container_ops.hpp` | 72k                | 0.5s         |
| `core.hpp`          | 49k                | 0.4s         |

(Measured with g++ 13, C++23, debug build, on Linux x86_64.)

## Optimization Options

### 1. Precompiled Headers (PCH) -- highest priority

g++ can precompile `tpy.hpp` into a binary `.gch` file that is reused across
all translation units, skipping header parsing entirely.

```bash
# Build PCH once (~3s)
g++ -std=c++23 -g -O0 -x c++-header tpy/tpy.hpp -o tpy/tpy.hpp.gch

# Each TU automatically picks up the .gch if flags match
g++ -std=c++23 -g -O0 -I ... -c module.cpp
```

**Impact**: Per-TU compile time drops from ~1.2s to ~0.35s (3.4x faster).

**Gotchas**:
- The `.gch` must be compiled with identical flags (`-std`, `-O`, `-g`, `-D`)
  as the source files, otherwise g++ silently ignores it.
- Only one PCH per translation unit.
- Compiler-version-specific (not portable across g++ versions).
- Separate PCH needed for debug vs release builds.

### 2. Parallel Compilation

The ccache code path already splits compilation into per-TU commands. Adding
`-j N` parallelism (or using a proper build system) enables parallel builds.

**Impact** (measured, 8 cores):

| Scenario                       | 10 TUs | 100 TUs |
|--------------------------------|--------|---------|
| Sequential, no PCH             | 11.6s  | ~116s   |
| Sequential, with PCH           | 3.6s   | ~36s    |
| **Parallel (8 jobs) + PCH**    | **0.8s** | **5.2s** |
| Parallel (8 jobs), no PCH      | 2.4s   | ~24s    |

### 3. Selective Includes

Instead of every file including `tpy.hpp` (the kitchen sink), the codegen
could track which runtime features each module actually uses and generate
per-module include lists. A module that only uses `int32_t` and `print`
doesn't need `bigint.hpp`, `ordered_map.hpp`, etc.

More complex to implement but could halve compile times for simple modules.

### 4. Incremental Builds

Only recompile changed modules. The per-TU object file split (ccache path)
already provides the foundation -- would need content hashing or timestamp
tracking to skip unchanged TUs.

## Alternative Backends Considered

### LLVM IR

Replacing C++ codegen with direct LLVM IR emission was considered and
rejected for now:

- Would require reimplementing everything the C++ compiler gives us for free:
  template instantiation (monomorphization), move semantics, destructor
  ordering, vtable layout, STL containers.
- The runtime library (`tpy::BigInt`, checked arithmetic, `ordered_map`, etc.)
  would need to be rewritten in IR or C.
- Would **not** help with compile speed -- the bottleneck is parsing 133k
  lines of C++ headers, which PCH solves directly.
- Would **lose** C++ header generation for interop (currently free).

May revisit for specific use cases (JIT in REPL, shipping without C++
toolchain) as a parallel backend, not a replacement.

### Embedded Clang (libclang)

Using Clang as a library instead of spawning `g++` as a subprocess. Moderate
effort, avoids process spawn overhead, but adds a heavy dependency (~100MB+).
Not worth it given that PCH + parallel already gets 100 TUs to ~5s.
