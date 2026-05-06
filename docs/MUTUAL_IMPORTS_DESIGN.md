# Mutual Imports Design

This document describes the implementation plan for accepting cyclic
cross-module imports in tpyc. It is the result of a multi-round design
exploration; the architecture chosen here is a workspace-wide global
pipeline modeled on Rust-style item collection + name resolution before
type checking.

## Problem

tpyc currently rejects any two-file mutual import unconditionally with
`Circular import detected: a -> b -> a`, even when the imported names
are used only as type annotations. The standard CPython idiom

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from a import A
```

also fails because the parser only handles top-level imports;
`ImportFrom` nested under `If` triggers `Unsupported statement:
ImportFrom`.

Two reproducers from the downstream issue report:

1. **Type-only mutual reference (annotations only).**
   ```python
   # a.py
   from b import H
   class A:
       def go(self) -> None:
           H(self)

   # b.py
   from a import A
   def H(x: A) -> None:
       x.go()
   ```

2. **Pure value-level mutual reference.**
   ```python
   # a.py
   from b import H
   def K() -> None: pass

   # b.py
   from a import K
   def H() -> None: K()
   ```

Both produce the circular-import error. The downstream workaround --
making cross-file helpers generic over a fresh type parameter
(`def H[M](x: M)`) -- emits a one-instantiation template and triggers
adjacent issues, so users want a real fix.

## Why not SCC-aware sema

The first design iteration (v1-v3) added an SCC-aware path to the
existing per-module-with-publishing pipeline. Each review round
surfaced new gaps:

- Declaration shells (parser-time `RecordInfo`/`FunctionInfo` minted
  for cross-SCC visibility) weren't rich enough; methods, constructors,
  generic type-params, and macro-emitted methods all needed inclusion.
- `bind_imports` resolves `from b import H` against `ModuleInfo.
  functions`, which aren't registered until late in the registration
  pipeline. Fixing this for cycle members required either a
  declaration-first pass or splitting `bind_imports` into "register
  placeholder" and "resolve to peer FunctionInfo".
- Parser-time `RecordInfo` placeholders leaking into sema introduced
  stale state that survived later full registration.
- `is_value_type` is a sema-time fact (set during record validation).
  Function signature normalization with `make_ref()` depends on it,
  so cross-SCC normalization required the fact to be computed
  workspace-wide before any signature normalization ran.
- Class macros emitting cross-SCC references couldn't be sequenced
  cleanly within partial-publish rounds.
- C++ header forward-declaration strategy was load-bearing but
  initially under-specified (mutual `#include` with `#pragma once` is
  order-dependent within a single TU; not all positions are
  forward-declarable).

Each gap was fixable, but the patch count was a code smell. The SCC
approach was trying to add a special path for cycles while keeping the
per-module-topo-order structure. The structural cause was the latter,
not the former.

## Architecture: workspace-wide global pipeline

Rust and Zig sidestep this whole problem by separating name collection
from type checking. Rust collects every item from every module into a
single workspace-wide symbol table before type-checking starts; the
order modules were declared or imported is irrelevant because there is
no per-module ordering. Zig is even more permissive via lazy
demand-driven analysis.

tpyc adopts the Rust-style pipeline. Two registry surfaces:

**Workspace qname index (shared).** Single store keyed by qualified
names (`pkg_a.Foo`, `tplib.json.parser.JsonError`). Every analyzer
reads from it. Mutations follow an explicit ownership rule.

**Per-module short-name bindings (per-analyzer).** What `Foo` means in
pkg_a versus pkg_b stays per-module. Python's import semantics demand
this. Each analyzer's local short-name table reflects its imports plus
its own declarations.

The qname-keyed surface is what becomes order-independent and
cycle-friendly.

### Ownership rule

An item (`RecordInfo`, `FunctionInfo`, `ProtocolInfo`, etc.) is
mutated only by code processing its *defining* module. Concretely:

- All declaration-time mutations (fields, methods, parents,
  `mro_ancestors`, `init_params`, `class_constants`, `is_value_type`,
  protocol facts, function signatures) happen during Phase 5
  (workspace declaration finalization).
- All body-sema-time mutations (`direct_mutated_params`,
  `transitive_mutated_params`, `call_edges`, inferred `is_readonly`)
  happen during Phase 6 (per-module body sema), gated by
  `originating_module == self.ctx.module_name` for the non-idempotent
  ones.
- After Phase 5, declaration-time fields are frozen
  (`_phase = 'finalized'`). After Phase 6 for module M, M's body-sema
  fields are frozen.

Debug assertions enforce both transitions during Phases 5 and 6 of
the rollout; once stable, they remain in builds compiled with checks
enabled.

### Three pipeline shifts

1. **Item collection becomes workspace-wide on the qname-keyed
   surface.** A single pass over all parsed modules registers names
   and unresolved DTOs.
2. **Type resolution becomes workspace-wide.** Every `TypeRefNode`
   resolves once across all modules.
