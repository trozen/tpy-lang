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

## Dependencies

No external C/C++ libraries are required by the runtime. A C++23 compiler is enough.

## Quick Start

```bash
# Install
uv sync

# Compile and run a program
tpyc -x examples/hello.py

# Release build (optimized)
tpyc -xO examples/hello.py

# Interactive REPL
tpyc -i
```

## CLI Reference

```
tpyc [options] <file.py>

Options:
  -x, --exec        Build and run the program
  -b, --build       Compile C++ to binary
  -O, --release     Build with optimizations (default: debug)
  -i, --repl        Start interactive REPL
  -o <dir>          Output directory (default: __tpyc__/ next to source)
  --dump-code       Print generated C++ to stdout (no build)
  --default-int     Default type for unannotated integer literals: Int32|Int64|BigInt (default: Int32)
  --emit-source     Embed Python source lines as comments in generated C++
  -L <path>         Extra library search path (can be repeated)
  --no-tplib        Disable tplib standard library
  --no-stdlib       Disable Python stdlib analogs
  -v                Verbose output
  -vv               Show compilation commands
```

Examples:

```bash
# Performance-first default (same as implicit default)
tpyc -x --default-int=Int32 examples/hello.py

# CPython-like unbounded integer behavior for unannotated literals
tpyc -x --default-int=BigInt examples/hello.py

# Extra library search paths
tpyc -x -L /my/libs examples/main.py
```

## Testing

```bash
# Run all tests
uv run pytest

# Run tests for a specific case
uv run pytest -k hello
```

## View library documentation

For pretty printing:

```
tpyc --print-types | glow -p
```

## Manual Build

If you need to compile the generated C++ manually:

```bash
tpyc examples/hello.py -o out/
g++ -std=c++23 -I runtime -o out/program out/hello.d/hello.cpp
./out/program
```
