# TurboPython (TPy)

A proof-of-concept compiler (tpyc) that translates Python to C++.

**Goals:**

1. **Performance** — Low-latency compiled output with opt-in constraints for hot paths (e.g. `@noalloc`). If the goals below conflict, performance wins.
2. **Regular Python compatibility** — We aim to compile and run regular Python code whenever possible, with clear diagnostics when a feature is unsupported or when semantics differ from CPython.
3. **Constrained C++ interop** — Easy integration with existing C/C++ code, but only through explicitly supported interop shapes and rules (not arbitrary native types/signatures).
4. **Familiar syntax** — Keep the language readable for non-programmers and close to regular Python where possible.
5. **Semantic transparency** — Warn when TurboPython behavior differs from CPython so differences are explicit during development.
6. **Tooling-friendly** — Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins.
7. **Thread safety** — Unlike CPython (GIL), TurboPython targets multi-threaded, high-performance environments. The compiler should be thread-safe by default where possible without sacrificing performance, and give the user explicit control where trade-offs exist.

## Example

```python
# Regular Python -- also valid TurboPython
def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

for i in range(40):
    print(fib(i))
```

```bash
$ tpyc -xO fib.py        # compile to C++ and run (optimized)
$ tpyc --dump-code fib.py # inspect generated C++
```

For performance-sensitive code, opt into fixed-width types:

```python
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def manhattan(a: Point, b: Point) -> Int32:
    return abs(a.x - b.x) + abs(a.y - b.y)
```

Source files are valid Python -- your IDE, linter, and type checker work as-is.

## Installation

### For development

```bash
git clone https://github.com/trozen/tpy-poc.git && cd tpy-poc
uv sync

uv run tpyc -x examples/hello.py
```

### For use

```bash
git clone https://github.com/trozen/tpy-poc.git && cd tpy-poc
uv tool install .
uv tool install ".[bundled]"   # bundles zig as C++ compiler
```

Or with pip: `pip install .` / `pip install ".[bundled]"`

The `tpyc` command is then available globally:

```bash
tpyc -c "print(1 + 2)"
```

Use `tpyc --cxx list` to see available C++ compilers.

## Quick Start

```bash
tpyc -c "print(1 + 2)"          # run inline code
tpyc -x examples/hello.py       # compile and run a file
tpyc -xO examples/hello.py      # release build (optimized)
tpyc -i                          # interactive REPL
tpyc --dump-code file.py         # inspect generated C++
tpyc --cxx list                  # show available C++ compilers
tpyc -x -j4 examples/hello.py   # parallel compilation (4 jobs)
tpyc --install-agent-docs docs/ # install TPy agent docs into your project
```

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

## Dependencies

- Python 3.12+
- A C++23 compiler: g++ 14+, clang++ 18+, or zig (auto-detected)

No external C/C++ libraries are required by the runtime.

## Development

```bash
# Run all tests
uv run pytest

# Run tests for a specific case
uv run pytest -k hello

# View built-in type documentation
uv run tpyc --print-types | glow -p
```

See `CLAUDE.md` for architecture, test structure, and development guidelines.
See `docs/LANGUAGE_FEATURES.md` for comprehensive language documentation.
See `docs/TPY_FOR_AGENTS.md` for the agent-facing bootstrap (also installable
into downstream projects via `tpyc --install-agent-docs`).
