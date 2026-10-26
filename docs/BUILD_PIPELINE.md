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
- Clang tracks `#pragma once` by file identity, so a `.gch` is only reusable
  from the include root it was built against: reaching the same headers
  through a second checkout's `-I` re-parses them and redefines everything.
  GCC compares contents instead, so its `.gch` travels between checkouts.
  Any cache that shares a `.gch` machine-wide must key on the include root
  (`toolchain.pch_is_path_sensitive`).
- One builder, `toolchain.shared_pch`, serves the CLI, the REPL and the test
  harness. Its `.gch` lives in the shared cache, `~/.cache/tpyc/pch/<key>/`
  (`$TPYC_SHARED_CACHE_DIR` overrides the root), never in a program's
  `__tpyc__/` dir; the key (`toolchain.pch_entry`) hashes the runtime headers'
  content, the PCH build command (compiler command, `-std`, extra/warn flags,
  opt flags -- so debug and release, and each consumer's flag set, get their
  own entry; every header or search path in it -- `-I`, `-isystem`,
  `-iquote`, `-idirafter`, `-include`, `-imacros`, `-iframework`, `-F`,
  `-isysroot`, `--sysroot` -- counts resolved against the cwd, symlinks
  followed as the compiler does), the `CPATH` / `CPLUS_INCLUDE_PATH`
  environment (resolved the same way; `C_INCLUDE_PATH` is read by C compiles
  only), the compiler binary's identity and, on clang only, the include root.
  Every program built with the same flags reuses one `.gch`.
- The entry also records a `deps` list: every header the build read outside
  the runtime (system and toolchain headers) with its inode, ctime, mtime and
  size (`toolchain.file_identity`). The kernel sets ctime on every content
  change, rename or replacement, and tools that restore mtime (tar, `cp -p`,
  package managers) cannot set it back. An entry whose `deps` no longer
  match (a libstdc++ upgrade under an unchanged compiler binary) is rebuilt
  in place. The same identity names the compiler binary in the entry key, in
  the toolchain probe cache and in the whole-run build cache, whose manifest
  re-hashes an input only when its identity moved.
- A build runs under the entry's `.lock` (first removing temp files a killed
  build left: every temp the builder writes is `<name>.<pid>.tmp` of a file
  the entry holds) and publishes the `.gch` by one atomic rename, then its
  `deps`; an entry without `deps` is not ready, so a reader never pairs a
  `.gch` with another build's `deps`. A build during which a runtime header
  changed, or an external one's ctime moved (at or after the build's start,
  as the kernel stamps a temp file it creates), publishes nothing. A failed build,
  or an unwritable cache root, compiles without a PCH. `tpy --rebuild`
  rebuilds the entry; `tpy --no-pch` skips it. The CLI (after a build) and
  the REPL (at startup) also run the shared-cache sweep, which removes
  entries unused for `$TPYC_CACHE_MAX_AGE`, and the temp files of a build
  killed over a ready entry once they are that old (see `tests/README.md`
  "Cache eviction"). The REPL builds its compiler config through
  `CppCompilerConfig.for_compiler`, as `from_env` does for the CLI and the
  harness, so its compiles and its PCH carry the same macOS deployment pin.

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

### ccache across programs and checkouts

Every compile and link step of a program build -- in the CLI, the REPL and
the test harness -- runs through `toolchain.run_build_step(cmd, cwd)`. Given
a cwd, the step runs there with it as `CCACHE_BASEDIR`, so ccache rewrites
the paths under it relative to the cwd before hashing; given None, it runs
from the caller's cwd with the environment untouched. The CLI and the REPL
take the cwd from `toolchain.build_step_cwd(config, base_dir)` -- their base
dir when the config shares (below), else None -- computed once per build and
passed to both `run_build_step` and `build_cpp_commands(step_cwd=...)`. The
CLI's base dir is the program's build dir (`BuildLayout.base_dir`, the
`<prog>.d/` that holds `src/`, `include/` and the per-variant object dirs);
the REPL's is its session dir. The harness passes its checkout root to both,
whatever its config: it has always run from there. The shared PCH build and the toolchain probes run outside it on
purpose: their commands are the same for every program.