3. **Per-module sema becomes order-independent in capability.**
   Topological order remains used in practice for runtime init
   sequencing but isn't a sema correctness requirement.

Cyclic imports become a non-issue. Cycles where layout would require a
C++ completeness cycle (by-value cross-references through fields,
containers, tuples, unions, `Own`, value-variants, or concrete
inheritance) get targeted rejection.

### Compatibility with the IR migration

The work sits above the `lower_module(ast, analyzer) -> THIR` boundary
in `IR_DESIGN.md`. THIR consumes a fully-analyzed module per-module
after sema; this plan changes how sema is *driven*, not what it
produces. The structure aligns better with THIR's "self-contained
per-module" property than today's per-module-with-publishing model.

## Goals

- Accept both cyclic-import shapes from the issue, including cycles
  with class-macro-decorated records.
- Preserve current behavior for every workspace today, with snapshot
  churn limited to one cosmetic qname-rendering policy decision.
- Replace per-module-with-publishing orchestration with a cleaner
  workspace-wide model.
- Targeted diagnostics for shapes still rejected.

## Non-goals (v1)

- `if TYPE_CHECKING:` / `from __future__ import annotations` parser
  support -- separate work.
- Top-level statements with side effects in cycle members.
- Cross-module by-value cross-references in cycles (C++ layout
  requires complete type -- rejected by the completeness graph gate).
- Re-export facades inside cycles (`__init__.py` or `# tpy:
  native_module` member of an SCC).

## Open design questions

These should be confirmed before Phase 0 starts:

1. **Qname rendering policy.** Today's codegen renders surface qname
   (`tplib::json::JsonError`) via `using` declarations from the
   package init header. The spike showed shared modules causes a flip
   to defining qname (`tplib::json::parser::JsonError`). Both compile.
   Recommendation: **defining qname** -- unambiguous, one-time
   snapshot churn for ~3 affected cases.

2. **Workspace-wide vs intra-module Phase 2 fixpoint.**
   Recommendation: **keep intra-module** for v1; Phase 9 is optional
   cleanup.

## Spike validation

A throwaway spike on branch `spike-workspace-registry` verified the
core feasibility: `TypeRegistry.modules` re-backed by a single shared
dict passed through `SemanticAnalyzer.__init__` from `Compiler`. Three
files changed; full test suite ran:

- 577/577 unit tests passed.
- 3396/3400 snippet tests passed.
- 4 failures, classified:
  - 3 cosmetic (qname rendering policy -- both forms compile due to
    `using` declarations in the package init header).
  - 1 real correctness regression (`cross_module_same_name_union` --
    short-name lookup re-resolves a `NominalType` ignoring its
    `_module_qname`, leading to wrong-namespace codegen). This is the
    bug Phase 0 fixes up front.

The hypothesis -- workspace-wide modules dict is a viable substrate --
holds. The caveat is that qname disambiguation must be load-bearing
in v1, addressed in Phase 0 before any structural change.

## Phases

Each phase is independently shippable. Phases 0-6 are pure refactors
with no user-visible behavior change. Phase 7 is the first
user-visible change (cycles compile). Phases 7 and 8 ship paired
(cycles need the header strategy to actually build). The user can ship
any prefix and stop.

### Phase 0 -- qname disambiguation audit + fix (~1-2 days)

Address the latent bug surfaced by the spike before any structural
change.

- Trace `cross_module_same_name_union`: pkg_a's `def name_of(x: Foo)`
  annotation, where `Foo` is pkg_a's local but spike emits
  `::tpyapp::pkg_b::Foo`. Identify the lookup path that re-resolves
  the type by short name instead of using `NominalType._module_qname`.
- Audit every short-name lookup for ambiguity-when-shared. Likely
  suspects:
  - `TypeRegistry._user_qname_index` writes during `register_module`
    (typesys.py:3741).
  - Codegen type-rendering paths that re-resolve `NominalType` by
    short name instead of trusting `_module_qname`.
- Add a debug assertion that flags any short-name lookup whose result
  doesn't match the requested `_module_qname` (when present). Run
  full corpus, fix any hits, then remove the assertion.

**Acceptance.** Full test suite passes on master (no spike change
yet). Pure refactor -- no observable behavior change.

### Phase 1 -- workspace-wide qname index (~0.5 day)

Land the spike's `shared_modules` change as production code. After
Phase 0's fix, this is now safe.

- `TypeRegistry` accepts an optional
  `shared_modules: dict[str, ModuleInfo]` keyed by qname. Default
  unchanged when not provided.
- `SemanticAnalyzer.__init__` plumbs it through.
- `Compiler` creates one `_shared_modules` per invocation, passes to
  every analyzer.
- Per-module short-name binding (analyzer-local `records`,
  `functions`, `_protocols_by_local_name`) stays per-analyzer --
  workspace surface is *only* `modules: dict[qname, ModuleInfo]`.
