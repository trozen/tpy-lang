# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TurboPython (TPy) is a proof-of-concept toolchain that translates Python to C++.
It ships two CLIs that share the same argument grammar, differing only in their default action:

- **`tpy`** -- user-facing runner. Bare `tpy` drops into a REPL; `tpy foo.py` runs the program.
- **`tpyc`** -- compiler front-end. `tpyc foo.py` emits `.hpp`/`.cpp` in `__tpyc__/` without running.

**Goals:**

1. **Performance** -- Low-latency compiled output with opt-in constraints for hot paths (e.g. `@noalloc`).
2. **Idiomatic Python** -- Standard Python should work out of the box, with minimal restrictions (e.g. type annotations on functions).
3. **Tooling-friendly** -- Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins.

## Commands

```bash
# Run a program (debug build, default)
uv run tpy examples/hello.py

# Run a program (release build, optimized)
uv run tpy -O examples/hello.py

# Run a snippet (write to file first, no heredocs)
uv run tpy /tmp/agents/snippet.py

# Print generated C++ to stdout
uv run tpy --dump-code /tmp/agents/snippet.py

# Compile and build binary (no run)
uv run tpy -b examples/hello.py

# Compile to C++ only (output in __tpyc__/ next to source)
uv run tpyc examples/hello.py

# Compile to specific output directory
uv run tpyc examples/hello.py -o out/

# Verbose mode (-v for commands+timing, -vv for generated C++)
uv run tpy examples/hello.py -vv

# Run inline code
uv run tpy -c "print(1 + 2)"

# Interactive REPL (auto-detects best backend: clang-repl > clang > gcc > zig)
uv run tpy

# Force a specific C++ compiler (REPL)
uv run tpy --cxx gcc

# Extra library search paths
uv run tpy -L /my/libs examples/main.py

# Disable standard library
uv run tpy --no-stdlib examples/main.py

# Build flags: ccache control, parallel jobs, CMake output
uv run tpy --no-ccache examples/hello.py
uv run tpy -j4 examples/hello.py

# Install for development (uses uv package manager)
uv sync
```

## Agent Workflow

Always use `uv run` to invoke Python/tpy (never bare `python` or `tpy`). Use the Read/Grep/Glob tools instead of `cat`/`head`/`tail`/`grep`/`rg`/`find`.

**For snippets**, write to a file under `/tmp/agents/` (any filename or subdirectory) and run from there. Do NOT use heredocs (`<<EOF`) as they trigger permission prompts for multi-line commands.

**For testing**, see the "Agent testing workflow" section below. Key rules: run only targeted test subsets during development (`-k pattern`), run the full suite only once at the end, and never start a new test run while another is still running.

To inspect generated C++:

```bash
# Write snippet to /tmp/agents/, then dump
uv run tpy --dump-code /tmp/agents/scratch.py
```

To run a snippet quickly:

```bash
# Write snippet to /tmp/agents/, then run
uv run tpy /tmp/agents/scratch.py
```

## Testing

Each folder under `tests/cases/` becomes one parametrized item of `test_case` with three phases: **comp** (compile + diagnostics + snapshot check + annotation validation), **exec** (build + run C++), and **cpy** (run with CPython, compare to `output.txt`).

The exec and cpy phases auto-skip when the compiler produces byte-identical generated code AND the recorded fingerprints match the current sources. Fingerprints split into:

- **Session-level** (`tests/.session_fingerprints.json`, single file): hash of `runtime/cpp/include/**` and hash of `lib/cpy/tpy/**`. When either changes, all per-case skips are invalidated by a single-file diff.
- **Per-case** (`tests/cases/<case>/expected/.fingerprints`, optional keys): hash of the case's `main.py` (`main`) and hash of any hand-written C++ companion files in `src/` (`extra_src`, e.g. native-interop tests). Cases with neither companions nor a CPython phase end up with no file.

This makes typical iterative work nearly free for cases the change didn't touch -- only cases whose generated code changes get rebuilt and re-run.

