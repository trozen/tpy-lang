# Architecture

Cross-cutting design notes for the tpyc compiler. Feature-specific
design docs live alongside this one (e.g. `ENUM_DESIGN.md`,
`MATCH_CASE_DESIGN.md`); see `README.md` / `CLAUDE.md` for the tool-
level and usage overview.

## Compilation pipeline

```
parse              -> AST with TypeRefNodes              [parse/]
collect DTOs       -> workspace-wide AST snapshot        [compiler.py]
canonicalize       -> import table -> defining modules   [compiler.py]
resolve            -> TypeRefNodes -> TpyType            [parse/resolve_refs.py]
finalize decls     -> records / sigs / globals (per mod) [sema/, sub-phases 1-3]
analyze bodies     -> per-function/method body sema      [sema/, sub-phases 4-5]
codegen            -> C++ .hpp / .cpp                    [codegen_cpp/]
```

The sema half runs as a workspace-wide two-pass loop: every module
finalizes declarations first, then bodies run as a second sweep.
Inside each module, sema is factored into five publicly callable
sub-phases (`bind_imports`, `register_records_and_protocols`,
`register_signatures`, `analyze_bodies`, `run_phase2_fixpoint`) with
a phase-counter assertion enforcing the ordering. The ModuleInfo
`registry.modules` dict is shared across all analyzers in a single
compilation; per-module short-name bindings (records / functions /
protocols / enums / type aliases) stay per-analyzer.

Cyclic imports between user modules are detected via Tarjan SCC over
the import graph. Cycle members must be import + declaration only at
top level (no executable statements); package `__init__.py` and
`# tpy: native_module` re-export facades may participate as of the
per-module attribute table refactor. All `from b import X` shapes
(record, function, protocol, enum, plus re-exports through cycle
peers) work: skeletal `RecordInfo` / `FunctionInfo` / `ProtocolInfo`
are minted from parsed ASTs before any module's full sema runs (so
peer `bind_imports` finds stable references), and the registration
paths adopt the skeletons via in-place mutation so peer registries
that captured a reference see the freshly-finalized fields.
Protocols also pre-attach a `TypeDef.protocol` entry under the
canonical qname so the parser-level type resolver detects cycle-peer
protocol references as `is_protocol=True` even before the defining
module's `register_protocol` runs. A second pre-pop pass
(`_pre_populate_reexport_bindings`) fixpoint-resolves cycle re-export
bindings on the per-module attribute table so a cycle peer's
`bind_imports` finds names re-exported from another peer (whose own
`bind_imports` may not have run yet). Variable and type alias
re-export *from* a cycle peer is the one remaining gap (BUGS.md
entries) -- their bodies aren't TpyType-resolved at pre-pop time.

The C++ back-end emits `<mod>_fwd.hpp` per cycle member with
forward declarations of the module's records / enums / `@dynamic`
protocol bases; cycle peers `#include` the fwd header in their
`.hpp` and the full header in their `.cpp`. Method bodies on cycle
members always emit out-of-line in `.cpp` so a small body's
inline-in-header optimization doesn't reach into a peer's complete
type from the .hpp.

A workspace-wide completeness-graph reject gate
(`_check_workspace_completeness_cycles`) runs after decl
finalization and before body sema. For every non-trivial import
SCC it walks each member's record fields and parents and rejects
positions that would require a peer's *complete* type (concrete
inheritance, by-value record fields, by-value containers, value-
variant unions, static-protocol template constraints). The
diagnostic names the offending field/parent + cycle members and
points the user at `Ptr[T]` as the workaround.

Phase 2 mutation propagation runs workspace-wide: each analyzer's
`_propagate_mutation_facts` collects FunctionInfos from every
ModuleInfo in the shared `registry.modules` dict (deduplicated by
`id()`), so cross-module mutating calls propagate facts uniformly
through the workspace call graph instead of getting conservative
defaults at module boundaries.

Parsing is mostly syntactic: the parser emits `TypeRefNode` (see
`parse/nodes.py`) for every annotation site and a dedicated resolve
phase binds them to `TpyType`. The parser's few remaining typesys
imports (`FieldInfo`, `RecordInfo`, `TypeRegistry`, `FunctionInfo`,
`MethodSignature`, `ProtocolInfo`, `TypeParamKind`, `LiteralValue`)
are bookkeeping only; zero `TpyType` constructors or singletons.

