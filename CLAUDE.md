# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TurboPython (tpyc) is a proof-of-concept compiler that translates a restricted Python-syntax language to C++ for ultra-low-latency applications. It enforces allocation constraints and type safety at compile time.

## Commands

```bash
# Compile a TurboPython file
tpyc examples/hello.tp.py -o out/
tpyc examples/hello.tp.py -o out/ -v    # verbose mode

# Alternative invocation
python -m tpyc examples/hello.tp.py -o out/

# Install for development (uses uv package manager)
uv pip install -e .
```

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