Parallel execution (`-n auto`) is configured in `pyproject.toml` via `addopts`. Worker count auto-caps to the cgroup v2 CPU quota (so `-n auto` in a Docker container with `--cpus=N` gets N workers, not the host's `os.cpu_count()`).

The exec phase reuses pre-compiled stdlib object files via a content-addressed persistent cache at `/tmp/tpyc-cache/stdlib-objs/<key>/` (override root with `TPYC_SHARED_CACHE_DIR`). The cache key invalidates when `runtime/cpp/include/`, `lib/tpy/`, or C++ build config changes; old keys remain on disk and are not auto-GC'd, so periodically prune `/tmp/tpyc-cache/stdlib-objs/` if it grows.

### Test commands

```bash
# Run all tests (full suite -- exec/cpy auto-skip per case when nothing relevant changed)
uv run pytest

# Force exec + cpy phases unconditionally (CI-style full verification)
uv run pytest --force-exec

# Run unit tests only (no C++ toolchain needed)
uv run pytest tpyc/

# Run tests for specific cases (pattern matching)
uv run pytest -k hello
uv run pytest -k "bool_type or bool_conversion"

# Update expected snapshots + fingerprints after intentional changes
uv run python tests/update_snapshots.py                    # all cases
uv run python tests/update_snapshots.py -k hello           # specific case(s)
```

`UPDATE_EXPECTED=1` (set by `update_snapshots.py`) implies `--force-exec` so output.txt, panic.txt, generated code, and `.fingerprints` are all regenerated in one pass.

### Agent testing workflow

**CPU awareness**: Never start a new test run while a previous one is still running. Either wait for it to finish, or kill it first (`pkill -f pytest`). Running concurrent test suites saturates all cores and makes everything slower for all agents and the user.

**During development**, run targeted subsets -- the new tests you are adding, or tests in categories likely affected by your changes. Use `-k pattern` to select specific tests:

```bash
# Test a specific feature area you changed
uv run pytest -k "bool_type or bool_conversion"

# Test new cases you just added
uv run pytest -k my_new_test_name
```

**Final verification**: Run the full suite once, after all changes are done, before reporting work as complete:

```bash
uv run pytest
```

If you want to be paranoid and re-verify runtime output for every case (ignoring fingerprint-based skips), use `--force-exec`.

### Snapshot policy

**Important**: If a change would modify expected output for *existing* tests (not new tests you're adding), consult with the user before running `update_snapshots.py`. Explain what generated code will change and confirm the change is desired.

**Important**: When adding new test cases, run `update_snapshots.py -k {name}` so that all expected files (`diag.txt`, generated `.hpp`/`.cpp`, `output.txt`, and `.fingerprints`) are generated together.

### Test Structure

There are two kinds of tests:

**Unit tests** live alongside the compiler source in `tpyc/` (no C++ toolchain needed):

```
tpyc/
├── test_compiler.py              # Compiler.from_source, BuildLayout paths
├── test_parse.py                 # RelativeImportKey encode/decode
├── test_dump_types.py            # Type documentation generation
├── test_macro_loader.py          # Macro module loading
├── test_union_types.py           # Union type handling
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
│   ├── bytes/                # bytes, bytearray, BytesView types
│   ├── calls/                # Function calls, argument passing, @overload dispatch
│   ├── control_flow/         # if/else, for loops, break/continue, with statement
│   ├── defaults/             # Default argument values
│   ├── dict/                 # Dict type, subscript, methods
│   ├── set/                  # Set type, methods, operators, algebra
│   ├── enum/                 # Enum types, auto(), cross-module, comparison
│   ├── error_return/         # @error_return(E) zero-cost error handling via std::expected
│   ├── exceptions/           # try/except/finally, raise, exception types
│   ├── float/                # Float operations
│   ├── generic_bounded_*/    # Bounded generics (basic, func_user_protocol, func_user_type, generic_protocol, nested_type, user_protocol)
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
│   ├── macros/               # Compile-time macro modules, @dataclass macro, AST building
│   ├── match/                # match/case pattern matching statements
│   ├── native/               # Native C++ interop (@native decorator)
│   ├── none_safety/          # Optional types, narrowing
│   ├── operators/            # Operators, coercion, assignment, subscript
│   ├── auto_move/            # Auto-move at last use, forwarding refs
│   ├── nested_def/           # Nested function definitions, captures, nonlocal
│   ├── pointers/             # Ptr, Ptr[readonly[T]], Own, dangling references
│   ├── protocols/            # Protocol definition and implementation
│   ├── readonly/             # @readonly decorator, readonly[T] type modifier
│   ├── records/              # Class/record methods, dunder, staticmethod
│   ├── returns/              # Return value semantics
│   ├── str/                  # str, Char, string operations
│   ├── tplib/                # TPy standard library modules (Box, JSON, etc.)
│   ├── tuple/                # Tuple types, access, generics
│   ├── typed_dict/           # TypedDict definitions, field validation, **kwargs
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
│               ├── output.txt         # Runtime output (or panic.txt)
│               └── .fingerprints      # (optional) per-case input hashes for auto-skip
├── .session_fingerprints.json # Session-level input hashes (runtime + cpy stubs)
├── conftest.py               # Pytest fixtures and shared utilities
├── test_case.py              # Unified test (compile + optional exec + optional CPython)
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
   - `# tpyc: type(TypeName)` - assert the compiler-inferred type of the variable declared on this line (e.g. `s = "hello"  # tpyc: type(StrView)`). Supports regex with `/pattern/` syntax. Validated in the comp phase only (not in update mode).
   - `# tpyc: non_null(var)` - assert that `var` is proven non-null at this ptr dereference (skips `deref_check`). Validated in the comp phase only.
   - `# tpyc: nullable(var)` - assert that `var` is NOT proven non-null at this ptr dereference (uses `deref_check`). Validated in the comp phase only.
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

## Terminology

TurboPython distinguishes **value types** (primitives, `bool`, `Char`, views like `str`/`Span[T]`, tuples, user types implementing `ValueType` -- copied when passed around) from **reference types** (classes/records, `list`, `dict`, `set`, `bytes`/`bytearray` -- passed by reference to functions, stored inline in fields and containers). Always use "reference types" for the latter, never "object types". `Own[T]` means ownership transfer (move), not heap allocation.

## Architecture

The compiler follows a multi-stage pipeline:

```
TurboPython Source (.py) -> Parser -> Semantic Analyzer -> Code Generator -> C++ (.hpp/.cpp) -> C++ Compiler -> Binary
```

### Compilation Pipeline

**Multi-module orchestration** (`compiler.py`): The compiler recursively discovers modules by following imports from the entry point, topologically sorts them (Kahn's algorithm), and processes each module through sema and codegen in dependency order. Implicit stdlib modules (`typing`, `tpy`, `builtins`) are always compiled first. Each module's exports (functions, records, protocols, enums, variables) are registered into a shared registry before analyzing downstream modules.

**Parser** (`parse/parser.py`): Single-pass walk over Python's `ast` module output. Scans `# tpy:` directives, converts Python AST to TurboPython AST (`TpyModule`), collects imports, and resolves type annotations against local definitions. No cross-module resolution -- that's deferred to sema.

**Semantic analysis** (`sema/analyzer.py:analyze()`): Two-phase design within each module:
- *Phase 1 -- Registration then analysis*: Types must be registered before they can be referenced. The pass order is: imports -> records & enums -> protocols -> inheritance validation -> value-type validation -> type aliases -> functions -> globals -> top-level statements -> record method bodies -> function bodies. Each body-analysis pass (methods, functions) runs pre-scan for variable hoisting, then full type-checking.
- *Phase 2 -- Call-graph fixpoint*: After all bodies are analyzed, mutation facts are propagated transitively through the intra-module call graph (`mutation_propagation.py`), `is_readonly` is inferred for methods, and deferred borrow checks are resolved.

**Code generation** (`codegen_cpp/generator.py`): Single pass producing `.hpp` and `.cpp` with careful emit ordering to satisfy C++ forward-declaration constraints: concepts before records, forward decls before full definitions, templates inline in headers, non-template functions in `.cpp`.

**C++ build** (`compiler.py` / `cli.py`): After codegen, the CLI generates a CMake sources file or invokes the C++ compiler directly. Object files are compiled in parallel (`-j`), then linked. Optional ccache integration.

### Core Modules (`tpyc/`)

| Module | Purpose |
|--------|---------|
| `cli.py` | CLI entry point, argument parsing, error handling |
| `parse/` | Parser package: `parser.py` (AST builder using Python's `ast`, `FragmentParser` subclass for macro source fragments), `nodes.py` (TurboPython AST node definitions), `imports.py` (import resolution helpers) |
| `typesys.py` | Type definitions (Int32, BigInt, Float, bool, Void, Str, Char, Bytes, ByteArray, BytesView, Record, Ptr, Own, Optional, List, Array, Span, Tuple) and TypeRegistry |
| `type_resolver.py` | `TypeResolver` class: resolves parser-emitted `TypeRefNode` (unresolved type-reference AST nodes from the walker) to `TpyType`. Owns primitive lookup, registered-type lookup, generic instantiation, and structural-wrapper construction. Parser constructs one instance and attaches it to `TpyModule.resolver`; sema delegates via `resolver.resolve(ref, scope, pending_alias=...)` |
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
| `qnames.py` | Qualified name constants for compiler-known types, decorators, and protocols |
| `cycle_detection.py` | Type dependency cycle detection for mutual recursion (DFS on type-reference graph) |
| `install_docs.py` | `--install-agent-docs` implementation (copies agent docs to downstream projects) |
| `macro_api.py` | Public API for compile-time macro modules: metadata (`ClassInfo`, `FieldInfo`, `TypeInfo`), AST builder (`ast`), type builder (`types`), type aliases (`Expr`, `Stmt`, `Function`, `Type`), source-based authoring (`ast.quote`, `ast.quote_expr`, `ast.quote_fun`, `cls.add_method_from_source`), f-string decomposition (`MacroArg.as_fstring()`, `MacroFStringPart`) |
| `macro_loader.py` | Discovers and loads `# tpy: macro_module` files via CPython at compile time. Supports macro-to-macro imports from lib search paths |
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
| `flow_facts.py` | Immutable flow-sensitive state snapshots (assignment, termination, narrowing, consumed vars) |
| `match.py` | Semantic analysis for match/case statements and pattern matching |
| `mutation_propagation.py` | Transitive parameter mutation inference through intra-module call graph |
| `value_range.py` | Integer value-range tracking [lo, hi] for bounds-check and div-zero elision |
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
| `match.py` | C++ codegen for match/case statements |
| `gen_generators.py` | Generator function codegen (yield -> state machine structs) |
| `string_dispatch.py` | String dispatch optimization for match/case and enum lookup |

### Built-in Modules (`tpyc/modules/`)

All builtin types, functions, and protocols are defined in `.py` stubs under `lib/tpy/`. The `tpyc/modules/` package provides resolution helpers and constant tables used by sema and codegen. (Generic type factories and per-qname behavior live on `tpyc.type_def_registry.TypeDef`; see `docs/ARCHITECTURE.md` for the nominal/structural split and TypeDef registry design.)

| Module | Purpose |
|--------|---------|
| `defs.py` | Data classes (`ParamDef`, `MethodDef`), dunder C++ templates, operator-to-method tables |
| `registry.py` | Builtin module name set, type object lookup from factories |
| `resolver.py` | User module resolution |
| `type_resolution.py` | Method resolution, iterator/span helpers, extends-arg parsing |

### Runtime (`runtime/`)

The C++ runtime is organized as a modular header library in `runtime/cpp/include/tpy/`:

| Header | Purpose |
|--------|---------|
| `tpy.hpp` | Main header - includes all modules |
| `core.hpp` | `tpy_panic`, `deref_check`, exception sentinels (`StopIteration`, etc.) |
| `format.hpp` | Python-style printing (`print_bool`, `print_float`, `char_to_str`) |
| `fixed_int.hpp` | Template-based checked arithmetic for all fixed-width integer types |
| `type_traits.hpp` | `is_value_type` trait distinguishing value types from reference types |
| `ranges.hpp` | `repeat_range`, `to_vector`, `from_range`, `collect`, `construct` utilities |
| `range.hpp` | `Range<T>` Python-style range with upfront overflow checking |
| `bigint.hpp` | `BigInt` arbitrary precision integer (custom runtime implementation) |
| `container_ops.hpp` | Index normalization, `get_item`/`set_item`, list methods |
| `ordered_map.hpp` | `tpy::ordered_map<K,V>` insertion-order-preserving hash map |
| `ordered_set.hpp` | `tpy::ordered_set<T>` insertion-order-preserving hash set |
| `dict_ops.hpp` | Dict helpers: `dict_get`, `dict_pop`, `DictPrinter` |
| `set_ops.hpp` | Set helpers: `set_remove`, `set_pop`, `set_union`, `SetPrinter` |
| `dunder.hpp` | Protocol free functions (`__len__`, `__getitem__`, `__setitem__`, `__delitem__`) |
| `protocols.hpp` | `NativeIterable`, `NativeRangeConstructible`, `AnyFixedInt`/`Signed`/`Unsigned` marker concepts (structural protocol concepts are compiler-generated) |
| `printing.hpp` | `ListPrinter`, `ValuePrinter` for collections |
| `uninit_array_storage.hpp` | `UninitArrayStorage<T, N>` inline uninitialized storage |
| `uninit_heap_storage.hpp` | `UninitHeapStorage<T>` heap-allocated uninitialized storage |
| `system.hpp` | `time_*`, `sys_argv` |
| `builtins.hpp` | Runtime support for `round()`, `divmod()`, and related builtins |
| `enum.hpp` | `EnumUtil<T>` primary template for enum name/value/members support |
| `dynamic.hpp` | `Adapter<Base, T>` and `RefAdapter<Base, T>` for `@dynamic` protocols |
| `iterable_ops.hpp` | Non-range overloads for container operations (`list_extend`, `str_join`, etc.) |
| `generator.hpp` | `generator_wrapper` and `make_generator` for generator expressions |
| `itertools.hpp` | `enumerate()`, `zip()`, `reversed()`, `map()`, `filter()` iterator builtins |
| `next_iter.hpp` | `next_iter_mixin` CRTP adapter: adds `begin()`/`end()` to any `__next__()`-based iterator |
| `span_iter.hpp` | `tpy::SpanIter<T>` lightweight iterator over contiguous span |
| `variant_ref.hpp` | `to_ptr_variant`, `to_const_ptr_variant`, `to_value_variant` for non-value union two-layer repr |
| `bytes_ops.hpp` | Python-style bytes operations: printing, encode/decode, search helpers, `bytes_literal()` static storage |
| `copy_iter.hpp` | `CopyIter<I>` iterator adapter that copies elements from a borrowing iterator |
| `own_iter.hpp` | `OwnIter<C>` drain iterator for consuming iteration over heap-backed containers |
| `varargs.hpp` | `varargs<T>` dual-mode span for `*args` (direct storage or pointer indirection) |
| `slice.hpp` | `BasicSlice` (start, stop) and `Slice` (start, stop, step) types |
| `file.hpp` | `TextFile`, `BinaryFile` for `open()` builtin, `FileFlags` mode parsing |

Generated code requires C++23 (for `std::ranges` concepts) and uses the GCC statement expression extension (`({ ...; value; })`) wherever codegen needs expression-level locals: `@error_return` unwrapping, list/dict/set/array comprehensions, chained comparisons with complex intermediates, and the `x in (a, b, c)` membership form with a complex LHS. This extension is supported by GCC, Clang, and all LLVM-based compilers (Intel ICX, ARM armclang, IBM Open XL). It is not supported by MSVC.

### Libraries (`lib/`)

Library search roots and CPython stubs:

| Directory | Purpose |
|-----------|---------|
| `tpy/` | Single search root for all library modules |
| `tpy/tpy/` | TPy package: `__init__.py` re-exports from `_core/` and `_bootstrap/`. Implicitly compiled. |
| `tpy/tpy/_bootstrap/` | Bootstrap layer: `_extern.py` (native, cpp_template, etc. -- compiler intrinsic stubs), `_decorators.py` (readonly, pure, inline, Own, etc. -- decorator stubs) |
| `tpy/tpy/_typing/` | Typing layer: `__init__.py` (Protocol, Self, Sized, Sequence, Iterator, Iterable) |
| `tpy/tpy/_core/` | Core types: `_types.py` (protocols, primitives), `_containers.py` (Span, Array, Ptr), `_functions.py` (span, deref, copy, copy_iter, own_iter, try_parse), `_bytes_view.py` (BytesView) |
| `tpy/tpy/_builtins/` | Builtin types and functions: `_types.py` (bool, int, float, str), `_funcs.py` (len, hash, print, isinstance, etc.), `_list.py`, `_dict.py`, `_set.py`, `_range.py`, `_bytes.py`, `_exceptions.py`, `_io.py` (TextIO, BinaryIO, open, open_text, open_binary) |
| `tpy/tplib/` | TPy standard library: `Box[T]` (`box.py`), `ArrayList[T, N]` (`array_list.py`), `FixStr[N]` (`fix_str.py`), JSON library |
| `tpy/tplib/json/` | JSON library: `parser.py` (JsonToken, JsonReader), `writer.py` (JsonWriter), `model.py` (@model macro -- compile-time only) |
| `tpy/typing.py` | `typing` protocols (`Sized`, `Sequence`, etc.). Re-exports from `tpy._typing`. Implicitly compiled. |
| `tpy/builtins.py` | Re-export facade for `tpy._builtins`. Implicitly compiled. |
| `tpy/tpy/extern.py` | Re-exports native, export, cpp_template, native_global from `_bootstrap` |
| `tpy/tpy/mem.py` | Memory management: `UninitArrayStorage[T, N]` inline uninitialized storage |
| `tpy/tpy/unsafe.py` | Unsafe operations: `unsafe_ptr()`, `unsafe_cast()` |
| `tpy/tpy/version.py` | Version/implementation identification: `__version__`, `version_info`, `is_compiled`. Values come from `_version.py` (a `# tpy: macro_module` that reads `tpyc.__version__` at compile time). Not re-exported through `tpy/__init__.py` because native_module facades don't propagate variables (see TODO). |
| `tpy/_macro_helpers.py` | Shared macro helpers: `build_init`, `build_eq`, `build_repr`, `build_hash`, `build_order` (compile-time only) |
| `tpy/math.py`, `time.py`, `sys.py`, `bisect.py`, `dataclasses.py`, `enum.py`, `random.py`, `struct.py` | Python stdlib analogs |
| `cpy/tpy/` | CPython stubs ONLY (not seen by tpyc): `Int32`, `Ptr`, `Array`, decorators; submodules: `mem`, `unsafe` |
| `cpy/tpyc/` | CPython stub for tpyc: `__init__.py` exposes `__version__` + `VERSION_INFO` read from the installed distribution via `importlib.metadata`; `macro_api.py` raises ImportError (macro modules run at compile time only, not available under CPython) |
| `cpy/tplib` | Symlink to `tpy/tplib/` so CPython tests can find tplib |

**Compiler search order** (first match wins):
1. Entry point directory (user modules)
2. `-L` paths (user-specified, in order)
3. `lib/tpy/` (tplib, stdlib modules, tpy protocols)

The CPY phase of `test_case` uses PYTHONPATH `lib/cpy/:src_dir`.

## Performance Profiles (Planned)

TurboPython will support performance profiles applied at function, class, or module level:

- **Default**: Standard Python constructs allowed (`list`, `str`, etc.)
- **`@noalloc`**: No heap allocation - for hot paths and real-time code

Note: `@noalloc` is parsed but not yet enforced. See `docs/LANGUAGE_FEATURES.md` for the design.

## Current Limitations

The compiler is a proof-of-concept. Not yet implemented:
- `async`/`await`
- Stepped slice assignment (`items[::2] = [...]`)

Working but not previously listed:
- `try`/`except`/`else`/`finally` -- two-tier exception model (C++ throw/catch + `@error_return` zero-cost via `std::expected`)
- `match`/`case` pattern matching (union, literal, record, optional, enum, guard, or-pattern, positional, nested type sub-patterns for parameterized union disambiguation and union-typed field guards)
- `bytes`/`bytearray`/`BytesView` types
- Generator functions (yield -> state machine codegen)
- Compile-time macro modules (`# tpy: macro_module`)
- `tplib.json` -- JSON parsing/serialization library with `@model` macro for pydantic-style typed deserialization
- `del x` -- variable unbinding and early destruction
- `a = b = c = expr` -- multiple assignment with anchor-based desugaring
- Nested class and enum definitions (`class Outer: class Inner: ...`) with arbitrary depth, constructor calls, and CPython-compatible short name resolution
- `TypedDict` -- typed dictionary with per-key types, used for `**kwargs` parameter passing
- `*args` (homogeneous) and `**kwargs: Unpack[TypedDict]` -- variadic arguments

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
| `bytes` | `std::vector<uint8_t>` |
| `bytearray` | `std::vector<uint8_t>` (mutable) |
| `BytesView` | `std::span<const uint8_t>` |
| `basic_slice` | `tpy::BasicSlice` (start, stop) |
| `slice` | `tpy::Slice` (start, stop, step) |

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
- **Report pre-existing bugs** discovered during implementation. If you find a bug in adjacent code that is not caused by your changes, report it to the user. Do not silently ignore it.

### Key Documentation Files

| File | Purpose | Policy |
|------|---------|--------|
| `docs/LANGUAGE_FEATURES.md` | Comprehensive language feature documentation | **Keep up-to-date** with any development |
| `docs/ARCHITECTURE.md` | Compiler architecture: nominal/structural types, TypeDef registry, sema layout, perf tradeoffs | Update when type-system or sema structure changes |
| `CLAUDE.md` | Commands, architecture, quick reference | Update when adding major features |
| `README.md` | Quick start, build flags | Update when CLI changes |

**Before committing**: If code adds new features or changes behavior, update `docs/LANGUAGE_FEATURES.md` in the same commit to reflect the current state (Working/Planned/Open status).
