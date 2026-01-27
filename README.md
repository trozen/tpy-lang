# TurboPython

A proof-of-concept compiler that translates Python to C++.

## Quick Start

```bash
# Install
uv sync

# Compile and run a program
tpyc examples/hello.tp.py --run

# Interactive REPL
tpyc --repl
```

## Testing

```bash
# Run all tests
pytest

# Run tests for a specific case
pytest -k hello
```

## Manual Build

If you need to compile the generated C++ manually:

```bash
tpyc examples/hello.tp.py -o out/
g++ -std=c++20 -I runtime -o out/program out/hello.d/hello.cpp -lgmp
./out/program
```