Error classes (`SemanticError`, `Scope`, `Diagnostic`,
`DiagnosticLevel`, `TypedExpr`) live in a neutral `tpyc/diagnostics.py`
that depends only on `typesys` and `parse.nodes`. Both the resolve
phase (`parse/resolve_refs.py`, `parse/type_resolver.py`) and the
analyze phase (everything in `sema/`) raise `SemanticError` so the
CLI emits `file:line: error: msg` uniformly across resolution and
analysis errors. The `tpyc.sema` package re-exports the diagnostics
classes for backward compatibility.

`Compiler._canonicalize_import_sources` rewrites each module's
import table from surface names (`from tplib import ArrayList`) to
defining modules (`tplib.array_list`). The lookup walks parsed
ASTs (records / protocols / enums for local definitions; chases
`ast.imports` for re-export chains) so it does not require the
dep's sema to have completed -- which is what allows cyclic
imports to canonicalize without a topological order. Runs before
`_resolve_module_refs` so `TypeResolver` can mint canonical
`_module_qname` directly.

Re-export chains work because `Compiler._extract_exports` share-
points the `RecordInfo` / `ProtocolInfo` / enum `NominalType` object
through every re-exporting module
(`exports.records[local_name] = module_info.records[original_name]`).
The shared ref always carries the ultimate `defining_module`, so
canonicalization produces the same tuple no matter how many
intermediates surfaced the type -- no transitive chain walk needed.
Functions and variables flatten to the ultimate definer at extract
time (via `originating_module` and `_flatten_var_reexport`) so
consumer codegen and `using` emissions land at the definer's
namespace directly. The per-module attribute table
(`CompiledModule.module_attributes`) stores the same flattened
bindings under one entry per local name; codegen, parser
canonicalization, macro chain resolution, and the cycle re-export
pre-pop all read it via `lookup_qualified` / `walk_attribute_chain`
helpers in `tpyc/symbol_binding.py`.

Only types backed by a real `TypeDef` entry (records, enums,
protocols, type-factory-backed generics) are canonicalized. Type
aliases, decorator names, and other non-nominal imports stay in
their surface shape; `ImportProcessor._canonical_imports` /
`is_canonical(name)` gates this. Blind qname minting on every
import would pollute bare `NominalType`s that the analyzer's alias
resolver uses as placeholders (it skips anything with
`_module_qname` set), silently breaking compile-time aliases like
`type Float64 = float`.

## Type system

### Nominal vs. structural

Every `TpyType` falls into one of two shapes:

```
TpyType
  NominalType(qname, type_args)
    Anything with a name. Identity = (name, _module_qname, type_args).
    Covers primitives, containers, records, protocols, enums.
    All per-qname behavior sourced from TypeDef registry.

  StructuralType (abstract)
    Identity = shape of operands. No qname, no TypeDef.
      PtrType, OwnType, OptionalType, UnionType, TupleType,
      ReadonlyType, CallableType(is_template), IntLiteralType,
      FloatLiteralType, LiteralType, PendingStrType, ...
```

Use qname/category predicates (`is_list`, `is_fixed_int_type`,
`type_def_of(t).category == ...`) or explicit `NominalType.name`
comparison for nominal dispatch. `type(x) == type(y)` is only
meaningful between structural classes, because post-collapse every
container, primitive, record, and enum-class is a `NominalType` --
`type(list[Int32]) == type(set[Int32])` is `True` and they differ
only in `.name` / `.qualified_name()`.

### TypeDef registry

`tpyc/type_def_registry.py` holds a single `TypeDef` per qname with
all behavior (`cpp_formatter`, `is_send`/`is_sync`, `element_of`,
`subscript_borrows`, `is_value_type`, `needs_explicit_element_target`,
`param_kinds`, `type_factory`, category payloads `int_traits`,
`float_traits`, `enum: EnumInfo`, `record: RecordInfo`,
`protocol: ProtocolInfo`). Dispatch is qname-based:

```python
type_def_of(t).subscript_borrows
type_def_of(t).category == TypeCategory.FIXED_INT
protocol_info_of(t)  # None-safe read of td.protocol
```

For string-only callers that can't synthesize a `TpyType` (parent-protocol
names in `TpyProtocol.parent_protocols`, `except`-clause exception types,
`isinstance(x, T)` second-arg TpyName, bare `protocol_name: str` params),
`TypeRegistry.scan_by_short_name(name)` resolves the name through the
current analyzer's module-local alias table. It is the only short-name
protocol lookup path; there is no parallel payload dict.

Static entries (primitives, builtin generics, `tpy.Ptr` under
`TypeCategory.STRUCTURAL_WRAPPER`) are populated once at module load
via `_populate()` + `_populate_factories()`. Dynamic entries
(records, protocols, enums registered at sema time) are attached
via `attach_dynamic_type_def(qname, category, ...)` and reset
between compilations by `clear_dynamic_type_defs()`.

Conformance tests in `tpyc/test_type_def_registry.py` pin the
invariants: `PRIMITIVE_SNAPSHOT`, `ENUM_SNAPSHOT`, `FACTORY_SNAPSHOT`,
`PROTOCOL_SNAPSHOT`.

### Identity invariants

1. **Nominal identity is qname-based; `_module_qname` is not a
   semantic shortcut.** Every builtin singleton -- primitives (Int32,
   str, bool, ...) and builtin generics (list, dict, set, Array,
   Span, ...) -- has `_module_qname` set. Never branch sema
   validation on "has `_module_qname`" to skip registry/arity checks;
   that lets bare generics like `list` (no type args, factory
   demands one) pass validation. Validity and arity for a
   `NominalType` come from looking up its qname in
   `TypeDef` / `RecordInfo` / `get_type_def(...).param_kinds`, not
   from inspecting `_module_qname`.

2. **`NominalType.__eq__` / `__hash__` include `_module_qname`
   strictly** (transitive equivalence; set/dict behavior is
   insertion-order independent). A helper `same_nominal_symbol_loose`
   in `typesys.py` covers the narrow set of sites that compare
   across the parse/resolve boundary (bare placeholder vs qname-
   bearing) -- currently only `TypeRegistry.is_subclass_of`'s
   parent-chain walk.

3. **Never use `type(x) == type(y)` for nominal-kind dispatch.** Use
   qname/category predicates or explicit `NominalType.name`
   comparison. Two sites in `sema/overloads.py` (`type_matches_numeric`,
   `_structural_match`) used this anti-pattern pre-Phase-D and
   silently matched `list[Int32]` against `set[Int32]` overloads via
   element recursion after the subclass collapse.

4. **`RecordInfo` carries two module fields.** `RecordInfo.module` is
   the public collapsed name (used for `qualified_name()` and TypeDef
   key); `RecordInfo.defining_module` is the raw uncollapsed
   submodule (used by re-export lookup and codegen C++ namespace
   qualification). Mixing them breaks re-export lookups.

5. **Sema `ctx.module_name` vs. codegen `module_name` differ for
   `__main__`.** Sema uses `"__main__"` for the entry point; codegen
   uses the file-based module name (e.g. `"main"`).

6. **`TpyTypeRef` flows to `TpyType` via `_resolve_pending_type_refs`
   (in `parse/resolve_refs.py`)** which runs early in
   `compiler._analyze_module`. Downstream passes read `TpyType`. New
   passes that read annotation fields must run after this.

7. **Pending\* types share builtin qnames** with their resolved form
   (`PendingListType.qualified_name() == "builtins.list"`), so
   `is_list` / `is_dict` / `is_set` require
   `isinstance(t, NominalType)` guards. Use `_is_concrete_cat` when
   adding new predicates for qnames that have Pending counterparts.

## Design decisions

- **Nominal vs. structural** is the real axis, not "named vs.
  container vs. primitive." `NominalType` covers anything with a
  qname; structural types cover everything whose identity is
  operand shape.
