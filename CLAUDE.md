# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TurboPython (TPy) is a proof-of-concept compiler (tpyc) that translates Python to C++.

**Goals:**

1. **Performance** -- Low-latency compiled output with opt-in constraints for hot paths (e.g. `@noalloc`).
2. **Idiomatic Python** -- Standard Python should work out of the box, with minimal restrictions (e.g. type annotations on functions).
3. **Tooling-friendly** -- Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins.

## Commands

```bash
# Compile and run (debug build, default)
uv run tpyc -x examples/hello.py

# Compile and run (release build, optimized)
uv run tpyc -xO examples/hello.py

# Compile and run a snippet (write to file first, no heredocs)
uv run tpyc -x /tmp/agents/snippet.py

# Print generated C++ to stdout
uv run tpyc --dump-code /tmp/agents/snippet.py

# Compile to C++ and build binary
uv run tpyc -b examples/hello.py

# Compile to C++ only (output in __tpyc__/ next to source)
uv run tpyc examples/hello.py

# Compile to specific output directory
uv run tpyc examples/hello.py -o out/

# Verbose mode (-v for commands+timing, -vv for generated C++)
uv run tpyc -x examples/hello.py -vv

# Interactive REPL (auto-detects best backend: clang-repl > clang > gcc)
uv run tpyc -i

# Force a specific REPL backend
uv run tpyc -i --backend gcc

# Extra library search paths
uv run tpyc -x -L /my/libs examples/main.py

# Disable standard library
uv run tpyc -x --no-stdlib examples/main.py

# Install for development (uses uv package manager)
uv sync
```

## Agent Workflow

Always use `uv run` to invoke Python/tpyc (never bare `python` or `tpyc`). Use the Read/Grep/Glob tools instead of `cat`/`head`/`tail`/`grep`/`rg`/`find`.

**For snippets**, write to a file under `/tmp/agents/` (any filename or subdirectory) and run from there. Do NOT use heredocs (`<<EOF`) as they trigger permission prompts for multi-line commands.

To inspect generated C++:

```bash
# Write snippet to /tmp/agents/, then dump
uv run tpyc --dump-code /tmp/agents/scratch.py
```

To run a snippet quickly:

```bash
# Write snippet to /tmp/agents/, then run
uv run tpyc -x /tmp/agents/scratch.py
```

## Testing

Parallel execution (`-n auto`) is configured in `pyproject.toml` via `addopts`.

```bash
# Run all tests
uv run pytest

# Run fast compilation tests only (diagnostics, codegen)
uv run pytest tests/test_comp.py

# Run execution tests only (C++ build, run)
uv run pytest tests/test_exec.py

# Run CPython compatibility tests only
uv run pytest tests/test_cpy.py

# Run unit tests only (no C++ toolchain needed)
uv run pytest tpyc/

# Run tests for one case (pattern matching)
uv run pytest -k hello

# Update expected snapshots after intentional changes
uv run python tests/update_snapshots.py                    # all cases
uv run python tests/update_snapshots.py -k hello           # specific case
uv run python tests/update_snapshots.py --comp             # compilation tests only
uv run python tests/update_snapshots.py --exec             # execution tests only
uv run python tests/update_snapshots.py --cpy              # CPython compatibility checks (read-only)
uv run python tests/update_snapshots.py --comp -k hello    # specific case, comp only
```

