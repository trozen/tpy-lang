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

A Typed High-level IR sits between sema and codegen: THIR (`tpyc/thir/`) lowers
a body to immutable IR and emits from it with no analyzer reference. It is the
SINGLE sema->codegen boundary for every body, in every module -- user code,
`lib/tpy` and the stdlib alike -- and `codegen_cpp/` is the printer/skeleton
layer around it (module driver, headers, signatures, record/protocol/enum
drivers, frames at their leaf seams, type rendering). There is no second body
emitter: a body THIR cannot lower is a compile error (`ThirRejectError`), not a
reroute. The landed half of the IR direction in `docs/IR_DESIGN.md`.

`tpyc/mir/` provides an internal CFG builder, verifier and dump for bool/int32
scalars, borrowed/owned plain records, flat tuples, selected Optional payloads
and nonrecursive unions of bool/int32 values or borrowed plain records.
THIR carries immutable borrowed-parameter,
alias-binding, owned-storage and direct-field facts from the existing lowering decisions.
MIR uses body-scoped reference holders and explicit alias transfers; scalar
field places contain dereference and qualified field projections. Distinct
holders can reference the same object, and readonly access does not imply
an immutable referent. The verifier checks access and definite assignment,
including initialized bases for field stores, but proves no lifetimes. Owned
record slots have explicit construct/copy/move/borrow operations. Their layouts
contain only bool/int32 fields and have no custom special members; construction
requires a complete, pure constructor definition. `MIRDefinitions` indexes and
checks the emitted `THIRConstructor` artifacts and their logical layout/member
facts, without reaching back into sema. OWN replacements use distinct storage;
IN_PLACE replacements preserve referent identity. Owning operations in CFG
cycles remain uncovered; the verifier also rejects cyclic materialization.
Tuple construction/copy snapshots bool/int32 values and borrowed record identities;
reseating one tuple holder leaves copies independent. THIR carries finalized
element capture/capability layouts and normalized constant indices. Tuple field
places compose index, dereference and field selection, checked by a typed
projection walk. Empty payloads and local tuple construction from readonly record
sources are tested at the internal IR boundary because their source forms still
fail existing frontend gates. Flat tuple parameters carry explicit payload layouts, including per-element
access; wrapper constness does not imply readonly pointees. Selected standalone
record-element captures borrow the dereferenced parameter element, independently
of any later wrapper replacement. Parameter/element replacement, tuple returns,
unpacking, nested/owned elements and wrappers remain uncovered. Existing frontend
gates still exclude standalone local-tuple captures, reseated captures and
record-tuple parameter copies into later-reseated locals. Readonly auto-copy
metadata mismatches also remain uncovered (`BUGS.md#readonly-auto-tuple-copy-fact`).
Optional bool/int32 values and nullable borrowed plain records
have explicit absent/present construction, whole-value copies, presence tests
and typed payload projections. THIR records the selected payload layout and
whole-wrapper versus extracted-name reads. Presence verification propagates
finite holder facts and boolean-test implications through CFG joins and loops;
holder writes invalidate their old implications. Presence permits payload
selection only, never a lifetime proof. Nonrecursive unions use the same finite
selection analysis for bool/int32 alternatives or borrowed plain-record
alternatives, optionally including None. Record extraction captures the referent;
scalar extraction aliases the wrapper payload and any wrapper replacement
invalidates that alias until re-extraction. Live projections require a current
selection proof. Mixed scalar/reference unions, owned/nested/recursive wrappers,
wrapper parameter replacement, calls/results and runtime-checked extraction
remain uncovered. Borrowed nested record paths use consecutive inline field
projections after the root dereference. THIR storage-borrow facts distinguish
capturing a field subobject from copying a name holder; MIRBorrow takes a place
and captures its current storage identity. Holder reseats cannot retarget a
captured field, and readonly access propagates down the path. Scalar leaves
may be written; whole-field replacement and nested owning operations remain
uncovered. These borrowed paths require no eligible owning constructor. Tests
feed it the exact THIR returned by `Compiler.generate_code_and_thir()`, together
with body identity and declaration kind. It does not run during normal
compilation or affect emission. Unsupported bodies return `MIRNotCovered`, which
is distinct from malformed MIR and never constitutes a safety proof. Broader
place/loan analysis remains planned in `docs/MIR_ANALYSIS_PLAN.md`.

