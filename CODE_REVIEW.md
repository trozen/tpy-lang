# Code Review — Architectural & Design Issues

## Critical Architectural Issues

### ~~1. Two divergent compilation paths (cli.py vs compiler.py)~~ ✅ Resolved

Unified via `Compiler.from_source()` — both stdin and file mode now use `Compiler`. The dead `compile_file()` function was removed.

### 2. Parser has too many responsibilities (parse.py) — partially addressed

The parser does far more than parsing:
- **Import validation** (`_check_import`, `_check_import_from`) — semantic concern
- ~~**TPY type import enforcement** (`_check_tpy_type_imported`) — policy enforcement~~ ✅ Extracted as standalone module-level function with explicit parameters; redundant `_tpy_imported_names` state removed
- **Import tracking** (`imports`, `user_module_imports`, `module_aliases`) — compiler-level concern
- **Type parameter scope management** (`_type_param_scope`) — semantic concern

Additionally: `SPECIAL_MODULES`, `TPY_TYPES`, and operator mapping dicts promoted to module-level constants, reducing Parser instance state. See also #24 (resolved).

This makes it difficult to test parsing in isolation and creates coupling between syntax handling and semantic validation.

### 3. Circular dependency management via manual wiring (sema/ and codegen_cpp/)

Both `sema/analyzer.py:43-78` and `codegen_cpp/generator.py:49-52` use fragile manual setter injection:

```python
# sema/analyzer.py
self.expr.calls = self.calls
self.expr.methods = self.methods
self.calls.expr = self.expr
self.methods.expr = self.expr
self.methods.calls = self.calls
self.stmts.expr = self.expr
```

If a new component is added but wiring is forgotten, it silently fails. No compile-time or runtime check ensures completeness.

### 4. God Objects: SemanticContext (24 fields) and CodeGenContext (22+ fields)

Both context classes accumulate unrelated responsibilities:
- **SemanticContext** (`sema/context.py`): scope state, type caches, list literal tracking, import tracking, cross-module support, namespace hierarchy, diagnostics
- **CodeGenContext** (`codegen_cpp/context.py`): indentation, variable tracking, module imports, temp variables, scope management, import tracking

These should be decomposed into focused sub-contexts.

---

## Significant Code Duplication

### 5. Type matching logic duplicated 3x across sema/

`operators.py:binop_type_matches`, `methods.py:_builtin_type_matches`, and `calls.py:_builtin_type_matches` all implement nearly identical type compatibility checks for IntLiteralType coercion. Should be a single shared `TypeMatcher`.

### 6. Type argument slice extraction duplicated 3x in parse.py

`_parse_protocol_type_args`, `_parse_record_type_args`, and `_parse_generic_type` all contain the same pattern:

```python
if isinstance(node.slice, ast.Tuple):
    slices = node.slice.elts
else:
    slices = [node.slice]
```

### 7. Binary operator definitions massively duplicated across modules

`builtins.py:220-254` (BigInt) and `tpy.py:30-61` (Int32) each define 17+ nearly identical operator methods. Forward and reverse operators differ only in operand order. A helper like `create_binop_methods(ops, type, template)` would eliminate ~60 lines of repetitive definitions.

### 8. Module-to-C++ namespace conversion duplicated 6+ times

`source_module.replace('.', '::')` and `tpy_user::{ns}::{name}` patterns appear in `generator.py`, `functions.py`, `expressions.py`, `statements.py`, `protocols.py`, and `types.py`. Should be a single utility.

### 9. C++ compilation command duplicated across cli.py and conftest.py

The `g++ -std=c++23 ... -lgmp` invocation appears in:
- `cli.py:237-249`
- `conftest.py:56-77` (`compile_and_run_cpp` — dead code)
- `conftest.py:150-176` (`build_and_run`)

### 10. String escaping logic duplicated

Character/string escaping (`.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')`) appears in both `expressions.py` and `builtins.py`.

### 11. Overload resolution patterns duplicated

`methods.py:469-516` and `calls.py:278-309` both implement two-pass overload resolution (exact match, then coercions) with duplicated matching and argument coercion loops.

---

## Hard-Coded Values and Magic

### 12. Relative import placeholder is a magic string

`parse.py:555` creates `__rel__{level}__{lineno}_{col}__{partial}`, and `compiler.py:188` parses it back with `split("__", 4)`. This is fragile (what if a module name contains `__`?), undocumented, and lacks type safety. Should be a dataclass.

### 13. C++ compiler and flags are hard-coded

`cli.py:237-249`: `g++` is hard-coded with no way to use `clang++`. `-std=c++23`, `-lgmp`, optimization flags — all embedded in CLI code. Should be a `CompilerConfig` object.

### 14. Mutation method set is a magic literal

`sema/methods.py:200-201`:
```python
mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend", "reverse", "__setitem__"}
```
If new mutation methods are added, this set must be found and updated. Should live alongside list type definitions.

### 15. INT32 range constants duplicated

`sema/compatibility.py:124-125` defines `INT32_MIN = -(2**31)` and `INT32_MAX = 2**31 - 1` locally. These should be in `typesys.py` and imported.

### 16. `.d` directory suffix convention undocumented and scattered

The `.d` suffix for module output directories appears in `cli.py:63`, `cli.py:230-231`, `compiler.py:545`, `compiler.py:558-560` with no central definition.

### 17. Dunder-to-operator mappings duplicated

Both `codegen_cpp/protocols.py:227-235` and `codegen_cpp/records.py:41-52` define identical `DUNDER_TO_OPERATOR` dictionaries.

---

## Design Inconsistencies

### 18. Mixed lookup strategies in sema/