- **`TypeDef` is one table, not many registries.** Scattering
  per-behavior sets (`_SUBSCRIPT_BORROWS_QNAMES`,
  `_IS_SEND_QNAMES`, ...) across modules scales poorly. One
  `TypeDef` entry per qname makes it easy to discover all behavior
  of a type in one place, and adding a new builtin means touching
  one line.
- **Primitives are `NominalType`.** Keeping a `FixedIntType`
  subclass with a `.name` field would be the hybrid trap. If
  behavior lives in `TypeDef`, the subclass has nothing left to
  justify its existence.
- **Enums are `NominalType`.** Members and underlying type belong
  on `TypeDef.enum`, not on a separate subclass.
- **Wrappers stay structural.** `Ptr`, `Own`, `Optional`, `Union`,
  `Tuple`, `Callable`, `Readonly` all have structural identity.
- **Literal types are structural.** `IntLiteralType(value)` /
  `FloatLiteralType(value)` carry a value, not a qname.
- **Canonical import sources.** Import names are aliases;
  authoritative identity is the module where a type is defined.
  `Compiler._canonicalize_import_sources` rewrites surface imports
  to defining modules so `TypeResolver` can mint canonical qnames
  on the first pass.

## Sema module layout

`tpyc/sema/` is partitioned by analysis concern, not by AST node.
The orchestrator is `analyzer.py`; everything else is an analyzer
component wired up through `SemanticContext` + a few ad-hoc
dependencies.

Top-level analyzers (one module each):
`type_ops`, `compatibility`, `protocols`, `narrowing`, `overloads`,
`operators`, `expressions`, `calls`, `methods`, `statements`,
`match`, `registration`, `list_literals`, `local_deduction`,
`init_tracker`, `scope_tracker`, `flow_facts`, `value_range`,
`numeric_lattice`, `mutation_propagation`, `method_expansion`,
`macros`, `reach_analysis`, `context`. Error classes live in
`tpyc/diagnostics.py` (see "Compilation pipeline").

`reach_analysis` runs after body analysis as a small post-pass that
scans the analyzed module for cross-module `NominalType._module_qname`
references and stores the set of reached defining modules on
`SemanticContext.reached`. Codegen consumes this to drive transitive
`# tpy: include(...)` propagation through native records: when a
consumer reaches a native record only via a field/method chain (and
not via a direct import), the chain of native-module headers it
depends on is emitted into the consumer header. Native-to-native
chains are followed explicitly in codegen since natives have no
`.hpp` to chain through.

### Circular imports

The sema top-level import graph is a DAG except for one pair:

```
methods.py --(top-level)--> calls.py
calls.py   --(lazy)-------> methods.py    # cycle
```

`MethodAnalyzer` is invoked from `CallAnalyzer.analyze_call`
(the `super()` dispatch branch) and `CallAnalyzer._inline_function_call`
via `from .methods import MethodAnalyzer` inside the method body.
The inverse direction (methods depending on calls at module load)
is load-bearing; both modules hold a `CallAnalyzer` /
`MethodAnalyzer` instance and call each other at analysis time.
This is the only genuine cycle; other lazy imports have been
hoisted to the top of their respective modules.

### Why `calls.py` is one module

`calls.py` is ~3400 lines with a single `CallAnalyzer` class
(~50 methods) plus ~20 module-level helpers. The class dispatches
on `TpyCall` shape -- record constructor, generic function,
builtin overload, special builtin (`isinstance`, `tpy.copy`,
`copy_iter`, `own_iter`, `try_parse`), dunder (`__init__`, `__call__`),
macro, fn/callable value, recursive-union constructor, expression
callee, etc. The methods share:

- `self.ctx` (SemanticContext) and its many fields,
- `self.type_ops`, `self.expr`, `self.protocol_checker`,
  `self.compat`, `self.deduce`, `self.methods`,
- the module-level helpers (`resolve_kwargs`, `arity_error_msg`,
  `validate_generic_defaults`, `validate_type_param_bounds`,
  `_enrich_literal_types`) which are also imported directly by `methods.py`.

Splitting `CallAnalyzer` by call-shape would either require mix-ins
(all assembled back into one `CallAnalyzer` -- no structural gain)
or delegate objects each holding the same 6-way dependency set. The
module-level helpers could move to a `call_utils.py` but that is
cosmetic. Left as one cohesive module.