- Decide qname rendering policy. Regenerate ~3 affected snapshots
  (`tplib/json_reader`, `json_reader_escapes`, `panic_json_malformed`).

**Acceptance.** Full test suite passes. Spike's 4 failures resolved.

### Phase 2 -- factor `analyze()` into 5 sub-phases (~1.5 days)

Extract `tpyc/sema/analyzer.py:313-552` into 5 publicly callable
methods on `SemanticAnalyzer`: `bind_imports`,
`register_records_and_protocols`, `register_signatures`,
`analyze_bodies`, `run_phase2_fixpoint`. Internal helpers preserved.
Public API at 5 prevents external reordering.

Phase-counter assertion: each public sub-phase records the phase it
just completed; calling them out of order is a hard error.

**Acceptance.** Full test suite passes with zero output diff. No
fingerprint regenerations.

### Phase 3 -- workspace-wide name + unresolved-DTO collection (~1.5 days)

Single workspace pass after parsing, before any resolution or sema.

Add `_collect_workspace_dtos()` to `Compiler`. Walks every parsed
module's AST and emits, per module:

- `UnresolvedRecord`: name, parent type-refs, field name + type-ref
  pairs, method name + signature-ref triples, decorator metadata.
- `UnresolvedFunction`: name, param name + type-ref pairs, return
  type-ref, decorator metadata, overload group membership.
- `UnresolvedProtocol`: name, method name + signature-ref triples,
  parent protocol refs.
- `UnresolvedEnum`: name, members (enums need no resolution -- fully
  resolvable from parser).
- `UnresolvedTypeAlias`: name, body type-ref.
- `UnresolvedGlobal`: name, type-ref, init expression.

The DTOs are pure data. No `RecordInfo`/`FunctionInfo` exists yet. This
is what prevents parser-stub leakage: sema objects are only minted in
Phase 5.

The per-analyzer setup loop at compiler.py:1602-1640 becomes a no-op
for cross-module ModuleInfo -- workspace populated.

**Acceptance.** Full test suite passes. No behavior change -- same
items collected, just stored as DTOs instead of being directly mutated
into per-analyzer registries.

### Phase 4 -- workspace-wide type resolution (~1.5 days)

Move `_resolve_module_refs` (compiler.py:1587) out of
`_analyze_module` into a workspace-wide pass that runs after Phase 3,
before Phase 5.

- Walk every DTO across all modules and resolve every `TypeRefNode`.
- Parser type resolver consults workspace-wide
  `parser_registries: dict[str, ParserRegistry]` so cross-module type
  names resolve uniformly.
- Outputs are still DTOs but with `TpyType` slots populated where
  TypeRefNodes used to be.
- Per-module `_canonicalize_import_sources` becomes a no-op (or
  removed) -- canonicalization happens workspace-wide.

**Acceptance.** Full test suite passes. Phase-counter assertion
catches any regression.

### Phase 5 -- workspace declaration finalization (~2.5 days)

The structural addition. Materialize real sema objects from resolved
DTOs, run class macros, validate inheritance, compute `is_value_type`,
and normalize function signatures -- all workspace-wide, in a clear
sub-phase order.

Sub-phases (each runs across all modules before the next begins):

1. **Instantiate enums and records (skeletal).** Mint
   `RecordInfo`/`NominalType` objects from DTOs. Fields and parents
   are set with resolved types. Methods are NOT yet attached.
2. **Run class macros.** For each record across the workspace, apply
   class macros (`@dataclass`, `@model`, etc.). Macros run with the
   workspace registry as their type-query surface; they may emit new
   methods/fields. Macro emissions are appended to existing DTOs and
   re-resolved (sub-pass for the appended TypeRefNodes).
3. **Attach methods.** With macros done, attach all methods (declared
   + macro-emitted) to their records.
4. **Register protocols.** Mint `ProtocolInfo` objects, methods
   attached.
5. **Validate inheritance + protocol parents.** Walk parent chains;
   reject ill-formed.
6. **Compute `is_value_type` and `nocopy` flags.** Validate
   `ValueType` field constraints. Propagate `@nocopy` from fields to
   containing records.
7. **Detect recursive unions.** Tag recursive aliases.
8. **Materialize type aliases.** Register validated aliases.
9. **Materialize function signatures (`FunctionInfo`).** Now that
   records have `is_value_type`, `make_ref()` correctly identifies
   which types need `RefType` wrapping. Normalize signatures across
   the whole workspace.
10. **Materialize globals.** Inject synthetic `__name__`, register
    Final declarations, etc.

After Phase 5, every declaration-time field of every
`RecordInfo`/`FunctionInfo`/`ProtocolInfo` is final
(`_phase = 'finalized'`). Body sema in Phase 6 only writes body-sema
fields.

**Macro execution detail.** v1 runs macros per-module within the
workspace pass, in topological order over the import graph (within
an SCC, alphabetical). Each macro execution still uses today's
`_apply_class_macros` helper but invoked from the workspace driver
rather than from inside `register_record`. Workspace registry is
fully populated at this point so macros can reference any peer type.