Three different lookup strategies coexist:
1. Namespace lookup: `self.ctx.current_ns.lookup(name)`
2. Scope fallback: `self.ctx.current_scope.lookup(name)`
3. Direct registry: `self.ctx.registry.get_record(name)`

`expressions.py:175-194` uses namespace first, then falls back to scope "for compatibility" — suggesting an incomplete refactoring.

### 19. Sentinel value misuse

`sema/analyzer.py:336` sets `self.ctx.current_function = True` as a sentinel for "module init context" when the field expects `TpyFunction | None`. This breaks `isinstance` checks and violates the type contract.

### 20. Inconsistent method resolution interfaces

The sema package offers three different method lookup patterns:
- `lookup_record_method()` → single method
- `lookup_record_method_overloads()` → list with substitution
- `RecordInfo.get_method_overloads()` → list without substitution

Call sites must know which to use and when, with no uniform interface.

### 21. `is_protocol` checks scattered as raw isinstance chains

`isinstance(type, NamedType) and type.is_protocol` appears ~10 times across `registration.py`, `statements.py`, `calls.py`. Should be `type_ops.is_protocol_type(typ)`.

### 22. Inconsistent module registration API

`builtins.py` uses both `.type()` (for parameterized types like `list`) and `.register_type()` (for non-parameterized types like `str`, `int`). No documentation explains when to use which.

---

## Anti-Patterns

### 23. Silent `"?"` fallback in operator parsing

`parse.py:1354`: `ops.get(type(op), "?")` returns `"?"` for unknown operators instead of raising an error. This could silently generate invalid C++ code.

### ~~24. Operator dicts recreated on every call~~ ✅ Resolved

Operator mappings (`_BINOP_TO_STR`, `_CMPOP_TO_STR`, `_UNARYOP_TO_STR`) promoted to module-level constants. Methods now do simple lookups.

### 25. Post-hoc type annotation updates

`sema/statements.py:292-293` tracks `var_decl_by_name` for later retroactive type updates (`statements.py:328-331`). This mutable-cache-based type resolution is fragile if variables are reassigned.

### 26. String-based C++ template substitution with no validation

Codegen uses chained `.replace("{self}", ...).replace("{0}", ...).replace("{1}", ...)` throughout. No verification that all placeholders were substituted. No check that `{2}` isn't used when only 1 parameter exists.

### 27. Assertions used for input validation

`codegen_cpp/builtins.py:67`: `assert method.cpp_template is not None` — assertions can be stripped with `-O`. Should be explicit checks with `CodeGenError`.

### 28. Dataclass `__post_init__` boilerplate instead of `field(default_factory=...)`

`typesys.py:836-852`:
```python
init_params: list[...] = None  # then in __post_init__: if self.init_params is None: self.init_params = []
```
Modern Python has `field(default_factory=list)` for this.

---

## Complexity Hotspots

### 29. `_discover_modules()` — 164 lines, deeply nested (compiler.py:127-290)

Handles circular import checking, relative import resolution, submodule detection, and builtin shadowing all in one method. Should be decomposed.

### 30. `analyze_method_call()` — 262 lines (sema/methods.py:89-351)

Handles 7 different call patterns (super, static, module, builtin, user record, protocol, bounded type param) in one method with deep nesting.

### 31. `match_type_with_inference()` — 152 lines (sema/type_ops.py:287-439)

7 nested cases with 8+ levels of nesting for ArrayType. Each case should be its own method.

### 32. `_generate_protocol_ordering()` — 164 lines (codegen_cpp/generator.py:156-320)

Complex ordering logic for protocol declarations, forward declarations, and re-exports all interleaved.

---

## Test Infrastructure Issues

### 33. Dead code: `compile_and_run_cpp()` (conftest.py:56-77)

This function is defined but never called. `build_and_run()` (line 150) supersedes it with multi-module support.

### 34. Test discovery can produce name collisions

`conftest.py:320`: Test names are generated by collapsing paths with `replace("/", "_")`. Both `tests/cases/foo_bar/` and `tests/cases/foo/bar/` would become `test_cases_foo_bar`.

### 35. Indirect test generation hurts debuggability

All three test files (`test_comp.py`, `test_exec.py`, `test_cpy.py`) use `globals()[f"test_{name}"]` to dynamically create test functions. IDE integration struggles with these, and stack traces point to generated code rather than test cases. `pytest.mark.parametrize` would be cleaner.

### 36. Platform-specific path separator

`conftest.py:41`: `env["PYTHONPATH"] = f"{HARNESS_DIR}:{src_dir}"` — Unix-specific. Would break on Windows (`os.pathsep` should be used).

---

## Summary by Priority

| Priority | Issues | Key Action |
|----------|--------|------------|
| **High** | #1, #2, #3, #4 | Unify compilation paths; decompose Parser, SemanticContext, CodeGenContext |
| **High** | #5, #6, #7, #8, #11 | Extract shared utilities for type matching, slice extraction, operators, namespaces |
| **High** | #12, #13 | Replace magic strings with typed data; create CompilerConfig |
| **Medium** | #9, #10, #17 | Consolidate duplicated code (compilation commands, escaping, dunder maps) |
| **Medium** | #18, #19, #20, #21, #22 | Standardize lookup/resolution patterns; fix sentinel misuse |
| **Medium** | #23, #26, #27 | Fail fast instead of silently; validate templates; use proper checks |
| **Medium** | #29, #30, #31, #32 | Decompose complex methods into focused helpers |
| **Low** | #14, #15, #16, #24, #25, #28 | Centralize constants; use `field(default_factory=...)`; class-level dicts |
| **Low** | #33, #34, #35, #36 | Clean up dead code; fix test infrastructure fragility |
