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

### ~~5. Type matching logic duplicated 3x across sema/~~ ✅ Resolved

Extracted shared `type_matches_strict`, `type_matches_numeric`, and `type_matches_with_coercion` functions into `sema/overloads.py`. All three call sites (`operators.py`, `calls.py`, `methods.py`) now delegate to these shared functions.

### ~~6. Type argument slice extraction duplicated 3x in parse.py~~ ✅ Resolved

Extracted shared `_extract_subscript_slices()` module-level helper used by all four type argument parsing methods.

### ~~7. Binary operator definitions massively duplicated across modules~~ ✅ Resolved

`make_binop_methods()` helper in `modules/helpers.py` generates forward and reverse operator methods from a compact definition dict. Both BigInt (`builtins.py`) and Int32 (`tpy.py`) now use it, eliminating ~60 lines of repetitive definitions.

### ~~8. Module-to-C++ namespace conversion duplicated 6+ times~~ ✅ Resolved

Extracted `module_to_cpp_namespace()` function in `codegen_cpp/context.py`, replacing 13+ inline `.replace('.', '::')` and `tpy_user::` constructions across `generator.py`, `functions.py`, `expressions.py`, `statements.py`, `protocols.py`, `types.py`, and `compiler.py`. Two duplicate `_module_to_namespace()` methods removed.

### ~~9. C++ compilation command duplicated across cli.py and conftest.py~~ ✅ Resolved

`BuildLayout.build_cpp_command()` centralizes the g++ command construction. Both `cli.py` and `conftest.py:build_and_run` now use it.

### ~~10. String escaping logic duplicated~~ ✅ Resolved

Extracted `escape_cpp_string()` and `escape_cpp_char()` helpers in `codegen_cpp/context.py`, used by `expressions.py` and `builtins.py`.

### ~~11. Overload resolution patterns duplicated~~ ✅ Resolved

Extracted shared `resolve_overload()` function in `sema/overloads.py` implementing two-pass overload resolution (strict match, then coercions). Used by `calls.py`, `methods.py` (both builtin function overloads and method overloads), and super method call resolution.

---

## Hard-Coded Values and Magic

### ~~12. Relative import placeholder is a magic string~~ ✅ Resolved

Replaced with `RelativeImportKey` frozen dataclass providing `encode()`/`decode()`/`is_placeholder()` methods. Format construction and parsing are now co-located.

### ~~13. C++ compiler and flags are hard-coded~~ ✅ Resolved

`CppCompilerConfig` dataclass in `compiler.py` holds compiler, standard, extra flags, and link flags. `CppCompilerConfig.from_env()` reads the `CXX` environment variable. Used by `cli.py` and `repl.py`. `BuildLayout.build_cpp_command()` accepts a `config` parameter.

### ~~14. Mutation method set is a magic literal~~ ✅ Resolved

`LIST_MUTATION_METHODS` frozenset now defined in `modules/builtins.py` alongside list type definitions, imported by `sema/methods.py`.

### ~~15. INT32 range constants duplicated~~ ✅ Resolved

`INT32_MIN` and `INT32_MAX` now defined in `typesys.py` alongside other type constants, imported where needed.

### ~~16. `.d` directory suffix convention undocumented and scattered~~ ✅ Resolved

`BuildLayout` class in `compiler.py` centralizes the build directory structure (`.d` suffix, `include/`/`src/` subdirs, binary path). Used by `compiler.py`, `cli.py`, and `conftest.py`.

### ~~17. Dunder-to-operator mappings duplicated~~ ✅ Resolved

Shared `DUNDER_TO_BINARY_OP` constant in `codegen_cpp/context.py`, imported by both `protocols.py` and `records.py`.

---

## Design Inconsistencies

### ~~18. Mixed lookup strategies in sema/~~ ✅ Resolved

Dead `else:` fallback branches removed from `methods.py` (`_analyze_static_method_call`, `_resolve_module_name`) and `calls.py` (`_analyze_call`). The remaining scope fallback in `expressions.py:_analyze_name` is a migration bridge (documented) for names not yet registered in Namespace.

### ~~19. Sentinel value misuse~~ ✅ Resolved

Replaced `True` sentinel with a proper `MODULE_INIT_CONTEXT` instance of `_ModuleInitSentinel` class. Type annotation updated to `TpyFunction | _ModuleInitSentinel | None`.

### ~~20. Inconsistent method resolution interfaces~~ ✅ Resolved

`lookup_record_method()` now delegates to `lookup_record_method_overloads()`, eliminating ~25 lines of duplicated inheritance traversal. Three interfaces remain for distinct purposes: `RecordInfo.get_method_overloads()` (direct, no inheritance), `lookup_record_method_overloads()` (all overloads with inheritance), `lookup_record_method()` (single method convenience wrapper).

### ~~21. `is_protocol` checks scattered as raw isinstance chains~~ ✅ Resolved

`is_protocol_type()` function in `typesys.py` replaces ~35 scattered `isinstance(x, NamedType) and x.is_protocol` patterns across `sema/` and `codegen_cpp/`.

### ~~22. Inconsistent module registration API~~ ✅ By design

`.register_type(type_obj)` registers singleton types with pre-instantiated TpyType (e.g., `STR`, `INT32`). `.type(name)` registers parameterized type templates with a factory (e.g., `"list"`, `"Array"`). These serve fundamentally different purposes — singletons don't need factories, parameterized types can't be pre-instantiated.

---

## Anti-Patterns