**Cycle-member macros are now legal.** Because Phase 5 runs all
macros before any body sema, cyclic-import members can carry
`@dataclass` etc. without breaking order-independence in Phase 6.

**Acceptance.** Full test suite passes. Snapshot stable.
Ownership-boundary debug assertions fire zero times.

### Phase 6 -- order-independent body sema + ownership-boundary discipline (~1.5 days)

After Phase 5, declarations are fully materialized. Per-module body
sema can now run in any order.

- Body sema phases: `analyze_top_level`, `analyze_class_constants`,
  `expand_builder_traces`, `analyze_record_methods`,
  `analyze_function_bodies`, `run_phase2_fixpoint`.
- Add `originating_module: str | None` to `FunctionInfo`. Set during
  Phase 5 sub-phase 9 to the defining module's name. Leave None on
  opaque FIs (builtin, native, `@builtin_function` stubs).
- Phase 2 mutation propagation gate:
  `(fi.originating_module == self.ctx.module_name) AND (fi.call_edges
  is not None)`. Strictly more conservative than today.
- Body-sema mutation audit: walk every write to
  `RecordInfo`/`FunctionInfo` during body sema. Confirm each is to a
  body-sema field and gated on
  `originating_module == module_name`.
- Run sema in arbitrary order during testing (reverse topo,
  alphabetical) to surface any remaining order-sensitivity.
  Production runs in topo order for stability.

**Acceptance.** Full test suite passes. Sema run in reverse topo
order produces byte-identical generated code.

### Phase 7 -- accept cyclic imports + completeness-graph reject gate (~1 day, paired with Phase 8)

Drop the cycle raise at compiler.py:1076-1081. Replace
`_compute_compile_order` with Tarjan-aware version that handles
cycles. Reconstruct cycle paths via DFS+back-edge for diagnostic
continuity.

`_reject_unsupported_cycle(scc)` enforces v1 conservatism. The reject
gate covers:

- **Top-level statements not in allowlist.** Only `TpyImport` and
  `TpyVarDecl(init is None, codegen confirmed inert)` allowed for
  cycle members. Any other top-level statement rejected with a
  pointer to the specific statement.
- **C++ type-completeness cycles** computed from the *resolved
  type-dependency graph*, not the import-SCC member set. Build a
  directed graph where node = module, edge `M -> N` exists if `M` has
  any type-defining position requiring `N`'s complete type:
  - field of type `N.X` (by-value)
  - field of type `list[N.X]`, `dict[K, N.X]`, `set[N.X]`,
    `tuple[N.X, ...]`, `Own[N.X]`
  - field of value-variant type containing `N.X`
  - record concrete-inherits from `N.X`
  - function with by-value `N.X` param/return where the function
    definition needs full layout
  - type alias with `N.X` in a complete-type position
  Any SCC in this graph (regardless of import SCC) is rejected with a
  diagnostic naming the offending positions and modules.
- **Re-export facades** in cycles (`is_package_init` /
  `native_module` / `implicit_stdlib` in SCC).

Class macros on cycle members are **not** rejected (Phase 5 handles
them workspace-wide).

Pair with Phase 8 -- Phase 7 alone enables sema cycles but codegen
would fail to build them.

**Acceptance.** Both issue repros compile. Repro 2 reshaped to be
CPython-valid or marked `no_cpython.txt` per Phase 0 decision.
Conservative-gate cases hit targeted errors with snapshot
diagnostics.

### Phase 8 -- C++ header strategy (~1.5 days, paired with Phase 7)

Brief 0.5-day spike to enumerate forward-declarable cross-module type
positions and pick the implementation strategy.

**Forward-declarable positions** (can use `<dep>_fwd.hpp`):

- Pointer/reference function parameters (`Ptr[T]`, `T&` via `Ref`
  wrapping).
- Pointer/reference fields.
- Function return type when by-pointer or by-reference.

**Positions requiring complete type** (must `#include <dep>.hpp`,
hence rejected by Phase 7 gate when crossing a completeness cycle):

- By-value fields, including in containers, tuples, unions, `Own`,
  value-variants.
- Concrete inheritance from peer record.
- By-value function param/return where the function definition needs
  full layout.
- Enums used by-value.
- Dynamic protocol adapters / `RefAdapter` instantiation.
- Templates / generic record instantiations.
- Recursive type alias bodies.

Phase 7's reject gate uses this list to determine
"complete-type-required" edges in the dependency graph.

For per-module `_fwd.hpp` split implementation:

- Each module emits `<mod>.hpp` (full) and `<mod>_fwd.hpp`
  (struct/enum forward declarations).
- Cross-module fwd-decl-only references `#include "<dep>_fwd.hpp"`.
- Cross-module complete-type references `#include "<dep>.hpp"`.
- Non-cyclic modules: behavior verifiably equivalent to today; the
  new fwd headers are benign and unused unless a complete-type cycle
  would otherwise be triggered.

