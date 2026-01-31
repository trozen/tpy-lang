# TurboPython

A proof-of-concept compiler that translates Python to C++.

## Quick Start

```bash
# Install
uv sync

# Compile and run a program
tpyc -r examples/hello.tp.py

# Release build (optimized)
tpyc -rR examples/hello.tp.py

# Interactive REPL
tpyc -i
```

## CLI Reference

```
tpyc [options] <file.py>

Options:
  -r, --run         Build and run the program
  -b, --build       Compile C++ to binary
  -R, --release     Build with optimizations (default: debug)
  -i, --repl        Start interactive REPL
  -o <dir>          Output directory (default: __tpyc__/ next to source)
  -v                Verbose output
  -vv               Show compilation commands
```

## Testing

```bash
# Run all tests
uv run pytest -n auto

# Run tests for a specific case
uv run pytest -k hello -n auto
```

## Manual Build

If you need to compile the generated C++ manually:

```bash
tpyc examples/hello.tp.py -o out/
g++ -std=c++23 -I runtime -o out/program out/hello.d/hello.cpp -lgmp
./out/program
```