## Safety techniques

- **Conformance tests** in `tpyc/test_type_def_registry.py`:
  `PRIMITIVE_SNAPSHOT`, `ENUM_SNAPSHOT`, `FACTORY_SNAPSHOT`,
  `PROTOCOL_SNAPSHOT` pin intrinsic per-qname behavior
  (`is_value_type`, `is_send`/`is_sync`, `subscript_borrows`,
  `to_cpp`, trait accessors, factory param-kinds, per-protocol
  `cpp_concept` / `is_marker` / `is_readonly` / method-name set).
  Compared against the live `TpyType` instance; values are
  independent of the implementation path.
- **Byte-identical generated C++** at any boundary: every case under
  `tests/cases/**/expected/` has pinned `include/*.hpp` and
  `src/*.cpp`. The fingerprint cache in `test_case` catches drift
  automatically -- any case whose exec phase doesn't skip during a
  refactor is worth investigating. `pytest --force-exec` catches
  cases where generated code changed but fingerprints happened to
  collide.
- **Root `conftest.py` autouse fixture** clears process-global
  compilation state (`_native_cpp_names`, `_union_alias_names`,
  `_protocol_modules`, `_return_exception_names` in `typesys.py`;
  dynamic slice of `_type_defs` in `type_def_registry.py`) before
  every test. See `TODO.md` for the plan to move this state onto
  `Compiler`.
- **Durable review check.** Grep future PRs for
  `isinstance(x, NominalType) and not x.is_protocol` -- most sites
  want `x.is_user_record` instead. User records are the only
  non-protocol `NominalType`s that bypass the TypeDef/factory
  system.

## Performance tradeoffs

Most type dispatch in the compiler is now Python-level predicate
calls and `TypeDef` dictionary lookups rather than C-level
`isinstance` checks. The architectural win is real -- one place to
add/change behavior per qname -- but the move has a per-call cost
worth tracking on large projects.

**Known hot paths where the cost concentrates:**

- **`coercions.py:resolve_coercion`** -- linear scan over ~39 rules,
  each evaluating two predicates (`from_type(actual)` and
  `to_type(expected)`) plus an optional `type_match`. Pre-collapse
  this was one C-level `isinstance` per rule (roughly two machine
  instructions); now it's a Python function call plus a `type_def_of`
  dict lookup per rule (tens of instructions each). For a compilation
  with N coercion resolutions the overhead is roughly `N * 39 * 2 *
  (Python call + dict lookup)`. Unmeasured.
- **`type_def_of`** on every `is_*_type` call. Each hit is
  `qualified_name()` + `dict.get`, called from many predicates.
  `qualified_name()` is an instance-method indirection that branches
  per subclass.
- **Tag-based predicates** (`is_numeric_type`, `is_primitive_type`,
  `is_any_str_type`, ...) formerly were `t.tag in _TAGS_FROZENSET`
  (one attribute + one set lookup). They now compose
  `is_fixed_int_type(t) or is_big_int_type(t) or ...` with up to
  four `type_def_of` lookups per call.

**Mitigations available if measurement shows a problem:**

1. **Hash-table dispatch in `resolve_coercion`**: when both sides
   are qname-resolvable, the rule can be indexed by
   `(from_qname, to_qname) -> Coercion`. Fallback to linear scan
   only for rules whose side is `_match_any_side`. Could cut
   per-call cost by >10x.
2. **Per-instance tag cache on `NominalType`**: store
   `TypeDef.category` on the instance at construction (one
   `type_def_of` lookup per singleton, then O(1) attribute access
   thereafter). Would let tag-based predicates return to the
   original frozenset-lookup speed.
3. **mypyc**: the current shape (small pure predicates calling
   other predicates) is mypyc-friendly -- mypyc can inline
   predicate calls and specialize `type_def_of` dispatch. The
   Python-level overhead should mostly vanish under mypyc.
4. **Conservative profiling before optimizing**: the compiler
   isn't in a tight loop -- most work is I/O (reading source,
   writing C++) and semantic analysis that's dominated by other
   costs. Benchmark before assuming dispatch cost is the
   bottleneck.