Test cases for: cyclic types used by-pointer, by-reference, function
with by-pointer cross-cycle param, peer-protocol structural
conformance.

**Acceptance.** Issue repros build end-to-end. Header tests pass. No
regression on existing test corpus.

### Phase 9 -- workspace-wide Phase 2 fixpoint (optional, ~1 day)

Optional cleanup. Extend mutation propagation from intra-module to
workspace-wide.

- `_propagate_mutation_facts` collects FIs across the whole workspace.
- The `originating_module` distinction from Phase 6 stays (gates
  per-module local-fact treatment); cross-module mutating calls now
  propagate facts uniformly.
- Better precision for cross-module mutation reasoning.

Skip if scope feels too aggressive for v1.

### Phase 10 -- tests + docs (~1 day)

New `tests/cases/imports/` group:

- `mutual_value_only/` (issue repro 2; reshaped or `no_cpython.txt`).
- `mutual_type_annotation/` (issue repro 1).
- `mutual_three_module_cycle/` (a -> b -> c -> a).
- `mutual_alongside_dag/` (cycle of 2 inside a workspace with
  non-cyclic peers).
- `mutual_cross_module_inheritance_from_protocol/` (protocols are
  templates; allowed).
- `mutual_cycle_with_dataclass/` (cycle member with `@dataclass` --
  newly legal).
- `error_mutual_top_level_init/`.
- `error_mutual_in_package_init/`.
- `error_mutual_by_value_field/`.
- `error_mutual_by_value_in_container/` (e.g. `list[B.Y]`).
- `error_mutual_concrete_inheritance/`.

Update `LANGUAGE_FEATURES.md`. Update `ARCHITECTURE.md` describing the
global pipeline and ownership rule.

## Phase summary

| # | What | Type | Days |
|---|---|---|---|
| 0 | Qname disambiguation audit + fix | Pure refactor (latent bug fix) | 1-2 |
| 1 | Workspace-wide qname index | Refactor + qname policy | 0.5 |
| 2 | Factor `analyze()` into 5 sub-phases | Pure refactor | 1.5 |
| 3 | Workspace-wide name + unresolved-DTO collection | Pure refactor | 1.5 |
| 4 | Workspace-wide type resolution | Pure refactor | 1.5 |
| 5 | Workspace declaration finalization | Pure refactor (structural addition) | 2.5 |
| 6 | Order-independent body sema + ownership discipline | Pure refactor | 1.5 |
| 7 | Accept cyclic imports + completeness reject gate | First behavior change (paired with 8) | 1 |
| 8 | C++ header strategy (with spike) | Paired with 7 | 1.5 |
| 9 | Workspace-wide Phase 2 fixpoint | Optional cleanup | 1 |
| 10 | Tests + docs | | 1 |

**Total: ~13-15 days** (~12-14 if Phase 9 skipped).

## Stop-points

- After Phase 1: cleanest qname behavior, no behavior change, fixes
  a latent bug.
- After Phase 6: full Rust-style global pipeline with workspace
  declaration finalization. Cycles still rejected. Architecturally
  satisfying; ownership rule fully enforced.
- After Phase 8: cycles work end-to-end, including with class macros.
- After Phase 10: shipped.

## Rollback story

Phases 0-6 are pure refactors. Each is independently revertable. If
Phase 7/8 surface an issue, revert them as a pair; the rest of the
pipeline remains and the project is meaningfully better than before
-- Phases 0-6 fix latent bugs (Phase 0 disambiguation, Phase 5
ownership discipline) and clean up orchestration.

## Implementation deviations from the plan

The shipped implementation diverges from the doc above in two places.
Both deviations are pragmatic; recording them here so future readers
can tell intent from accident.

### Skeleton mutation in place vs. unresolved DTOs

The doc's Phase 3 / Phase 5 split builds *unresolved DTOs* (pure data
with `TypeRefNode` slots), defers any `RecordInfo` /
`FunctionInfo` / `ProtocolInfo` allocation until Phase 5 sub-phase 1,
and then materializes sema objects from those DTOs.

The shipped pipeline pre-populates real (but skeletal)
`RecordInfo` / `FunctionInfo` / `ProtocolInfo` / enum `NominalType`
objects in `Compiler._pre_populate_decl_exports` and stashes them in
the workspace-shared `compiled.exports`. Cycle peers' `bind_imports`
captures references to those skeletons. Each registration site
(`register_record`, `register_protocol`, `register_function`,
`register_overload_group`) adopts the corresponding skeleton via
`_adopt_skeleton(skeleton, full)`, which copies every dataclass field
of the freshly built object onto the skeleton in place. Peers'
captured references therefore see the freshly-finalized payload
without a second resync pass.

