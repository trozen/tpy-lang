# TurboPython (TPy)

A compiler that translates Python to C++.

**Goals:**

1. **Performance** — Low-latency compiled output with opt-in constraints for hot paths (e.g. `@noalloc`). If the goals below conflict, performance wins.
2. **Regular Python compatibility** — We aim to compile and run regular Python code whenever possible, with clear diagnostics when a feature is unsupported or when semantics differ from CPython.
3. **Constrained C++ interop** — Easy integration with existing C/C++ code, but only through explicitly supported interop shapes and rules (not arbitrary native types/signatures).
4. **Familiar syntax** — Keep the language readable for non-programmers and close to regular Python where possible.
5. **Semantic transparency** — Warn when TurboPython behavior differs from CPython so differences are explicit during development.
6. **Tooling-friendly** — Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins.
7. **Thread safety** — Unlike CPython (GIL), TurboPython targets multi-threaded, high-performance environments. The compiler should be thread-safe by default where possible without sacrificing performance, and give the user explicit control where trade-offs exist.

## Example

Main differences from CPython:

- Type annotations required on functions (parameters + return) and class fields; local variables are inferred
- `int32` for integer literals (overrideable), `int32`/`int64` for explicit fixed-width, `int` = `BigInt` for arbitrary precision
- Value types (`int32`, `bool`, `str`, ...) are copied; reference types (classes, containers) are passed by reference to functions but stored inline in fields and containers. `Own[T]` transfers ownership (move) at function boundaries. The distinction is one axis, not a list of blessed types: `bytearray` and `Array[T, N]` are reference types alongside `list`/`dict`/`set` and your own classes, and reach the same slots by the same rule
- No GIL, no refcounting, no GC -- deterministic destruction via RAII

```python
from tpy import int32

def fib(n: int32) -> int32:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

for i in range(40):
    print(fib(i))
```

```bash
$ tpy fib.py             # compile to C++ (-O3) and run
$ tpy --debug fib.py     # unoptimized build with debug info (-g -O0)
$ tpy --dump-code fib.py # inspect generated C++
```

Reference types (classes, `list`, `dict`, ...) are passed by reference to functions, but stored
inline in class fields and containers. `Own[T]` marks ownership transfer -- the value is moved,
not referenced:

```python
from dataclasses import dataclass
from tpy import Own, int32

@dataclass
class Event:
    timestamp: int32
    code: int32

def make_batch(n: int32) -> Own[list[Event]]:
    # list comprehension creates a new list; Own means it is moved out to the caller
    return [Event(i, i * 2) for i in range(n)]

batch = make_batch(3)  # batch owns the list (moved, not copied)
for e in batch:
    print(e.timestamp, e.code)
```

Where TurboPython would silently copy what CPython shares by reference (e.g. storing a parameter into a field or container), the compiler warns and suggests an explicit `copy()` -- so dual-target code behaves identically under both runtimes.

There is no GIL: real OS threads with compile-checked safety (`spawn`, `Arc[Mutex[T]]`, atomics, cross-thread channels -- a non-thread-safe capture is a compile error, not a race). A TPy module can also compile into a regular CPython extension: mark it `# tpy: ext_module` and import the built `.so` from ordinary Python.

Source files are valid Python -- your IDE, linter, and type checker work as-is.

## Installation

```bash
pip install tpy-lang
pip install "tpy-lang[bundled]"   # also installs zig as a bundled C++ compiler
```

Or as an isolated tool with uv:

```bash
uv tool install tpy-lang
uv tool install "tpy-lang[bundled]"
```

Two commands are installed: `tpy` (runs programs, drops to a REPL with no args) and `tpyc` (compile-only; emits `.hpp`/`.cpp`).

### From source

```bash
uv sync                          # in a checkout of the source tree
uv run tpy examples/hello.py
```

## Quick Start

```bash
tpy                              # interactive REPL
tpy -c "print(1 + 2)"            # run inline code
```

Create a `hello.py`:

```python
def main() -> None:
    print("Hello from TurboPython!")

main()
```

Then:

```bash
tpy hello.py                     # compile and run a file
tpy --debug hello.py             # debug build (-g -O0; the default is -O3)
tpy --dump-code hello.py         # inspect generated C++
tpy --cxx list                   # show available C++ compilers
tpy -j4 hello.py                 # parallel compilation (4 jobs)
tpy --install-agent-docs docs/   # install TPy agent docs into your project

tpyc hello.py                    # compile only -- emit .hpp/.cpp into __tpyc__/
tpyc -o out/ hello.py            # compile only, custom output directory
```

Re-running an unchanged program skips the whole pipeline: after a
successful build, `tpy` records every input (sources, imported modules,
compiler and toolchain identity, options) next to the binary and, when
nothing changed, executes the binary directly (~100ms startup instead of a
rebuild). Anything changed -- a source edit, a new file that shadows an
imported module, a compiler upgrade, different flags -- triggers a normal
rebuild. `--rebuild` forces one; it's also the escape hatch for the
(ccache-grade) blind spots: system-mode third-party libraries
(`--pcre2=system` etc.) resolve at link time outside the tracked inputs,
and compile-affecting environment variables (`CPATH`,
`CPLUS_INCLUDE_PATH`, `LIBRARY_PATH`, `CCACHE_*`) are not part of the
key -- after changing either, run once with `--rebuild`.

Ctrl-C reaches the running program after both fresh builds and cached runs.
After a fresh build, the launcher reports signal termination as exit status
`128 + signal` (130 for SIGINT, 143 for SIGTERM); a program that handles the
signal keeps its own exit status. An intentionally inherited ignored SIGINT
remains ignored. Cached runs replace the launcher, so process supervisors
observe signal termination directly instead of a normal launcher exit.

A `sources.cmake` file is generated alongside the C++ output for easy CMake integration.
By default, the tpy runtime headers are bundled into the output directory so the
result is self-contained and can be committed or copied to another machine.
Use `--no-bundle-runtime` to skip the copy (e.g. during development on the runtime itself).

```cmake
include(path/to/__tpyc__/myapp.d/sources.cmake)
add_executable(myapp ${TPYC_SOURCES})
target_include_directories(myapp PRIVATE ${TPYC_INCLUDE_DIRS})
target_link_libraries(myapp PRIVATE ${TPYC_LIBRARIES})
set_target_properties(myapp PROPERTIES CXX_STANDARD ${TPYC_CXX_STANDARD})
```

## Coding with an AI agent (recommended)

TurboPython source is valid Python, so coding agents (Claude Code, Cursor,
Copilot, ...) and your existing tooling work out of the box. The fastest way to
be productive is to hand the agent TPy's rules and exact API surface up front:

```bash
tpy --install-agent-docs docs/   # writes TPY_*.md into ./docs and prints a
                                 # snippet to add to your AGENTS.md / CLAUDE.md
```

This installs four reference files into your project:

- `TPY_FOR_AGENTS.md` -- concise Python-to-TPy bootstrap (the delta, ownership
  rules, idiomatic patterns)
- `TPY_LANGUAGE_FEATURES.md` -- full language reference (only **Working**
  sections are usable today)
- `TPY_STDLIB_ROADMAP.md` -- stdlib coverage (what's available vs missing)
- `TPY_API_REFERENCE.md` -- the exact callable API surface, generated from the
  installed version

Append the printed snippet to your `AGENTS.md` / `CLAUDE.md` so the agent reads
them before writing TPy code. Re-run after upgrading `tpy-lang` to refresh.

## Dependencies

- Python 3.12+
- A C++23 compiler: g++ 13+, clang++ 19+, or zig (auto-detected)

Compilers below the floor are skipped during auto-detection (falling
through to the next viable one, e.g. the `[bundled]` zig toolchain); an
explicit `--cxx`/`$CXX` selection is honored with a warning.

No external C/C++ libraries are required by the runtime.

## Development

```bash
# Run all tests
uv run pytest

# Run tests for a specific case
uv run pytest -k hello

# View built-in type documentation
uv run tpy --print-types | glow -p
```

From a source checkout: see `docs/ARCHITECTURE.md` for the compiler
architecture, `docs/LANGUAGE_FEATURES.md` for the full language reference, and
`docs/TPY_FOR_AGENTS.md` for the agent-facing bootstrap (also installable into
your project via `tpy --install-agent-docs`).
