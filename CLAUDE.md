# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TurboPython (tpyc) is a proof-of-concept compiler that translates Python to C++. The goal is **Python-first**: idiomatic Python should work out of the box, with opt-in performance constraints for hot paths. Most code uses standard Python constructs (`int`, `str`, `list`), while critical sections can enforce allocation restrictions via performance profiles (`@noalloc`).

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
│           ├── main.hpp      # Named after source module
│           ├── main.cpp
│           └── output.txt
├── harness/tpy/              # CPython simulation module
├── conftest.py               # Pytest fixtures
├── test_codegen.py           # C++ code generation tests
├── test_output.py            # Runtime output tests
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
| `typesys.py` | Type definitions (Int32, Bool, Void, Str, Record, Ptr, ConstPtr, StaticList, Array, Span) and TypeRegistry |
| `sema.py` | Multi-pass semantic analysis: type checking, `@noalloc` validation |
| `codegen_cpp.py` | Generates `.hpp` (header) and `.cpp` (source) files |

### Runtime (`runtime/`)

`tpy_runtime.hpp` provides C++ template utilities, primarily `StaticList<T, N>` - a fixed-capacity container with zero dynamic allocation. Generated code requires C++20 (for `std::span`).

## Performance Profiles

TurboPython supports different performance profiles applied at function, class, or module level:

- **Default**: Standard Python constructs allowed (`list`, `dict`, `str`, etc.)
- **`@noalloc`**: No heap allocation - for hot paths and real-time code

See `docs/LANGUAGE_FEATURES.md` for full profile hierarchy and pluggable backend system.

## Current Limitations

The compiler is a proof-of-concept. Not yet implemented:
- `list`, `dict`, `set`, `tuple` (use `StaticList`, `Array` for now)
- `str` type (string literals work as `const char*`)
- Exception handling (`try`/`except`/`raise`)
- `async`/`await`, `lambda`, `yield`
- Inheritance

## Type Mappings

| TurboPython | C++ |
|-------------|-----|
| `Int32` | `int32_t` |
| `Bool` | `bool` |
| `Ptr[T]` | `T*` |
| `ConstPtr[T]` | `const T*` |
| `StaticList[T, N]` | `StaticList<T, N>` |
| `Array[T, N]` | `std::array<T, N>` |
| `Span[T]` | `std::span<const T>` |
| String literals | `const char*` |

## Supported Language Features

### Control Flow
- `if`/`elif`/`else` conditionals
- `while` loops
- `for i in range(n)` and `for i in range(start, end)`
- `break` and `continue`

### Operators
- Arithmetic: `+`, `-`, `*`, `//`, `%`, unary `-`
- Comparison: `==`, `!=`, `<`, `<=`, `>`, `>=`
- Logical: `and`, `or`, `not`
- Bitwise: `&`, `|`, `^`, `~`, `<<`, `>>`
- Augmented assignment: `+=`, `-=`, `*=`, `//=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=`

### Classes
- Typed fields
- `__init__` constructor
- Instance methods with `self` parameter

### Built-in Functions
- `print()` - supports multiple arguments
- `len()` - works with `StaticList`, `Array`, `Span`
- `range()` - for loop iteration