Why: keeps peer references *live* without an extra DTO-to-sema
materialization pass. Trade-off: every registration path that mints
the canonical sema object must remember to adopt the skeleton --
forgetting the call leaves peers bound to the empty placeholder. The
adopt sites are localized to the registration helpers and guarded by
a name-equality assertion.

### `analyze_top_level` runs in declaration sub-phase 3, not body sub-phase 4

The doc puts `analyze_top_level` in the body sema phase. The
shipped pipeline calls it from `register_signatures` (sub-phase 3,
declaration finalization) instead.

Why: peer modules can write `from X import VAR` where `VAR` is a
top-level variable whose type emerges from analyzing the RHS of a
tuple-unpack assignment (`lo, hi = get_bounds()`). If
`analyze_top_level` ran in the body phase, peer `bind_imports`
would not yet see `VAR` in `X.exports.variables` and the import
would fail. Hoisting top-level statement analysis into sub-phase 3
makes globals visible by the time any module finalizes
declarations. Cross-module function calls in top-level statements
resolve against decl-finalized peer `ModuleInfo`s (deps run first
in topo order in the declarations pass).

Trade-off: sub-phase 3 contains a small amount of body-sema-shaped
work. The ownership-rule guards that body sema relies on
(`originating_module` gating) still apply because top-level
statement analysis writes only to the *current* module's globals.

### Completeness gate scope -- v1 limitations

The completeness-graph reject gate
(`Compiler._reject_completeness_cycle_in_scc`) walks the resolved
type-dependency graph for SCC members and rejects by-value
cross-cycle peer references at:

- Concrete inheritance.
- Record fields, including descents through containers, tuples,
  unions, `Own`, value-variants, and generic-record `type_args`
  (`Box[B]` where `B` is a cycle peer).
- In-header-bodied function and method *signatures*: any function
  whose codegen emits its body in the `.hpp` (generic /
  `@cpp_template` / Fn-typed param / static-protocol param) and any
  method that codegen keeps inline-in-struct (record-generic /
  method-templated / `__init__` / `__del__` / overload-dispatched).
  Walks return type and each parameter via the same generic-aware
  `_find_complete_required_peer` used for fields.
- Recursive type alias bodies (TPy synthesizes a wrapper struct for
  recursive aliases; same complete-type constraint as a record
  field).

Two consequences of v1's coarse header strategy are visible to
users today; both surface as the underlying C++ build error
rather than a structured TPy diagnostic. They share a single root
cause -- the v1 gate rejects on edges in the type-dependency
graph, but the original design called for rejecting only on SCCs
in the **header-completeness graph** (see Future Work). v2 closes
both at once.

- **Body-only peer use in an inline / header-emitted body.** A
  generic / Fn-typed / inline method whose *signature* contains no
  cycle peer but whose *body* uses one (e.g. `def f[T](x: T) -> T:
  tmp = B(1); return x` in cycle peer `a`, where `B` is a
  value-type peer from `b`) is not caught -- the inline template
  body in `a.hpp` references `B` by concrete name but the `.hpp`
  only includes `b_fwd.hpp`. Sema is correct; the failure is a
  self-imposed codegen artifact.
- **Asymmetric cycles where one direction would compile.** When
  `a.hpp` legitimately needs `b.hpp` complete but `b.hpp` only
  needs `a_fwd.hpp` (e.g. `b.py` imports `a.helper` only to call
  it from `b.cpp`), today's strategy still uses fwd-only headers
  in both directions and a templated `a` definition fails to
  build. Most visibly, this means **a dead import** on one side
  of a cycle can break otherwise-correct code on the other side:
  `b.py`'s unused `from a import helper` registers the
  import-graph cycle and switches `a`'s peer includes to
  fwd-only, even though `b.hpp` itself does not need any of `a`'s
  complete types.