Tuple local bindings register their borrowed payloads with the existing sema
`BorrowTracker`, including its statement records consumed by alias-rebind
analysis. Declaration/reassignment capture modes distinguish borrowed elements
from last-use owned captures; walrus literals retain their target borrow form.
Tuple-returning calls use the existing return-borrow contracts. Consequently a
record replacement preserves storage retained by a tuple, while copying scalar
tuple members does not create loans. This is production sema behavior and does
not depend on MIR coverage.

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
defaults at module boundaries. The edges are seeded during body sema
(`_record_mutation_call_edges`), so a callee's facts may be at
different maturity depending on import order: `infer_method_const`
has already run for a peer module's methods but not for this
module's own. An edge gate must therefore never read `is_readonly`
-- a RECEIVER fact that also arrives late -- as "the callee mutates
nothing"; only a callee with no body facts at all (`native` /
builtin stub) may stand on the declaration.

Const inference reads recorded return-borrow roots through
`typesys.recorded_return_borrow_sources`. Receiver inference retains its
inherently-const-view exception; const-method parameter emission subtracts
the recorded roots from mutation facts. These are distinct policies over
the same body-analysis facts, not a second provenance analysis.

Result-representation readers share `typesys.classify_result_representation`.
An explicit position selects synchronous call classification, async payload
classification or the erased callable's declared return spelling. The reader
preserves each position's wrapper handling and exclusions; `value_category`
adapts its answers and `CallableType` renders the erased result. These are
representation decisions, not ownership, access-permission or provenance proofs.

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

