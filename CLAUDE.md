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

# Interactive REPL
tpyc --repl

# Install for development (uses uv package manager)
uv sync
```

## Testing

```bash
# Run all tests
pytest

# Run tests for one case (pattern matching)
pytest -k hello

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
├── test_cases.py             # Main test module (codegen + output)
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
| `sema.py` | Multi-pass semantic analysis: type checking, type inference |
| `codegen_cpp.py` | Generates `.hpp` (header) and `.cpp` (source) files |

### Runtime (`runtime/`)

`tpy_runtime.hpp` provides C++ template utilities, primarily `StaticList<T, N>` - a fixed-capacity container with zero dynamic allocation. Generated code requires C++20 (for `std::span`).

## Performance Profiles (Planned)

TurboPython will support performance profiles applied at function, class, or module level:

- **Default**: Standard Python constructs allowed (`list`, `str`, etc.)
- **`@noalloc`**: No heap allocation - for hot paths and real-time code

Note: `@noalloc` is parsed but not yet enforced. See `docs/LANGUAGE_FEATURES.md` for the design.

## Current Limitations

The compiler is a proof-of-concept. Not yet implemented:
- `dict`, `set`, `tuple`
- Exception handling (`try`/`except`/`raise`)
- `async`/`await`, `lambda`, `yield`
- Inheritance
- List slicing (`items[1:3]`)

## Type Mappings

| TurboPython | C++ |
|-------------|-----|
| `int` | `tpy::BigInt` (arbitrary precision) |
| `Int32` | `int32_t` |
| `Bool` | `bool` |
| `str` | `const char*` (parameters: `std::string_view`) |
| `Char` | `char` |
| `list[T]` | `std::vector<T>` |
| `Array[T, N]` | `std::array<T, N>` |
| `Span[T]` | `std::span<const T>` |
| `StaticList[T, N]` | `StaticList<T, N>` |
| `Ptr[T]` | `T*` |
| `ConstPtr[T]` | `const T*` |

## Supported Language Features

### Control Flow
- `if`/`elif`/`else` conditionals
- `while` loops
- `for i in range(n)` and `for i in range(start, end)`
- `for x in collection` (for-each over list, Array, Span, str)
- `break` and `continue`

### Operators
- Arithmetic: `+`, `-`, `*`, `//`, `%`, unary `-`
- Comparison: `==`, `!=`, `<`, `<=`, `>`, `>=`
- Membership: `in`, `not in` (for list, Array, Span, str)
- Logical: `and`, `or`, `not`
- Bitwise: `&`, `|`, `^`, `~`, `<<`, `>>`
- Augmented assignment: `+=`, `-=`, `*=`, `//=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=`

### Classes
- Typed fields
- `__init__` constructor
- Instance methods with `self` parameter

### Built-in Functions
- `print()` - supports multiple arguments
- `len()` - works with `list`, `Array`, `Span`, `StaticList`, `str`
- `range()` - for loop iteration

### List Methods
- `append(x)`, `pop()`, `insert(i, x)`, `remove(x)`, `clear()`, `extend(other)`
- Indexing: `items[i]`, `items[-1]` (negative indexing supported)

## Development Guidelines

When implementing new features:
- If you encounter a hard problem or are unsure how to proceed, **ask first** before attempting a complex solution
- Check TODO.md for current priorities
- Run `pytest` to verify no regressions after changes
- Update test snapshots with `python tests/update_snapshots.py` when expected output changes intentionally

**Before committing**: Verify that documentation is still accurate:
- `CLAUDE.md` - commands, test structure, type mappings, supported features
- `README.md` - quick start commands, build flags
- `docs/LANGUAGE_FEATURES.md` - working/planned status of features

If committed code adds new features or changes behavior, update the relevant docs in the same commit.
