# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TurboPython (tpyc) is a proof-of-concept compiler that translates Python to C++. The goal is **Python-first**: idiomatic Python should work out of the box, with opt-in performance constraints for hot paths. Most code uses standard Python constructs (`int`, `str`, `list`), while critical sections can enforce allocation restrictions via performance profiles (`@noalloc`).

## Commands

```bash
# Compile and run (debug build, default)
tpyc -x examples/hello.tp.py

# Compile and run (release build, optimized)
tpyc -xO examples/hello.tp.py

# Compile to C++ and build binary
tpyc -b examples/hello.tp.py

# Compile to C++ only (output in __tpyc__/ next to source)
tpyc examples/hello.tp.py

# Compile to specific output directory
tpyc examples/hello.tp.py -o out/

# Verbose mode (-v for info, -vv for compilation commands)
tpyc -x examples/hello.tp.py -vv

# Interactive REPL
tpyc -i

# Install for development (uses uv package manager)
uv sync
```

## Testing

Always use `-n auto` for parallel test execution (much faster).

```bash
# Run all tests (parallel)
uv run pytest -n auto

# Run fast compilation tests only (diagnostics, codegen)
uv run pytest tests/test_comp.py -n auto

# Run slow execution tests only (C++ build, run, CPython comparison)
uv run pytest tests/test_exec.py -n auto

# Run tests for one case (pattern matching)
uv run pytest -k hello -n auto

# Update expected snapshots after intentional changes
uv run python tests/update_snapshots.py              # all cases
uv run python tests/update_snapshots.py hello        # specific case
uv run python tests/update_snapshots.py --comp       # compilation tests only
uv run python tests/update_snapshots.py --exec       # execution tests only
uv run python tests/update_snapshots.py --comp hello # specific case, comp only
```

**Important**: If a change would modify expected output for *existing* tests (not new tests you're adding), consult with the user before running `update_snapshots.py`. Explain what generated code will change and confirm the change is desired.

### Test Structure

```
tests/
├── cases/                    # Normal tests (compile + run)
├── errors/                   # Compilation error tests (compile only)
├── panics/                   # Runtime panic tests (compile + run, expect failure)
│   └── {name}/
│       ├── src/
│       │   └── main.tp.py
│       └── expected/
│           ├── diag.txt      # Compiler diagnostics
│           ├── main.hpp      # Generated header (if compiles)
│           ├── main.cpp      # Generated source (if compiles)
│           └── output.txt    # Runtime output (or panic.txt)
├── harness/tpy/              # CPython simulation module
├── conftest.py               # Pytest fixtures and shared utilities
├── test_comp.py              # Fast compilation tests (diagnostics, codegen)
├── test_exec.py              # Slow execution tests (C++ build, run)
└── update_snapshots.py       # Snapshot update utility
```

### Adding a New Test Case

1. Create test in the appropriate directory:
   - `tests/cases/{name}/` - normal tests that compile and run
   - `tests/errors/{name}/` - tests for compilation errors
   - `tests/panics/{name}/` - tests for runtime panics
2. Add source file: `src/main.tp.py`
3. Add `# tpyc:` annotations on lines that test specific compiler behavior:
   - `# tpyc: ok` - line should compile without error
   - `# tpyc: error(/regex/)` - line should produce an error matching the regex
4. Run `uv run python tests/update_snapshots.py {name}` to generate expected outputs
5. Run `uv run pytest -k {name}` to verify

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
| `sema/` | Multi-pass semantic analysis: type checking, type inference (composable package) |
| `codegen_cpp/` | C++ code generation: expressions, statements, records, protocols (composable package) |
| `compiler.py` | Multi-module orchestration: discovery, dependency resolution, compilation order |
| `modules/` | Built-in function/type definitions and module resolution (`resolver.py` for user modules) |

### Runtime (`runtime/`)

The C++ runtime is organized as a modular header library in `runtime/cpp/include/tpy/`:

| Header | Purpose |
|--------|---------|
| `tpy.hpp` | Main header - includes all modules |
| `core.hpp` | `tpy_panic`, `deref_ptr` |
| `format.hpp` | Python-style printing (`print_bool`, `print_float`, `char_to_str`) |
| `int32.hpp` | Checked Int32 arithmetic with overflow detection |
| `type_traits.hpp` | `is_value_type` trait for value/reference semantics |
| `ranges.hpp` | `repeat_range`, `to_vector`, `from_range` utilities |
| `static_list.hpp` | `StaticList<T, N>` fixed-capacity container |
| `bigint.hpp` | `BigInt` arbitrary precision integer (GMP-based) |
| `container_ops.hpp` | Index normalization, `get_item`/`set_item`, list methods |
| `protocols.hpp` | `__len__`, `Sized`, `Sequence` concepts |
| `printing.hpp` | `ListPrinter`, `ValuePrinter` for collections |
| `system.hpp` | `Global<T>`, `time_*`, `sys_argv` |

Generated code requires C++23 (for `std::ranges` concepts).

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
| `Own[T]` | `T` (by value, for returns/params) |

## Supported Language Features

See `docs/LANGUAGE_FEATURES.md` for comprehensive documentation of all language features, including what's working, planned, and open for design.

## Development Guidelines

When implementing new features:
- If you encounter a hard problem or are unsure how to proceed, **ask first** before attempting a complex solution
- Check `TODO.md` for current priorities
- Run `uv run pytest -n auto` to verify no regressions after changes
- Update test snapshots with `uv run python tests/update_snapshots.py` when expected output changes intentionally

### Key Documentation Files

| File | Purpose | Policy |
|------|---------|--------|
| `docs/LANGUAGE_FEATURES.md` | Comprehensive language feature documentation | **Keep up-to-date** with any development |
| `CLAUDE.md` | Commands, architecture, quick reference | Update when adding major features |
| `README.md` | Quick start, build flags | Update when CLI changes |

**Before committing**: If code adds new features or changes behavior, update `docs/LANGUAGE_FEATURES.md` in the same commit to reflect the current state (Working/Planned/Open status).