Re-export attribution lives on the per-module attribute table
(`CompiledModule.module_attributes`): one `BindingCell` per local
name, with the binding's `defining_module` and `canonical_name`
already chain-flattened to the ultimate definer at install time
(sema's `_register_user_module_import` resolves variables via
`resolve_definer`; records/enums/functions take the ultimate from
`record_info.defining_module` / `EnumInfo.module_name` /
`func_info.originating_module`; `_pre_populate_reexport_bindings`
fixpoints cycle peers before any module's `bind_imports` runs).
Codegen, parser canonicalization, macro chain resolution, the
cycle re-export pre-pop, and compile-time star-import expansion
(`_expand_star_imports_for_module`) all read this table through
`lookup_qualified` / `lookup_imported` / `resolve_definer` /
`walk_attribute_chain` helpers in `tpyc/symbol_binding.py`. The
per-kind `ModuleExports.{records, functions, enums, protocols,
variables, type_aliases}` dicts are supplementary lookup tables
keyed by name -- they share-point the underlying Info objects
(a `RecordInfo` lives once and is referenced by every re-exporting
module's `exports.records`) but do not carry attribution; the
table is where defining_module / canonical_name live.

Only types backed by a real `TypeDef` entry (records, enums,
protocols, type-factory-backed generics) are canonicalized. Type
aliases, decorator names, and other non-nominal imports stay in
their surface shape; `ImportProcessor._canonical_imports` /
`is_canonical(name)` gates this. Blind qname minting on every
import would pollute bare `NominalType`s that the analyzer's alias
resolver uses as placeholders (it skips anything with
`_module_qname` set), silently breaking compile-time aliases like
`type float64 = float`.

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
`type(list[int32]) == type(set[int32])` is `True` and they differ
only in `.name` / `.qualified_name()`.

### Value-form taxonomy

`TpyType.value_form()` classifies how a type behaves as a tuple element /
borrow slot (`ValueForm` in `typesys.py`): `VALUE` (copied), `OWN` (moved),
`TYPE_PARAM` (generic proxy, resolved at C++ instantiation via
`val_or_ptr_t<T>`), `PTR_OPTIONAL` (nullable `T*`), and `BORROW_REF`
(non-null `T*`). It drives the tuple borrow/storage form split --
`TupleType.has_pointer_repr_element()` and the per-element C++ renderings --
and is the sema-side anchor for the boundary conversions
(`tuple_to_pointer` / `tuple_to_storage`). The OWNERSHIP half of the same
taxonomy is `TupleType.is_owned_movable()` (every non-value element owned,
so the tuple is owned storage) versus `is_mixed_own()` (owned AND borrowed
elements, so it has no single form) -- `has_own_element()` alone answers
neither, and an owning slot must ask the former. See `LANGUAGE_FEATURES.md`
"Borrow Form vs Storage Form" for the user-facing semantics and
`IR_DESIGN.md` Open Questions item 9 for the planned IR-level form fact.
Unit-tested in `tpyc/test_value_form.py`.

### TypeDef registry

`tpyc/type_def_registry.py` holds a single `TypeDef` per qname with
all behavior (`cpp_formatter`, `is_send`/`is_sync`, `element_of`,
`subscript_borrows`, `is_value_type`, `is_indirecting`,
`boundary_marshal` (crosses the CPython `@export` boundary by copy --
queried by `is_boundary_marshallable`, replacing a C++-type-string set
that could not tell `bytes` from `bytearray`),
`is_borrowing_view` (value-type wrapper referencing foreign storage,
e.g. dict views), `iter_yields_ref_tuple_proxies` (iteration yields
proxy reference tuples, e.g. dict_items -- drives the resumable-frame
borrow-tuple loop binding), `needs_explicit_element_target`,
`param_kinds`, `type_factory`, category payloads `int_traits`,
`float_traits`, `enum: EnumInfo`, `record: RecordInfo`,
`protocol: ProtocolInfo`). The `is_indirecting`
flag is set from `@native(..., indirecting=True)` on the stub class --
it flows parser -> `TpyRecord` -> `RecordInfo` -> `TypeDef` during
`attach_dynamic_type_def`. Cycle detection consults it to decide
whether a native wrapper type breaks recursive size cycles; structural
TPy records (with a `Ptr[T]` field) are recognized separately by
walking `RecordInfo.fields` under type-param substitution in
`tpyc/cycle_detection.py` and do not need the flag. Dispatch is
qname-based:

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

Nominal subtyping is decided through one helper: `is_subtype(info,
supertype_name)`. `ProtocolInfo` and `RecordInfo` both carry a
`transitive_supertypes: frozenset[str]` populated once during sema
registration (protocol closures over `parent_protocols`, then record
closures over `implemented_protocols` and the protocol closures already
in the registry). All record-implements-protocol and protocol-inherits-
protocol queries route through `is_subtype`; per-call filters such as
`is_dynamic` on the matching protocol or `is_native` on the record live
at the call site, not in the cache.

Static entries (primitives, builtin generics, `tpy.Ptr` under
`TypeCategory.STRUCTURAL_WRAPPER`) are populated once at module load
via `_populate()` + `_populate_factories()`. Dynamic entries
(records, protocols, enums registered at sema time) are attached
via `attach_dynamic_type_def(qname, category, ...)` and reset
between compilations by `clear_dynamic_type_defs()`.

Conformance tests in `tpyc/test_type_def_registry.py` pin the
invariants: `PRIMITIVE_SNAPSHOT`, `ENUM_SNAPSHOT`, `FACTORY_SNAPSHOT`,
`PROTOCOL_SNAPSHOT`.

### Per-compilation state

State that belongs to one compilation lives on the `Compiler`
instance (added in `_init_shared`, read through
`get_current_compiler()`), not in module-level globals -- the
registry's dynamic slice above is the model.

A fact keyed on a specific *object* has a second requirement: **the
table must own its keys**. A side table spelled `{id(node): fact}`
keeps no reference to the node, so once the node dies CPython is free
to hand its address to the next allocation, and the table then
answers a fresh object with a dead one's fact. Two thirds of the
entries in `SemanticContext.expr_types` belong to nodes that are dead
by the end of a compilation, which is how the property-getter seam
came to read a stale type and reject a valid body in some runs.
`tpyc/identity_map.py` provides `IdentityMap` / `IdentitySet`, which
store the key beside the value; every such table on
`SemanticContext`, `SemanticAnalyzer`, `CodeGenContext`, `Compiler`
and `THIRResumableBody` uses them, and their `__deepcopy__` carries
keys over by identity so a snapshot is still looked up with the live
node. A `WeakKeyDictionary` is not available: AST nodes are plain
`@dataclass`es, so they define `__eq__` and are unhashable -- which
is why `id()` was reached for in the first place. Raw `id()` keys
remain sound only where the container is a local recursion guard or
worklist whose keyed objects are alive for the whole call, or where a
live list / the module AST co-owns them; say which in a comment when
writing one.

### Identity invariants

1. **Nominal identity is qname-based; `_module_qname` is not a
   semantic shortcut.** Every builtin singleton -- primitives (int32,
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
   bearing) -- `TypeRegistry.is_subclass_of`'s parent-chain walk
   and the `record_to_ptr` / `record_to_const_ptr` coercions.

3. **Never use `type(x) == type(y)` for nominal-kind dispatch.** Use
   qname/category predicates or explicit `NominalType.name`
   comparison. Two sites in `sema/overloads.py` (`type_matches_numeric`,
   `_structural_match`) used this anti-pattern pre-Phase-D and
   silently matched `list[int32]` against `set[int32]` overloads via
   element recursion after the subclass collapse.

4. **`RecordInfo` carries two module fields.** `RecordInfo.module` is
   the public collapsed name (used for `qualified_name()` and TypeDef
   key); `RecordInfo.defining_module` is the raw uncollapsed
   submodule (used by re-export lookup and codegen C++ namespace
   qualification). Mixing them breaks re-export lookups.

5. **Sema `ctx.module_name` vs. codegen `module_name` differ for
   `__main__`.** Sema uses `"__main__"` for the entry point; codegen
   uses the file-based module name (e.g. `"main"`).

6. **`TpyTypeRef` flows to `TpyType` via `resolve_refs`
   (in `parse/resolve_refs.py`)** which runs early in
   `compiler._analyze_module`. Downstream passes read `TpyType`. New
   passes that read annotation fields must run after this.

7. **Pending\* types share builtin qnames** with their resolved form
   (`PendingListType.qualified_name() == "builtins.list"`), so
   `is_list` / `is_dict` / `is_set` require
   `isinstance(t, NominalType)` guards. Use `_is_concrete_cat` when
   adding new predicates for qnames that have Pending counterparts.

8. **`OptionalType(...)` may return a non-`OptionalType`** due to a
   construction-time collapse: `OptionalType(PtrType(T))` and
   `OptionalType(ReadonlyType(PtrType(T)))` are replaced by their
   inner Ptr (or `ReadonlyType(Ptr)`) directly. `Ptr[T]` is already
   nullable; the wrapper would be redundant (would lower to
   `std::optional<T*>`) and split one C++ shape into two distinct
   TPy types. Callers that act on the returned object must
   `isinstance`-check before reading `.inner` / `.force_pointer_repr`
   / `.uses_pointer_repr()`. The parser emits a warning at the
   user's spelling site (`Ptr[T] | None`, `Optional[Ptr[T]]`, etc.)
   so the redundant form doesn't drift back in.

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
`macros`, `builder_trace`, `function_macros`, `reach_analysis`,
`frame_traits`, `own_copy`, `context`. Error classes live in
`tpyc/diagnostics.py` (see "Compilation pipeline").

`own_copy` holds the owning-slot copy contract of a body whose payload is
still a type parameter. The sinks warn at the body line, hedged, at
DECLARATION time (a library author has no instantiation to consult), and also
record the site as an obligation so that a non-copyable instantiation
(`@nocopy`, or a record with `__del__`) can answer it with the located
error, which takes the hedge's line when the declaring module composes its
diagnostics. A copyable instantiation answers nothing. It is a leaf module
(it imports only `diagnostics`, `typesys` and `identity_map`) so
`compatibility`, `statements`,
`expressions`, `type_ops` and `methods` can all reach it, and it owns
`contains_reference_type` -- the recursive copy predicate -- so the site that
records and the pass that answers cannot ask different questions.
`Compiler._finalize_workspace` drives the discharge for every module before
any module collapses or withdraws its diagnostics.

It is a third deferral channel beside two that look similar, and the
difference is what each one defers:

- `FunctionInfo.representational_type_params` (recorded in a body by
  `compatibility`, discharged by
  `type_ops.compute_representational_subst_params`) carries a CODEGEN fact
  stamped onto each call node, so every call site can hold its own answer
  and nothing already emitted changes.
- `pending_borrow_checks` / `resolve_pending_borrow_checks` (`calls`) defers
  a CHECK the analyzer will run itself, once cross-module mutation facts are
  final.
- `own_copy` defers a PROMOTION the instantiation supplies to a diagnostic
  already in the list. The obligation is a frozen fact of the declaring body;
  the verdict is recorded in the compilation's `OwnCopyVerdicts` table, keyed
  on the obligation by identity, and the declaring module composes its final
  diagnostics from the two (`apply_own_copy_verdicts`), replacing the hedge
  at its own position. That keeps the line where the body put it without a
  program writing into the bodies it instantiates, so a library module's
  analysis carries nothing of any one program.

A further deferred verdict needs no fourth shape -- record the question as an
obligation, hold its diagnostic, and add a route that discharges it.

`reach_analysis` runs after body analysis as a small post-pass that
scans the analyzed module for cross-module `NominalType._module_qname`
references and stores the set of reached defining modules on
`SemanticContext.reached`. The qname-to-module mapping goes through
`module_from_qname` in `tpyc/module_names.py`: registered records
return `RecordInfo.module` directly (so a `@builtin_type` record whose
qname diverges from its defining file's `cpp_namespace` -- e.g.
`Poll`, qname `tpy.coro.Poll`, body in `tpy/_core/_types.py` -- still
routes through the correct header); unregistered qnames fall back to
a prefix walk over registered modules. Codegen consumes the reached
set to drive transitive `# tpy: include(...)` propagation through
native records: when a consumer reaches a native record only via a
field/method chain (and not via a direct import), the chain of
native-module headers it depends on is emitted into the consumer
header. Native-to-native chains are followed explicitly in codegen
since natives have no `.hpp` to chain through.

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
- **Registry-vs-runtime value-type parity** in
  `tests/test_runtime_value_type_parity.py`: `is_value_type` is
  decided twice -- by the registry for the front end and by
  `tpy::is_value_type` in the C++ runtime for every generic body --
  so the test re-derives both lists (each TypeDef's own
  `cpp_formatter`, and the specializations found by scanning every
  header under `runtime/cpp/include/tpy/`) and fails on a value type
  the runtime would give a mutable `T&` slot. The same scan holds
  the `is_send` / `is_sync` overrides, which default to
  `is_value_type` and so must spell out a False.
  `T*` -- the borrow form the runtime mints for a non-value `T` --
  is the one declared exception; user value
  records are out of scope, since codegen emits their
  specialization next to the struct. A runtime type declares its own
  value-ness beside its definition (`span_iter.hpp`, `slice.hpp`,
  `range.hpp`, `varargs.hpp`, `dict_ops.hpp`); `type_traits.hpp`
  holds the trait machinery plus the rows for types with no defining
  header of ours (the `std::` types, the enum-kind default).
- **Byte-identical generated C++** at any boundary: every case under
  `tests/cases/**/expected/` has pinned `include/*.hpp` and
  `src/*.cpp`, compared in the comp phase. The exec phase (build +
  run) skips via a local content-addressed cache keyed on the actual
  generated C++ plus toolchain, so changed codegen always re-execs --
  there is no fingerprint-collision blind spot. `pytest --force-exec`
  re-runs exec for every case regardless of that cache.
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