### ~~23. Silent `"?"` fallback in operator parsing~~ ✅ Resolved

Operator conversion methods now raise `ParseError` for unknown operators instead of silently returning `"?"`.

### ~~24. Operator dicts recreated on every call~~ ✅ Resolved

Operator mappings (`_BINOP_TO_STR`, `_CMPOP_TO_STR`, `_UNARYOP_TO_STR`) promoted to module-level constants. Methods now do simple lookups.

### ~~25. Post-hoc type annotation updates~~ ✅ By design

The `var_decl_by_name` pattern is a correct mechanism for retroactive `IntLiteralType` resolution (e.g., `x = 5` later used as `x + Int32(10)` resolves `x` as Int32). Keyed by AST node identity (`id(stmt)`), only tracks `IntLiteralType` variables, and performs a single irreversible update — not fragile.

### ~~26. String-based C++ template substitution with no validation~~ ✅ Resolved

Extracted `expand_cpp_template(template, self_val, *args)` in `codegen_cpp/context.py`. Replaced 9 chained `.replace()` call sites across `expressions.py`, `statements.py`, and `builtins.py` with the unified helper.

### ~~27. Assertions used for input validation~~ ✅ Resolved

All 4 `assert` statements in `codegen_cpp/` replaced with explicit `CodeGenError` checks in `builtins.py`, `statements.py`, and `records.py`.

### ~~28. Dataclass `__post_init__` boilerplate instead of `field(default_factory=...)`~~ ✅ Resolved

`RecordInfo` now uses `field(default_factory=...)` for all collection fields, eliminating the `__post_init__` method.

---

## Complexity Hotspots

### ~~29. `_discover_modules()` — 164 lines, deeply nested (compiler.py)~~ ✅ Resolved

Extracted `_resolve_relative_import()` and `_process_user_import()` helpers. Main method is now a short loop delegating to focused helpers.

### ~~30. `analyze_method_call()` — 262 lines (sema/methods.py)~~ ✅ Resolved

Extracted `_analyze_static_method_call()`, `_analyze_module_method_call()`, `_analyze_builtin_type_method()`, `_analyze_user_record_method()`, and `_analyze_protocol_or_bound_method()`. Main method is now a short dispatch chain. Also extracted `_resolve_module_name()` to unify the namespace/fallback module lookup.

### ~~31. `match_type_with_inference()` — 152 lines (sema/type_ops.py)~~ ✅ Resolved

Extracted `_match_protocol_type_args_with_inference()`, `_match_array_with_inference()`, and `_match_record_with_inference()`. Main method is now a concise dispatch.

### ~~32. `_generate_protocol_ordering()` — 164 lines (codegen_cpp/generator.py)~~ ✅ Resolved

Added `_ProtocolDeps` dataclass for dependency sets. Extracted `_collect_protocol_deps()`, `_generate_forward_decls_and_concepts()`, and `_generate_definitions_and_reexports()`. Main method is now 3 lines.

---

## Test Infrastructure Issues

### ~~33. Dead code: `compile_and_run_cpp()` (conftest.py:56-77)~~ ✅ Resolved

Removed unused function.

### 34. Test discovery can produce name collisions

`conftest.py:320`: Test names are generated by collapsing paths with `replace("/", "_")`. Both `tests/cases/foo_bar/` and `tests/cases/foo/bar/` would become `test_cases_foo_bar`.

### 35. Indirect test generation hurts debuggability

All three test files (`test_comp.py`, `test_exec.py`, `test_cpy.py`) use `globals()[f"test_{name}"]` to dynamically create test functions. IDE integration struggles with these, and stack traces point to generated code rather than test cases. `pytest.mark.parametrize` would be cleaner.

### ~~36. Platform-specific path separator~~ ✅ Resolved

Now uses `os.pathsep` instead of hardcoded `:`.

---

## Summary by Priority

| Priority | Issues | Key Action |
|----------|--------|------------|
| **High** | ~~#1~~, #2, #3, #4 | ~~Unify compilation paths~~; decompose Parser, SemanticContext, CodeGenContext |
| **High** | ~~#5~~, ~~#6~~, ~~#7~~, ~~#8~~, ~~#11~~ | ~~Extract shared utilities for type matching, slice extraction, operators, namespaces~~ ✅ All resolved |
| **High** | ~~#12~~, ~~#13~~ | ~~Replace magic strings with typed data; create CompilerConfig~~ ✅ All resolved |
| **Medium** | ~~#9~~, ~~#10~~, ~~#17~~ | ~~Consolidate duplicated code (compilation commands, escaping, dunder maps)~~ ✅ All resolved |
| **Medium** | #18, ~~#19~~, #20, ~~#21~~, #22 | Standardize lookup/resolution patterns; ~~fix sentinel misuse~~ |
| **Medium** | ~~#23~~, #26, ~~#27~~ | ~~Fail fast instead of silently~~; validate templates; ~~use proper checks~~ |
| **Medium** | ~~#29~~, ~~#30~~, ~~#31~~, ~~#32~~ | ~~Decompose complex methods into focused helpers~~ ✅ All resolved |
| **Low** | ~~#14~~, ~~#15~~, ~~#16~~, ~~#24~~, #25, ~~#28~~ | ~~Centralize constants; use `field(default_factory=...)`; class-level dicts~~ — mostly resolved |
| **Low** | ~~#33~~, #34, #35, ~~#36~~ | ~~Clean up dead code~~; fix test infrastructure fragility |