(Non-recursive type aliases like `Alias = tuple[B]` are *not* a
separate limitation: at use sites the alias gets expanded, so a
field of type `Alias` is caught by the field walk and a parameter
of type `Alias` to an inline function is caught by the function
walk -- both via the existing generic-aware
`_find_complete_required_peer`. Out-of-line consumers of an alias
do not fail because the `.cpp` includes the peer's full header.)

### `_normalize_function_info_refs` is order-dependent inside SCCs (latent)

`SemanticAnalyzer._normalize_function_info_refs` runs once per
module at the end of `register_signatures`. It calls `make_ref()`
over every `FunctionInfo`'s param and return types, which consults
`is_value_type()` on the underlying `RecordInfo`. Inside an SCC the
alphabetically-earlier module's normalization runs while the later
peer's records are still skeletons, so `is_value_type()` answers
`False` on those skeletons and `make_ref` wraps the cross-module
reference in `RefType` even when the peer's record will end up as a
`ValueType`. The wrapping is preserved by a `RefType` idempotency
guard, so subsequent peer-side normalization does not unwrap it.

This is currently unobservable: codegen reads parameter and return
types from the parser AST (`TpyFunction.params` /
`TpyFunction.return_type`) rather than from
`FunctionInfo.params` / `.return_type`, and the sema consumers of
`fi.params` (overload resolution, generic-call inference, method
dispatch) explicitly call `unwrap_ref_type()` before comparing
types. So the wrong wrapping is dead state given the current
codegen and sema shape, and given that TPy does not yet support
passing imported functions as first-class values into generic
`Fn[[], T]` consumers (the path where the wrong return-type
wrapping would otherwise leak through inference).

Future work that consumes `FunctionInfo.return_type` without
unwrapping -- richer first-class function support, more aggressive
return-by-reference codegen, or any LSP-style introspection that
reports inferred function types -- would surface this as a real
bug. The clean fix is to split `register_signatures` into a
register-raw-signatures sub-phase that runs workspace-wide before
any module's normalization sub-phase, so all `is_value_type` flags
are final before any `make_ref` wrapping happens. Until that
becomes load-bearing, the staleness is documented here as known
debt rather than fixed.

## Future work (post-v1)

Items that are deferred from v1 with concrete plans for closing
them. Each one is a real gap, not declared out-of-scope; landing
order is open.

### Header-completeness SCC gate (the right Phase 7 + Phase 8)

**Why this is the headline item.** The current implementation
collapses Phase 7's "C++ type-completeness cycles computed from
the resolved type-dependency graph" into a coarser predicate:
"any cross-cycle by-value reference in any in-header position."
This over-rejects, and -- worse -- makes a *dead import* on one
side of the cycle break otherwise-correct code on the other side:
an unused `from a import helper` in `b.py` registers the import-
graph cycle, switches every peer header to `_fwd.hpp` form, and a
generic `def make[T](x: T) -> T: tmp = B(7); ...` in `a.py` then
fails at the C++ build because `a.hpp`'s template body sees only
`b_fwd.hpp`. The sema is correct; the failure is a self-imposed
codegen artifact from the simplified header strategy.

**Symptoms it closes.**
- Body-only peer use in inline / header-emitted bodies (the
  `tmp = B(7)` example above).
- Cycles where `a.hpp` legitimately needs `b.hpp` complete but
  `b.hpp` only needs `a_fwd.hpp` (asymmetric completeness needs).
  These are accepted at sema today only when no by-value
  cross-cycle reference appears in either signature or field; the
  current gate rejects more than the underlying C++ rules
  require.

**Plan.** Two coordinated changes -- one in sema, one in codegen.
They land together because the gate's decisions and the
include-strategy decisions read the same graph.

1. **Header-completeness graph.** Build a directed graph keyed on
   modules; nodes are `(module, header_position)` slots, edges
   flow from a module's `.hpp` to each peer whose **complete
   type** that `.hpp` requires. Sources of edges:
   - Concrete inheritance from a peer record.
   - Record fields whose type contains a peer by value (already
     covered by the v1 walker, including descents through
     containers, tuples, unions, `Own`, value-variants, generic-
     record `type_args`).
   - Inline-emitted free function or method *signature* with a
     peer by value (already covered).
   - Inline-emitted free function or method *body* that
     constructs / type-ascribes / `isinstance`-checks / generic-
     instantiates a peer record. (This is the body walk; the
     v1 gate omits it.)
   - Recursive type alias bodies (already covered).
   - Generic-record definition in module M whose own body
     references a peer by value (caught by field walk today,
     re-stated here for completeness).
2. **SCCs are the reject set.** Compute Tarjan SCCs over this
   graph. Reject only SCCs (with a diagnostic that names both
   ends of the offending cycle, not just one position). A chain
   `a -> b` with no return edge is *not* rejected.
3. **Codegen reads the same graph.** For each module's `.hpp`,
   emit `#include "<peer>.hpp"` (full) when there is no return
   edge from peer back to module within the same import-SCC, and
   `#include "<peer>_fwd.hpp"` only when there is. Symmetric
   completeness cycles (which the gate now rejects) never reach
   codegen; asymmetric cases get the full include and compile.

**Why it's the right shape.**
- Subsumes today's gate as a special case: when every cycle
  member has both incoming and outgoing complete-type edges, the
  graph is one big SCC and the behavior matches v1 exactly.
- Makes dead-import scenarios "just work": `from a import helper`
  with `helper` unused for type completeness contributes no edge
  in the header-completeness graph. The import-graph cycle stays
  registered (it's a real edge for runtime init order) but it
  doesn't poison the header strategy.
- Subsumes the body-only-use limitation. The body walker is the
  same one a body-only reject gate would need; here it feeds a
  graph-edge decision rather than a hard reject.
- Aligns with what Phase 7 of this doc *already specified* --
  v1 implementation took a shortcut, this closes back to the
  designed behavior.

**Cost estimate.** Codex framed it as "weeks, not days." The
biggest pieces of new code:
- Body walker over resolved expressions/statements after Phase 2
  fixpoint, collecting `NominalType` references in completeness-
  required positions. `compute_reached_symbols` is the closest
  existing precedent.
- Header-completeness graph builder, parameterized over a
  registry-of-records + per-module function/method/alias
  inventory. Tarjan over the result.
- Codegen change in `generator.py:_module_to_include_path` /
  `BuildLayout.fwd_hpp_path` consumers: per-peer-direction
  fwd-or-full decision instead of the current "all peers fwd."
- Tests covering: chains (asymmetric, accepted), genuine SCCs
  (rejected with both-ends diagnostic), body-only refs that close
  a cycle, body-only refs that don't.

**User workarounds while v1 stands.** (a) Move the inline body
that constructs the peer into a non-generic, non-Fn-param wrapper
(so its definition lands in `.cpp` where the peer's full header
is included). (b) Hold the peer through a pointer / reference /
`@dynamic` protocol position. (c) If the cycle edge is purely a
dead import, removing the import works -- but this is a
work-around, not a recommendation: the import is sema-correct
code and the compiler should accept it.

**Test re-classification when v2 lands.** The v1 gate's coarse
per-edge rejection produces several `tests/cases/imports/error_*`
cases whose underlying programs are *asymmetric* in the header-
completeness graph and would compile under v2. When the SCC gate
ships, the following tests should flip from `error_*` to a
supported `mutual_*` test (cycle accepted, runtime output
asserted):

- `error_mutual_by_value_field` -- a stores B by value;
  b only takes A by reference. Asymmetric.
- `error_mutual_by_value_in_container` -- a has `list[B]`;
  b only takes A by reference.
- `error_mutual_concrete_inheritance` -- a inherits from B;
  b only takes A by reference.
- `error_mutual_generic_function_by_value` -- a's generic
  function takes B by value; b only references a via a function
  call (not by-value record).
- `error_mutual_init_param_by_value` -- a's `__init__`
  takes B by value; b only references a via a function call.
- `error_mutual_inline_method_by_value` -- a's generic
  method takes B by value; b only references a via a function
  call.
- `error_mutual_overload_method_by_value` -- a's overload-
  dispatched method takes B by value; b only references a via
  a function call.
- `error_mutual_static_proto_param_by_value` -- a's
  static-protocol-typed function takes B by value; b only
  references a via a function call.
- `error_mutual_template_function_by_value` -- a's
  Fn-typed function takes B by value; b only references a via
  a function call.

The v1-only error tests for symmetric cases (the residual that v2
also rejects) stay as error tests:

- `error_mutual_symmetric_by_value_fields` -- A stores B
  by value AND B stores A by value. C++ cannot lay out either
  record under any header strategy.
- `error_mutual_symmetric_inline_templates` -- a's
  inline template returns B by value AND b's inline template
  returns A by value. Each `.hpp` would need the peer's full
  layout for its inline body to compile, with no header strategy
  satisfying both directions.
- `error_mutual_symmetric_inheritance_and_field` -- a
  concretely inherits from B AND b stores A by value. Mixed-
  shape symmetric cycle: a.hpp needs B's layout for the
  base-class subobject; b.hpp needs A's layout for the field.
  v2 rejects with a both-ends diagnostic. (Note: direct mutual
  concrete inheritance, `A(B)` and `B(A)`, is rejected by a
  separate circular-inheritance gate independent of any header
  strategy.)
- `error_mutual_top_level_init` -- executable top-level
  code in cycle members; orthogonal to the header-completeness
  graph, stays rejected on order-independence grounds.
- `error_mutual_in_package_init` -- re-export facade
  inside a cycle; orthogonal to header completeness, stays
  rejected on facade semantics.

Under v2 the diagnostics for symmetric cases should additionally
name *both* ends of the offending SCC edge, so the user sees
which other peer's position pairs with theirs.

### Workspace-subphase-by-subphase declaration pipeline

Closes the `_normalize_function_info_refs` order-dependence
documented above. Splits `register_signatures` into a workspace-
wide register-raw-signatures pass followed by a workspace-wide
normalize-signatures pass, so every record's `is_value_type` is
final before any `make_ref` runs. Currently latent; promotes to
real bug when first-class function support / LSP introspection /
return-by-ref codegen lands.

### Mutation-propagation across cycle borrow checks

Phase 9 in the original plan -- workspace-wide Phase 2 fixpoint
for `direct_mutated_params` / `transitive_mutated_params`. The
current per-module fixpoint with workspace `registry.modules`
collection works for present test shapes; an explicit cycle-aware
fixpoint would tighten cross-module borrow reasoning.

### Better cycle diagnostics

Today's reject diagnostics name the offending position and the
SCC, but pin to a heuristic source line (first cross-cycle import,
or the field/function definition). The header-completeness SCC
gate above naturally improves this -- with both ends of each SCC
edge available, the diagnostic can name "field X in module a uses
peer record Y in module b by value, AND method m in module b uses
peer record Z in module a by value -- one of these edges must be
broken." Until then, the v1 diagnostics pin to one end and the
user has to find the other.
