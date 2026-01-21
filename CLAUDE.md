# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TurboPython (tpyc) is a proof-of-concept compiler that translates a restricted Python-syntax language to C++ for ultra-low-latency applications. It enforces allocation constraints and type safety at compile time.

## Commands

```bash
# Compile and run (simplest workflow)
tpyc examples/hello.tp.py --run

# Compile to C++ and build binary
tpyc examples/hello.tp.py --build

# Compile to C++ only (output in __tpyc__/ next to source)
tpyc examples/hello.tp.py

# Compile to specific output directory
tpyc examples/hello.tp.py -o out/

# Verbose mode
tpyc examples/hello.tp.py --run -v

# Install for development (uses uv package manager)
uv sync
```

## Testing

```bash
# Run all tests
pytest

# Run specific test
pytest tests/test_compiler.py::test_hello_codegen

# Run tests for one case (pattern matching)
pytest -k hello

# Run all codegen or output tests
pytest -k codegen
pytest -k output

# Update expected snapshots after intentional changes
python tests/update_snapshots.py           # all cases
python tests/update_snapshots.py hello     # specific case
```

### Test Structure

```
tests/
├── cases/                    # Test cases (auto-discovered)
│   └── {name}/
│       ├── src/              # TurboPython source files
│       │   └── main.tp.py
│       └── expected/         # Expected outputs (snapshots)
│           ├── generated.hpp
│           ├── generated.cpp
│           └── output.txt
├── harness/tpy/              # CPython simulation module
├── test_compiler.py          # Test runner
└── update_snapshots.py       # Snapshot update utility
```

### Adding a New Test Case

1. Create `tests/cases/{name}/src/main.tp.py`
2. Run `python tests/update_snapshots.py {name}` to generate expected outputs
3. Run `pytest -k {name}` to verify

## Code Style

For `tpyc/` compiler modules:
- Imports at top of file only (avoid internal imports unless unavoidable)
- Use type annotations

## Architecture

The compiler follows a 4-stage pipeline:

```
TurboPython Source (.tp.py) → Parser → Semantic Analyzer → Code Generator → C++ (.hpp/.cpp)
```

### Core Modules (`tpyc/`)

| Module | Purpose |
|--------|---------|
| `cli.py` | CLI entry point, argument parsing, error handling |
| `parse.py` | Uses Python's `ast` module to build TurboPython AST nodes |
| `typesys.py` | Type definitions (Int32, Void, Str, Record, Ptr, ConstPtr, StaticList) and TypeRegistry |
| `sema.py` | Multi-pass semantic analysis: type checking, `@noalloc` validation |
| `codegen_cpp.py` | Generates `.hpp` (header) and `.cpp` (source) files |

### Runtime (`runtime/`)

`tpy_runtime.hpp` provides C++ template utilities, primarily `StaticList<T, N>` - a fixed-capacity container with zero dynamic allocation.

## Language Constraints

TurboPython enforces strict constraints for low-latency guarantees:

- **Required type annotations** on all function parameters
- **No inheritance** - classes are flat records with `__init__` only
- **No dynamic allocation** in `@noalloc` functions
- **Forbidden constructs**: `list`, `dict`, `set`, `tuple`, `str`, `try`, `raise`, `with`, `async`, `await`, `lambda`, `yield`, `global`, `nonlocal`
- **Imports restricted** to `tpy` module only

## Type Mappings

| TurboPython | C++ |
|-------------|-----|
| `Int32` | `int32_t` |
| `Ptr[T]` | `T*` |
| `ConstPtr[T]` | `const T*` |
| `StaticList[T, N]` | `StaticList<T, N>` |
| String literals | `const char*` |