`build_cpp_commands` spells the paths that lie under its `step_cwd` --
for the CLI the generated sources, `-I <base>/include`, objects, the link's
inputs and output -- relative to it, so a program's commands are
byte-identical in every build dir. That is what zig's own object cache
needs: it keys a `zig c++ -c` on the literal command line plus the content
of every dependency (not on `-o` or the cwd), so with ccache absent the
bundled-zig install still shares every stdlib and runtime TU across
programs. Every other path stays absolute -- for the CLI the runtime and
third-party sources and include dirs, the shared PCH, and all paths of a
build that does not share or of a debug build (`debug=`, the CLI's
`--debug`: zig would hand a second program an object whose debug info names
the first program's build dir). Absolute paths are joined onto the cwd
without collapsing `..` (the kernel resolves it after a symlink), and the
relative ones are taken lexically against the equally uncollapsed step cwd,
so both name what the kernel finds from the step's cwd. In the harness the
paths under the checkout root (the case's sources and objects, the runtime
include dir) are relative to it; they name the same files from there, and
neither the exec fingerprint nor the stdlib cache's `output_hash` hashes these
commands. The REPL builds its own commands with absolute paths.

Release builds pass `-g0` (`cli._opt_flags`): zig emits debug info even at
`-O3`, so without it a second program's object, served from zig's cache or
ccache, would carry `DW_AT_comp_dir` naming the first program's build dir.
g++ and clang emit none without `-g`, so every release build agrees. The
link step takes the same opt flags: zig builds its own libc++ / libunwind at
the link line's optimization level, so a release link without them pulls in
a Debug-mode libc++ (a json program: 8.26 MB -> 7.74 MB). g++ and clang link
prebuilt runtimes and ignore them there.

ccache wraps zig as it wraps any compiler (`ccache <zig> c++`): the zig
binary tpyc runs is the real executable, so ccache's `compiler_check` keys
on it, and the PCH sloppiness tpyc sets reaches ccache through the
environment either way. With both, ccache answers first; zig's own cache
answers what ccache misses.

The user's compiler settings are never parsed or rewritten: a path in them
(`-include x.h` found through `-I`, `-I=` under `--sysroot`, a path inside
an `@file`, a relative compiler, a relative `CPATH` element) means something
relative to the caller's cwd, in more spellings than a rewrite can follow. So
a CLI or REPL build shares only when the user supplied none
(`CppCompilerConfig.shares_build_dir`, decided where the config is built): the
compiler is a bare name `PATH` resolves to an absolute path, or an absolute
path, followed only by the words tpyc adds itself (zig's `c++`, the darwin
version pin); no extra flags were given; and `CPATH`, `CPLUS_INCLUDE_PATH`,
`C_INCLUDE_PATH` and `LIBRARY_PATH` are unset or empty. Otherwise its steps
run from the caller's cwd with the environment untouched and share nothing
across programs.

- Generated stdlib C++ is byte-identical across programs, so a second
  program in another directory hits every stdlib TU the first one stored
  (measured: a `print("hi")` program's second copy builds in ~0.1s instead of
  ~5.6s; a json/re/datetime/os program's in ~0.1s instead of ~17s).
- The TPy-owned runtime TUs (`runtime/cpp/src/**/*.cpp`) include only runtime
  headers, so they compile without the program's `-I <prog>.d/include` and
  without the PCH force-include: their commands are the same in every program,
  and they skip parsing the whole PCH (`socket_impl.cpp`: 2.5s -> 0.2s).
- `-g` builds (`--debug`) do not share across directories: ccache hashes the
  cwd of a debug compile, and the debug info keeps the absolute source paths
  so a debugger finds the sources from any directory. A
  `-fdebug-prefix-map=<build dir>=.` would let them share but record sources
  relative to `.`, which a debugger started elsewhere cannot find.
- Third-party TUs carry no program path and hit as well.
- A third-party library's include dir (`ThirdPartyBuildPlan.extra_include_dirs`,
  e.g. PCRE2's `src/` when a program uses `re`) goes to the generated TUs
  only: a user `@native` module declaring the library may include its
  public headers, while the runtime TUs include only runtime headers (the
  `*_shim` sources that include upstream headers are the library's own
  sources, built with its flags). So the runtime TUs hit between programs
  that use the library and programs that do not; the generated stdlib TUs
  still differ by that `-I`.

Current limitation: other settings the compiler driver or ccache resolve
against the cwd are not part of that check -- a relative `COMPILER_PATH` or
`GCC_EXEC_PREFIX`, a relative `CCACHE_CONFIGPATH` / `CCACHE_DIR`, a relative
or empty element in `PATH` -- so in a sharing build they resolve against the
build dir instead of the caller's cwd. Spell them as absolute paths.

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