**Important**: If a change would modify expected output for *existing* tests (not new tests you're adding), consult with the user before running `update_snapshots.py`. Explain what generated code will change and confirm the change is desired.

**Important**: When adding new test cases, always run `update_snapshots.py` without `--comp`/`--exec` flags (or run both separately) so that both compilation snapshots and execution snapshots (`output.txt`) are generated. Running only `--comp` will miss `output.txt`.

### Test Structure

There are two kinds of tests:

**Unit tests** live alongside the compiler source in `tpyc/` (no C++ toolchain needed):

```
tpyc/
├── test_compiler.py              # Compiler.from_source, BuildLayout paths
├── test_parse.py                 # RelativeImportKey encode/decode
└── codegen_cpp/
    └── test_context.py           # expand_cpp_template validation
```

**Snippet tests** (integration) live in `tests/` with snapshot-based expected output:

```
tests/
├── cases/                    # Main test suite
│   ├── array_span/           # Array, Span
│   ├── assert/               # assert statements, narrowing integration
│   ├── bool/                 # bool type and conversion
│   ├── builtins/             # Built-in functions, stdlib modules
│   ├── calls/                # Function calls, argument passing, @overload dispatch
│   ├── control_flow/         # if/else, for loops, break/continue, with statement
│   ├── defaults/             # Default argument values
│   ├── dict/                 # Dict type, subscript, methods
│   ├── set/                  # Set type, methods, operators, algebra
│   ├── enum/                 # Enum types, auto(), cross-module, comparison
│   ├── float/                # Float operations
│   ├── generics/             # Generic types, functions, inference, bounds
│   ├── globals/              # Global variables, name binding
│   ├── imports/              # Imports, relative imports, packages, shadowing
│   ├── inference/            # Type inference, variable type deduction
│   ├── inheritance/          # Class inheritance, super calls, method override
│   ├── int/                  # int, Int8-64, UInt8-64, BigInt
│   ├── int_default_override/ # --default-int flag behavior
│   ├── iterators/            # Iterators, range, __iter__/__next__, NativeIterable
│   ├── kwargs/               # Keyword arguments
│   ├── list/                 # List, container methods
│   ├── native/               # Native C++ interop (@native decorator)
│   ├── none_safety/          # Optional types, narrowing
│   ├── operators/            # Operators, coercion, assignment, subscript
│   ├── auto_move/            # Auto-move at last use, forwarding refs
│   ├── pointers/             # Ptr, Ptr[readonly[T]], Own, dangling references
│   ├── protocols/            # Protocol definition and implementation
│   ├── readonly/             # @readonly decorator, readonly[T] type modifier
│   ├── records/              # Class/record methods, dunder, staticmethod
│   ├── returns/              # Return value semantics
│   ├── str/                  # str, Char, string operations
│   ├── tuple/                # Tuple types, access, generics
│   └── union/                # Union types (A | B), variant codegen
│       ├── {name}/           # Success test
│       ├── error_{name}/     # Compilation error test
│       └── panic_{name}/     # Runtime panic test
│           ├── src/
│           │   ├── main.py
│           │   ├── native_types.hpp   # (optional) C++ type defs for native interop tests
│           │   └── native_types.cpp   # (optional) C++ stubs for native interop tests
│           ├── no_cpython.txt         # (optional) Skip CPython compatibility test (see note below)
│           └── expected/
│               ├── diag.txt           # Compiler diagnostics
│               ├── include/main.hpp   # Generated header (if compiles)
│               ├── src/main.cpp       # Generated source (if compiles)
│               └── output.txt         # Runtime output (or panic.txt)
├── conftest.py               # Pytest fixtures and shared utilities
├── test_comp.py              # Fast compilation tests (diagnostics, codegen)
├── test_exec.py              # Execution tests (C++ build, run, compare to output.txt)
├── test_cpy.py               # CPython compatibility tests (run with CPython, compare to output.txt)
└── update_snapshots.py       # Snapshot update utility
```

### Adding a New Test Case

1. Create test in the appropriate feature group under `tests/cases/<group>/`:
   - `{name}/` - normal tests that compile and run
   - `error_{name}/` - tests for compilation errors
   - `panic_{name}/` - tests for runtime panics
2. Add source file: `src/main.py` with a short comment (1-2 lines) at the very top explaining what the test covers -- test names alone are often not enough context
   - Prefer putting test logic inside functions (e.g. `def main(): ...` + `main()`) rather than as top-level statements. Top-level codegen differs from function codegen (globals use pointer slots, different variable model), so use top-level statements only when specifically testing global variable behavior.
3. Add `# tpyc:` annotations on lines that test specific compiler behavior. Multiple annotations per line are supported (e.g. `# tpyc: warning(/a/) warning(/b/)`):
   - `# tpyc: ok` - line should compile without error or warning
   - `# tpyc: error(/regex/)` - line should produce an error matching the regex
   - `# tpyc: warning(/regex/)` - line should produce a warning matching the regex
   - `# tpyc: type(TypeName)` - assert the compiler-inferred type of the variable declared on this line (e.g. `s = "hello"  # tpyc: type(StrView)`). Supports regex with `/pattern/` syntax. Validated in `test_comp` only (not in update mode).
   - `# tpyc: non_null(var)` - assert that `var` is proven non-null at this ptr dereference (skips `deref_check`). Validated in `test_comp` only.
   - `# tpyc: nullable(var)` - assert that `var` is NOT proven non-null at this ptr dereference (uses `deref_check`). Validated in `test_comp` only.
4. Run `uv run python tests/update_snapshots.py {name}` to generate expected outputs
5. Run `uv run pytest -k {name}` to verify (parallel execution is automatic via `addopts`)

**CPython compatibility:** Avoid adding `no_cpython.txt` unless absolutely necessary. Most tests can be made CPython-compatible by adding `__init__` methods (CPython doesn't create instance attributes from type annotations alone) and using `lib/cpy/` stubs. The `lib/cpy/tpy/` package provides CPython implementations of TPy types (`Own`, `nocopy`, `UninitHeapStorage`, etc.). Only skip CPython when the test truly depends on C++-only behavior (e.g. `@native` interop).

Per-case compiler options can be set with optional `options.json` at the case root.
Currently supported:

```json
{
  "default_int": "Int32 | Int64 | BigInt"
}
```

## Code Style

For `tpyc/` compiler modules:
- Imports at top of file only (avoid internal imports unless unavoidable)
- Use type annotations
- **ASCII only** in source code and comments -- no Unicode arrows (`→`), em dashes (`—`), or other non-ASCII characters. Use `->` and `--` instead.

For test snippets (`tests/cases/*/src/main.py`):
- Prefer plain literals (`1`, `"hello"`, `{1, 2}`) over explicit constructors (`Int32(1)`, `{"a": Int32(1)}`) and variable type annotations (`x: list[Int32] = ...`) when the compiler can infer the type from context. Only use explicit constructors or annotations when the test is specifically exercising constructor syntax, type annotations, or a case where inference would be ambiguous.

## Architecture

The compiler follows a 4-stage pipeline:

```
TurboPython Source (.py) -> Parser -> Semantic Analyzer -> Code Generator -> C++ (.hpp/.cpp)
```

### Core Modules (`tpyc/`)

| Module | Purpose |
|--------|---------|
| `cli.py` | CLI entry point, argument parsing, error handling |
| `parse/` | Parser package: `parser.py` (AST builder using Python's `ast`), `nodes.py` (TurboPython AST node definitions), `imports.py` (import resolution helpers) |
| `typesys.py` | Type definitions (Int32, BigInt, Float, bool, Void, Str, Char, Record, Ptr, Own, Optional, List, Array, Span, Tuple) and TypeRegistry |
| `sema/` | Multi-pass semantic analysis (see below) |
| `codegen_cpp/` | C++ code generation (see below) |
| `compiler.py` | Multi-module orchestration: discovery, dependency resolution, compilation order |
| `modules/` | Built-in function/type definitions and module resolution (see below) |
| `namespace.py` | Unified namespace system for name resolution |
| `coercions.py` | Type coercion rules (numeric widening, etc.) |
| `liveness.py` | Last-use liveness analysis for auto-move optimization |
| `prescan.py` | Pre-scan utilities for function bodies |
| `dump_types.py` | Type documentation generation (`--print-types`) |
| `repl.py` | Interactive REPL implementation |
| `repl_backends.py` | REPL execution backends (clang-repl JIT, g++/clang++ compile) |

### Semantic Analysis (`tpyc/sema/`)

| Module | Purpose |
|--------|---------|
| `analyzer.py` | Main semantic analyzer orchestrator |
| `context.py` | Analysis context and state |
| `statements.py` | Statement analysis |
| `expressions.py` | Expression analysis and type checking |
| `calls.py` | Function call resolution and overload matching |
| `methods.py` | Method resolution and binding |
| `operators.py` | Operator type checking |
| `type_ops.py` | Type operations (coercion, compatibility) |
| `compatibility.py` | Type compatibility checking |
| `overloads.py` | Overload resolution |
| `registration.py` | Symbol registration and scope binding |
| `scope_tracker.py` | Variable scope tracking |
| `init_tracker.py` | Variable initialization tracking |
| `local_deduction.py` | Type deduction for reassigned variables |
| `list_literals.py` | List literal type inference |
| `numeric_lattice.py` | Numeric type lattice for inference |
| `narrowing.py` | Optional narrowing tracker (None-safety flow analysis) |
| `protocols.py` | Protocol implementation checking |
| `diagnostics.py` | Error and warning message formatting |

### Code Generation (`tpyc/codegen_cpp/`)

| Module | Purpose |
|--------|---------|
| `generator.py` | Main code generator orchestrator |
| `context.py` | Code generation context (scopes, pointer tracking) |
| `expressions.py` | Expression code generation |
| `statements.py` | Statement code generation |
| `functions.py` | Function code generation |
| `records.py` | Class/record code generation |
| `protocols.py` | Protocol code generation |
| `builtins.py` | Built-in function codegen |
| `types.py` | Type mapping (TurboPython -> C++) |
| `type_resolution.py` | Runtime type resolution for generics |

### Built-in Modules (`tpyc/modules/`)

| Module | Purpose |
|--------|---------|
| `builtins.py` | Built-in types (`list`, `dict`, `str`, `int`, etc.) and functions with special handling (`print`, `isinstance`, `pow`, `round`, `divmod`, `range`, `iter`) |
| `resolver.py` | User module resolution |
| `helpers.py` | Helper utilities for type definitions |
| `tpy.py` | TurboPython-specific types (`Int32`, `Array`, `Span`, etc.) and `copy`/`try_parse` functions. Protocols defined in `lib/tpy/tpy/__init__.py` |
| `extern.py` | `tpy.extern` module -- native global variable declarations |
| `dataclasses.py` | `dataclasses` module (decorator, frozen, order, etc.) |
| `enum.py` | `enum` module (Enum, auto(), comparison) |

### Runtime (`runtime/`)

The C++ runtime is organized as a modular header library in `runtime/cpp/include/tpy/`:

| Header | Purpose |
|--------|---------|
| `tpy.hpp` | Main header - includes all modules |
| `core.hpp` | `tpy_panic`, `deref_check`, exception sentinels (`StopIteration`, etc.) |
| `format.hpp` | Python-style printing (`print_bool`, `print_float`, `char_to_str`) |
| `fixed_int.hpp` | Template-based checked arithmetic for all fixed-width integer types |
| `type_traits.hpp` | `is_value_type` trait for value/reference semantics |
| `ranges.hpp` | `repeat_range`, `to_vector`, `from_range` utilities |
| `range.hpp` | `Range<T>` Python-style range with upfront overflow checking |
| `bigint.hpp` | `BigInt` arbitrary precision integer (custom runtime implementation) |
| `container_ops.hpp` | Index normalization, `get_item`/`set_item`, list methods |
| `ordered_map.hpp` | `tpy::ordered_map<K,V>` insertion-order-preserving hash map |
| `ordered_set.hpp` | `tpy::ordered_set<T>` insertion-order-preserving hash set |
| `dict_ops.hpp` | Dict helpers: `dict_get`, `dict_pop`, `DictPrinter` |
| `set_ops.hpp` | Set helpers: `set_remove`, `set_pop`, `set_union`, `SetPrinter` |
| `dunder.hpp` | Protocol free functions (`__len__`, `__getitem__`, `__setitem__`, `__delitem__`) |
| `protocols.hpp` | `NativeIterable`, `NativeRangeConstructible` marker concepts (structural protocol concepts are compiler-generated) |
| `printing.hpp` | `ListPrinter`, `ValuePrinter` for collections |
| `uninit_array_storage.hpp` | `UninitArrayStorage<T, N>` inline uninitialized storage |
| `uninit_heap_storage.hpp` | `UninitHeapStorage<T>` heap-allocated uninitialized storage |
| `system.hpp` | `time_*`, `sys_argv` |
| `builtins.hpp` | Runtime support for `round()`, `divmod()`, and related builtins |
| `enum.hpp` | `EnumUtil<T>` primary template for enum name/value/members support |
| `dynamic.hpp` | `Adapter<Base, T>` and `RefAdapter<Base, T>` for `@dynamic` protocols |
| `iterable_ops.hpp` | Non-range overloads for container operations (`list_extend`, `str_join`, etc.) |
| `generator.hpp` | `generator_wrapper` and `make_generator` for generator expressions |
| `span_iter.hpp` | `tpy::SpanIter<T>` lightweight iterator over contiguous span |
| `variant_ref.hpp` | `to_ptr_variant`, `to_const_ptr_variant`, `to_value_variant` for non-value union two-layer repr |
| `with_guard.hpp` | `tpy::WithGuard<T>` RAII guard for `with` statement context managers |

Generated code requires C++23 (for `std::ranges` concepts).

### Libraries (`lib/`)

Library search roots and CPython stubs:

| Directory | Purpose |
|-----------|---------|
| `tpy/` | Single search root for all library modules |
| `tpy/tpy/` | TPy package: `__init__.py` defines protocols (`Comparable`, `Truthy`, `ValueType`, etc.). Implicitly compiled. |
| `tpy/tplib/` | TPy standard library: `Box[T]`, custom collections |
| `tpy/typing.py` | `typing` protocols (`Sized`, `Sequence`, etc.). Implicitly compiled. |
| `tpy/builtins.py` | Builtin functions (`len`, `repr`, `hash`, `chr`, `ord`, `abs`, `min`, `max`). Implicitly compiled. |
| `tpy/math.py`, `time.py`, `sys.py`, `bisect.py` | Python stdlib analogs |
| `cpy/tpy/` | CPython stubs ONLY (not seen by tpyc): `Int32`, `Ptr`, `Array`, decorators; submodules: `mem`, `unsafe` |
| `cpy/tplib` | Symlink to `tpy/tplib/` so CPython tests can find tplib |

**Compiler search order** (first match wins):
1. Entry point directory (user modules)
2. `-L` paths (user-specified, in order)
3. `lib/tpy/` (tplib, stdlib modules, tpy protocols)

CPython tests (`test_cpy.py`) use PYTHONPATH `lib/cpy/:src_dir`.

## Performance Profiles (Planned)

TurboPython will support performance profiles applied at function, class, or module level:

- **Default**: Standard Python constructs allowed (`list`, `str`, etc.)
- **`@noalloc`**: No heap allocation - for hot paths and real-time code

Note: `@noalloc` is parsed but not yet enforced. See `docs/LANGUAGE_FEATURES.md` for the design.

## Current Limitations

The compiler is a proof-of-concept. Not yet implemented:
- General exception handling (`try`/`except`/`raise` with C++ exceptions). `@error_return(E)` provides zero-cost `try`/`except` for control-flow errors via `std::expected`.
- `async`/`await`, `lambda`, `yield`
- Slice step (`items[::2]`, `items[::-1]`)

## Type Mappings

| TurboPython | C++ |
|-------------|-----|
| `int` | `tpy::BigInt` (arbitrary precision) |
| `Int8/16/32/64` | `int8_t/int16_t/int32_t/int64_t` |
| `UInt8/16/32/64` | `uint8_t/uint16_t/uint32_t/uint64_t` |
| `float` / `Float64` | `double` (IEEE 754) |
| `Float32` | `float` (IEEE 754 single precision) |
| `bool` | `bool` |
| `str` | `std::string` (parameters: `std::string_view`) |
| `String` | `std::string` (parameters: `const std::string&`) |
| `StrView` | `std::string_view` |
| `Char` | `char` |
| `None` | `void` (return type) |
| `Optional[T]` | `std::optional<T>` |
| `tuple[T1, T2, ...]` | `std::tuple<T1, T2, ...>` |
| `dict[K, V]` | `tpy::ordered_map<K, V>` |
| `set[T]` | `tpy::ordered_set<T>` |
| `list[T]` | `std::vector<T>` |
| `Array[T, N]` | `std::array<T, N>` |
| `Span[T]` | `std::span<T>` |
| `Span[readonly[T]]` | `std::span<const T>` |
| `SpanIter[T]` | `tpy::SpanIter<T>` |
| `A \| B` (value types) | `std::variant<A, B>` |
| `A \| B` (non-value, params/returns/locals) | `std::variant<A*, B*>` (pointer variant) |
| `A \| B` (non-value, fields/containers) | `std::variant<A, B>` (value variant) |
| `Ptr[T]` | `T*` |
| `Ptr[readonly[T]]` | `const T*` |
| `Own[T]` | `T` (by value, for returns/params) |

## Supported Language Features

See `docs/LANGUAGE_FEATURES.md` for comprehensive documentation of all language features, including what's working, planned, and open for design.

## Development Guidelines

When implementing new features:
- **Never commit, amend, or stage without explicit user request.** Do not run `git commit`, `git commit --amend`, or `git add` unless the user explicitly asks for it.
- **Never make design decisions autonomously.** If during implementation you discover that the plan needs to change (e.g., a new concept, a split in behavior, a workaround for an unforeseen constraint), **stop and consult the user** before proceeding. Do not invent new design concepts or alter the agreed-upon design without explicit approval.
- If you encounter a hard problem or are unsure how to proceed, **ask first** before attempting a complex solution
- **Do NOT use heredocs (`<<EOF`)** in Bash commands -- they trigger permission prompts in Claude Code. Write snippets to files under `/tmp/agents/` instead.
- Check `TODO.md` for current priorities
- Run `uv run pytest` to verify no regressions after changes
- Update test snapshots with `uv run python tests/update_snapshots.py` when expected output changes intentionally
- **Add tests** when adding new features or making changes that could affect generated code. If proper tests don't already exist or the functionality isn't covered, add tests that check the happy path, errors/warnings, edge cases, and prevent future regressions.

### Key Documentation Files

| File | Purpose | Policy |
|------|---------|--------|
| `docs/LANGUAGE_FEATURES.md` | Comprehensive language feature documentation | **Keep up-to-date** with any development |
| `CLAUDE.md` | Commands, architecture, quick reference | Update when adding major features |
| `README.md` | Quick start, build flags | Update when CLI changes |

**Before committing**: If code adds new features or changes behavior, update `docs/LANGUAGE_FEATURES.md` in the same commit to reflect the current state (Working/Planned/Open status).
