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

`tpyc/mir/` provides an internal CFG builder, verifier and dump for scalar
leaves, borrowed/owned plain records, flat tuples, selected Optional payloads
and nonrecursive unions of scalar leaves or borrowed plain records. A scalar
leaf is a value the loan classifier (`typesys.loan_class`) proves inert at
its representation: every fixed-width int, `float`, `float32`, `bool`,
`char`, and enum values (`thir/scalar_leaves.storage_leaf`; the rules are
the B1 contract in `MIR_ANALYSIS_PLAN.md`). An owned leaf is a value type
whose TypeDef declares `owned_leaf` (`int`, `str`, `String`, `bytes`): owned
storage at rest and a readonly borrow at a `const T&` / view parameter,
modeled as a record with an opaque interior (`thir/scalar_leaves.owned_leaf`;
the B2 contract there).
THIR carries immutable borrowed-parameter,
alias-binding, owned-storage and direct-field facts from the existing lowering decisions.
Ordinary monomorphic instance methods also carry a borrowed receiver fact with
the finalized method readonly verdict. MIR maps THIRSelf to the first borrowed
parameter slot and reuses local alias, tuple and field-place operations; reseating
the receiver is forbidden. Bounded constructors have an explicit receiver
initialization before the CFG: every scalar-leaf field comes from a scalar
parameter or literal, then existing body operations run on that same storage.
Initialization is separate from record replacement. Constructor-call summaries
still require an empty tail; body coverage does not imply call-effect coverage.
A record with plain struct bases is the same model: a field is keyed by its
DECLARING record (`THIRFieldIdentity.owner`), a layout spans the inherited
fields in C++ construction order and lists the struct-base ancestors, and a
subclass constructor's definition composes its base's
(`MIR_ANALYSIS_PLAN.md`, inherited records).
Defaults, partial initialization, consuming/generated method variants,
properties, the lifecycle hooks (`__del__`, `__copy__`, `__move__`) and
resumables retain their separate coverage boundaries; other dunder bodies carry
the receiver fact.
MIR uses body-scoped reference holders and explicit alias transfers; scalar
global slots instead carry a qualified module/binding identity and refer to
caller-supplied shared storage. THIR carries the selected scalar global binding
and write permission; MIR does not infer either from C++ names. Same-module
scalar-leaf globals and direct module attributes are covered; from-import names,
reexports, native globals and module initialization remain uncovered.
MIR scalar field places contain dereference and qualified field projections. Distinct
holders can reference the same object, and readonly access does not imply
an immutable referent. THIR also carries selected ordinary free-function
declaration identities and semantic signatures at calls and definitions;
these do not supply call effects or admit general MIR calls. Registration
retains a unique ordinary declaration for signature/identity checks; overloads,
redefinitions and stale cycle signatures stay uncovered. A plain record's
ordinary instance method publishes the same fact at its definition and at each
call that statically resolves to it: the identity names the declaring record
(`THIRFunctionIdentity.owner`; a subclass receiver binds at it) and the
signature's parameter 0 is the receiver,
so MIR schedules and summarizes methods beside free functions
(`MIR_ANALYSIS_PLAN.md`, B3 contract, second half). The body a call runs
comes from `Compiler.callable_body(owner, name, accessor)`, which selects by
role (method, property getter `fget`, setter `fset`; `typesys.accessor_role`)
and collapses an `@auto_readonly` clone pair -- linked where sema makes it,
`TpyFunction.clone_of` on the mutable clone -- to its const clone;
`single_method_body` keeps its one-body-or-None rule for sema's with-exit
check. One builder, `thir/lower/callables.method_callee`, resolves every role
from that body, its declared parameters passing as the body's own
`THIRParam.passing` reads them (`predicates.param_passing`). A getter or
`@auto_readonly` def is one callable whose receiver passes `const_ref` and
whose borrowed result follows the receiver's access at the call
(`THIRCallableSignature.result_follows_receiver`); the call records the
access its receiver is emitted at (`THIRMethodCall.receiver_access`), its
mutable clone publishes the same callee as an access twin, and MIR requires
the two clones to summarize alike. The codegen pass checks a module's
definitions together (`thir/validate.validate_definitions`). Structural module keys
remain independent of rendered C++ namespaces. Selected lambdas and nested defs
also carry complete capture inventories, distinguishing scalar binding references,
scalar snapshots, borrowed record referents and receiver aliases. Closure occurrence
and capture-slot identities are scoped to the enclosing body; source bindings retain
their parameter/local/receiver category. Unsupported inventory is distinct from a
proven empty one. Capture metadata grants no MIR execution or lifetime coverage.
The verifier checks access and definite assignment,
including initialized bases for field stores, but proves no lifetimes. Owned
record slots have explicit construct/copy/move/borrow operations, and a
resolved call handing over an `Own[R]` result (`MIRCall`) initializes or
replaces one as a construct does; a body returning `Own[R]` moves its own
record storage out (`MIR_ANALYSIS_PLAN.md`, owned record results). Supported
pointer-form Optional locals use the same slots: construction borrows distinct
record storage into a nullable holder, OWN replacement redirects that holder,
IN_PLACE replaces its present referent and None clears only the holder. THIR
records payload ownership at the constructor-backed declaration producer;
MIR consumes the effective replacement verdict and retains presence checks.
Their layouts contain only scalar-leaf fields and have no custom special members; construction
requires a complete, pure constructor definition. `MIRDefinitions` indexes and
checks the emitted `THIRConstructor` and `THIRInheritedConstructor` artifacts and their logical layout/member
facts, without reaching back into sema. Each OWN replacement site has distinct
storage; repeated execution reuses that site's body-hoisted backing. IN_PLACE
replacements preserve referent identity. Positive MIR record-write facts
distinguish initial construction, reusable OWN sites and in-place replacement.
Verified scalar-field record construction, copy and move can write reusable
OWN sites, optional backing and IN_PLACE referents in CFG cycles. Fresh scoped
backing uses region initialization; body initialization in a cycle remains
uncovered. Source eligibility and retained aliases are checked independently.
The debug dump exposes write events and possible retained-object conflicts,
using incoming referents and post-write liveness. It exempts the deliberately
rebound holder but retains other aliases, including aggregate payloads.
External origins may alias. These results make no physical lifetime-end or
general safety claim (`MIR_M3_REUSE_PLAN.md`).
Inline scalar Optional/union writes carry initialization or assignment facts
in the same typed `MIRAssign.storage_write` field. The existing selection
solver supplies feasible-point tag sets for a possible payload-end inventory.
Internal inspection combines it with incoming referents and post-write
liveness, reporting retained payload aliases separately from strict freshness
failures. Inspection rejects structural, definite-assignment and selection
errors; all existing public validators/analyses still reject stale aliases.
Same-tag scalar assignment preserves payload lifetime but does not relax that
freshness policy. Record pointer-wrapper writes do not end the pointee.
`--dump-mir` exposes these results only for already covered source bodies;
general cleanup and lifetime safety remain future work
(`MIR_M3_PAYLOAD_LIFETIME_PLAN.md`).
Tuple construction/copy snapshots scalar leaves and borrowed record identities;
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
Optional scalar leaves and nullable borrowed plain records
have explicit absent/present construction, whole-value copies, presence tests
and typed payload projections. THIR records the selected payload layout and
whole-wrapper versus extracted-name reads. Presence verification propagates
finite holder facts and boolean-test implications through CFG joins and loops;
holder writes invalidate their old implications. Presence permits payload
selection only, never a lifetime proof. Nonrecursive unions use the same finite
selection analysis for scalar-leaf alternatives or borrowed plain-record
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
with body identity and declaration kind. The `--dump-mir` debug option uses
the same emitted THIR caches, collecting constructor definitions across user
modules before lowering their bodies. It reports coverage failures explicitly
and does not write generated files. MIR does not run during normal
compilation or affect emission. Unsupported bodies return `MIRNotCovered`, which
is distinct from malformed MIR and never constitutes a safety proof. Broader
place/loan analysis remains planned in `docs/MIR_ANALYSIS_PLAN.md`.

M4.1/M4.2 add a bounded call-summary consumer to this debug path
(`MIR_CALL_SUMMARY_INTERFACE_PLAN.md`). Local evidence comes from validated
acyclic MIR with explicitly audited operations. The post-THIR workspace adapter
inventories all call dependencies and schedules leaves before callers; it does
not reuse the parameter-flow-filtered mutation graph. Pending, opaque and
known-empty remain distinct. Known reader-only, normal-returning scalar-result
calls become `MIRCall` operations, with argument liveness and unknown result
values. Each body retains its immutable summary entries for standalone
validation and analysis. Recursion and general effects/exits remain outside
this slice. No summary feeds back into production
sema, and no new checker authority is introduced.

M4.3/M4.4 add typed parameter-relative scalar-field may-writes and void setters
(`MIR_CALL_EFFECTS_PLAN.md`). Existing dependency facts resolve writes through
aliases at their program points; call-site inspection and forwarding summaries
share that substitution. `MIRCallStmt` represents void calls without a result
slot. Their arguments stay live, while scalar-field writes preserve holder
dependencies and storage engagement. Eager operand order and named-argument
materialization retain conservative effect-aware gates. Layouts and mutable
access are checked before these richer summaries can be consumed.

M4.5 adds whole-parameter borrowed-record return evidence
(`MIR_BORROWED_RETURN_PLAN.md`). The resolved THIR signature retains emitted
result access (a follows-receiver signature publishes its definition's;
MIR derives each call's from the receiver it binds); MIR carries that
fact into standalone return validation.
Leaf summaries collect origins from pre-return dependency states, preserving
alias joins independently of read/write effects. M4.6 substitutes those origins
through actual holders during dependency transfer, before overwriting any
destination. Returned holders feed the existing storage and retention analyses;
forwarding reuses those dependency states. Unsupported origins never certify
an empty result dependency set.

Covered MIR means a complete representable body, not a lifetime-safety
certificate. Workspace analysis and `--dump-mir` can report coverage while
separate storage evidence reports a conflict. Owned record storage is private
to the body and publishes nothing in its summary when the body returns or
hands it over with no borrow of it live at that transfer, or keeps it to its
end and uses it only through its own holders, field accesses, copies and loans
at borrowing passings (`mir/summaries._private_records`).

Verified readonly constructor arguments of an existing borrowed-call
declaration feed the returned-origin substitution through their named holders;
borrowed-expression MIR lowering shares the scalar path's initialization
anchors and evaluation-order proof, and the caller's local backing is its
private storage (`MIR_BORROWED_ARGUMENT_STORAGE_PLAN.md`). `PTR_ADDR` pointer
declarations and reseats share the declaration flush contract in the emitter,
validator and temporary planner; lowering grants them no temporary arguments.
The flush contract describes well-formed emission of THIR, independently of
source admission and the separate lifetime proof.

Every lowered function, constructor and module-init body publishes
`THIRStorageFacts` (`thir/storage_facts.py`) beside its optional temporary plan
(`MIR_STORAGE_ORIGIN_DESIGN.md`). Each backing names one materialized storage
producer by THIR identity: a `THIRArgTemp` or `THIRSlotEmplace` linked to its
plan placement, or an inline full-expression constructor with its enclosing
full expression. The shared plan preserves separate anonymous argument and
named select declaration channels: while-head select slots remain in the
parent scope even when argument temporaries occupy a rewritten iteration.
Select initialization stays at its conditional operand. Missing placement carries an explicit
uncovered reason; full-expression backing needs no temporary-plan placement.
`storage_facts is None` means unpublished.
Supported plain-record local bindings and reseats record obligations even
when their values are names or field paths. Eligible free-function borrowed
returns use the selected callable's borrowed-result contract. Previously
recorded unsupported sinks remain in the inventory. The facts carry no
admission authority; consumers validate them by recomputation against the
exact body, plan and selected return contract, like `validate_plan`.

`mir/storage_adapter.py` binds these facts to internal lifetime evidence.
`MIRStorageRequest` captures the exact THIR function or constructor, plan,
definitions and summary snapshot. The ordinary MIR builder exposes the
backing places as it allocates them and maps each borrowed sink to its final
holder write or return terminator; no second lowering or name-based matching
is involved. `certify_thir_storage` validates the inventory and composes
`mir/storage_evidence.py` over those operations and actual roots, retaining
both THIR and MIR identity. Operation evidence retains the exact demanded
points and explicit backing roots, resolving dead destinations too. It adds
reached local storage to the whole-body checks; parameter-only operations
need neither new backing nor a temporary plan. Storage-only evidence retains
its separate nonempty-root contract. Scope ends, replacement, payload ends and explicit returned origins
must all be supported and free of conflicts. Unknown origins or engagement
withhold certification. Covered inline
constructor temporaries map to their actual MIR roots and use the existing
full-expression lifetime; this correspondence needs no named-argument plan.
Mixed plain-record ternaries use planned optional backing, initialized empty
at declaration and filled at the exact selected operand with `OPTIONAL_ASSIGN`.
They share the existing borrowed-expression CFG and lifetime evidence, with
hook-free movable scalar-leaf-field constructors and stable scalar operands.
Readonly holders retain readonly access to mutable backing. This consumer
allows one emplacement per declaration activation, including fresh loop-body
activations; repeated while-head emplacement and record and/or remain
uncovered. The known plain branch-local alias escape is an internal Conflict
with no gaps. Internal tuple/Optional/union witnesses retain the actual select
root; source wrapper sinks remain unplanned (`MIR_SELECT_STORAGE_PLAN.md`).
Bodies with neither backing nor obligations are distinguished from missing
facts and unknown-origin obligations; none receives an empty proof. Unmapped
or pruned obligations stay uncovered. Production emission does not call
this API. Its gate and compatibility policy remain a separate future decision.

Named scalar-field record arguments additionally share a prepared THIR storage
plan with C++ emission (`MIR_NAMED_ARGUMENT_STORAGE_PLAN.md`). The shared queue
retains declaration scopes and ordered eager/lazy initialization anchors;
emission verifies its actual events against those identities. MIR consumes
that plan for pure constructors at known readonly reader calls, including
synthetic elif scopes and fresh while-condition activations. An outer-region
backedge bridge ends one activation before the next begins. Ordinary range
and native loops also map planned body/else scopes to existing MIR regions;
range counter scope remains the body's parent, while native iterator storage
keeps its outer residence (`MIR_FOR_ARGUMENT_STORAGE_PLAN.md`). Optional lazy
backing uses existing engagement facts. Unknown producers leave the body
unplanned; no C++ names, strings or emission logs supply MIR storage facts.

`mir/liveness.py` computes backward may-liveness over validated MIR with a
predecessor worklist. Its immutable result includes block entry/exit sets,
statement boundaries (index equal to statement count denotes the terminator),
and constructor initialization entry uses. A projected store reads its address
root; a live scalar payload alias also keeps its wrapper live. The debug dump
prints these sets. Production AST last-use and provenance decisions are
unchanged.

`mir/dependencies.py` propagates possible referents per reference-bearing
holder leaf, then selects live dependencies using those liveness sets. Copies
capture referents independently of later source reseats. Origins are local
backing IDs or symbolic external inputs with inline field paths; external
origins may alias. Explicit BODY/CALLER duration facts describe backing roots.
Missing duration or recursive inline paths produce an uncovered result. The
debug dump exposes this inventory, without storage-release or safety authority.

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
type from the .hpp. A small non-template generator's `__next__` (at
most 40 rendered lines) is the one body that still emits `inline`, in
`<mod>_inl.hpp` -- a file nothing includes from a header, only from a
`.cpp` whose headers are all complete (see docs/ASYNC_DESIGN.md "Body
placement", which also covers where a generator expression's frame
goes).

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
builtin stub) may stand on the declaration. Such a callee is
conservatively taken to mutate every argument it is handed, with one
exemption: a slot bound through a call that LENDS the caller's storage
(`MutationCallEdge.lent` -- a combinator result, a handle into it)
propagates only the callee's element mutation (`elem_mutated_params`),
and a body-less callee has no body to write through an element with,
so `next(it)` over `it = zip(xs, ys)` leaves `xs` and `ys` const.

Const inference reads recorded return-borrow roots through
`typesys.recorded_return_borrow_sources`. Receiver inference retains its
inherently-const-view exception; const-method parameter emission subtracts
the recorded roots from mutation facts where the return is const-projected
(`typesys.return_const_projected`) -- an INFERRED-const method whose return
borrows a parameter keeps that parameter in the mutated set, so the
parameter stays mutable and the borrow keeps its declared type. These are
distinct policies over the same body-analysis facts, not a second
provenance analysis. A DECLARED borrow (`borrows=` / `element_of=`) is a mutable use
instead: a call whose result is a mutable reference into its operands marks
every lending operand mutated at the call
(`CallAnalyzer._mark_borrow_result_operands_written`), as an argument at an
open-`T` parameter of a user generic is, climbing an iterator to what it
walks; a read-only result (a readonly operand or container) marks nothing,
nor does a call whose C++ result is a copy. Precise const (operands const
until written through) needs whole-function analysis, not per-reader arms.

Result-representation readers share `typesys.classify_result_representation`.
An explicit position selects synchronous call classification, async payload
classification or the erased callable's declared return spelling. The reader
preserves each position's wrapper handling and exclusions; `value_category`
adapts its answers and `CallableType` renders the erased result. These are
representation decisions, not ownership, access-permission or provenance proofs.

A bodyless binding's result borrows are DECLARED (`borrows=` / `element_of=`,
`FunctionInfo.return_borrows_from` with `element_borrows_from` for the
element-of parameters, `borrow_declared`), and whether one CALL's result
binds as a borrow is decided once, in sema (`CallAnalyzer.stamp_result_borrow`,
for free and method calls alike -- a method's receiver lends as index -1 and
rule A marks it written:
`TpyCallLike.result_form` -- BORROW, REFERENCE_VALUE (a reference into the
operands for the statement), COPY (the C++ hands back a copy) or VALUE (a
value-shaped result handed back by value, lending nothing; lowering and
codegen ask `value_category.call_hands_back_value` /
`call_value_optional` of the node instead of re-deriving it) -- and
`copy_observable`, from the allow-list
`sema.context.proven_lend_roots` -- the one root set the lending verdict, the
loans the result files and the mutable use it makes of its operands all read
-- and the element-source classification `sema.iter_loans.iter_element_source`
the `for` statement shares, keyed on the declared element cursor
`NativeMembers.cursor`, or a record whose `__iter__` hands out a borrowing
view). The emitter wraps every call whose `THIRCall.result_form` /
`THIRMethodCall.result_form` is BORROW, at one site, in
`::tpy::assert_lent(...)` -- an identity that does not build when the C++
hands back a value, so the compiler's borrow verdict and the callee's C++
cannot silently disagree. Every other form renders the bare call (over a
lending source a helper still returns the element, which a holder copies).
Readers read the stamp: `is_rvalue_source` for the C++ value
category,
`value_category.call_result_holdable` for a holder that outlives the
statement, `call_result_live_in_statement` for an argument bound in place
at a const slot and `call_result_is_reference` at a mutable one (a copy
takes a statement temporary there).

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

### Default construction

Whether a type's C++ default construction exists and whether it runs user
code is one typesys fact, `cpp_default_init` (`CppDefaultInit`: `NONE` /
`USER_INIT` / `INERT`), computed by one walk over the storage form (unwrap
-> tuple -> a union's first alternative -> array -> a record's fields and
non-protocol bases) and memoized per record instantiation on the `Compiler`
(`default_ctor_facts`). Builtins the compiler cannot introspect declare it on
the `TypeDef` (`cpp_default_init`).

Codegen emits `X() = default;` by it, and sema's parent-initializer checks
(single- and multi-base) and the demoted ctor-field check reject on NONE.
An open instantiation (a generic definition, or type args naming a type
parameter) answers what the template emits; a type parameter answers
INERT. The Python-level zero-argument rule (`Default` conformance, an
aggregate's `Record()`, and the constructor split-point check;
`_is_default_constructible` in `sema/protocols.py`) is a separate question:
whether TPy can build a fully initialized value from `T()` with no
arguments, which a record with a required-argument `__init__` fails although
C++ gives it `X() = default;`. The two readers differ on purpose: the
placeholder serves C++ positions, while the split point decides whether a
field `__init__` leaves unset may be default-constructed at all, and it
rejects one CPython could only build through a required-argument `__init__`
(or a union field). For an aggregate the rule differs from CPython, whose
`T()` leaves a field without a default unset.

A slot declared before its first value -- a hoist, a walrus
pre-declaration, an annotation-only decl, a match capture, an
`@error_return` bind, a frame field or pending return, a module global --
is built by that C++ default constructor. Every pre-declared position
renders its declarator through `emit_prims.placeholder_init`: `P p{};`
(value-initialized) where `typesys.placeholder_value_inits` holds -- a
`ValueType` record, reached directly or through a tuple, an array or a
union's first alternative, or a type parameter -- and the bare `T x;`
otherwise. A `ValueType` whose `__init__` can be called with no arguments
(USER_INIT) runs it there, once more than CPython; the contract asks such an
`__init__` to be free of observable side effects (LANGUAGE_FEATURES
"Placeholders run the default constructor").

### TypeDef registry

`tpyc/type_def_registry.py` holds a single `TypeDef` per qname with
all behavior (`cpp_formatter`, `is_send`/`is_sync`, `element_of`,
`subscript_borrows`, `is_value_type`, `is_indirecting`,
`boundary_marshal` (crosses the CPython `@export` boundary by copy --
queried by `is_boundary_marshallable`, replacing a C++-type-string set
that could not tell `bytes` from `bytearray`),
`is_borrowing_view` (every value is a borrow handle into storage it
does not own: `StrView`, `BytesView`, `Span`, `varargs`, `SpanIter`,
the dict views; not `CopyIter`, which borrows an lvalue source but owns
a temporary one, a per-call fact), `iter_yields_owned_elements` (iterating
hands out elements the adapter owns, never a reference into what it walks:
`CopyIter`, `OwnIter` -- the element-copy warning's provenance walk stops
there), `iter_yields_ref_tuple_proxies` (an
internal stopgap for dict_items, whose iteration yields proxy reference
tuples -- drives the resumable-frame borrow-tuple loop binding; set by the
private stub kwarg `_iter_yields_ref_tuple_proxies` and slated for removal), `needs_explicit_element_target`,
`param_kinds`, `type_factory`, the loan-model facts `loan_inert` (a
value holds no borrow and lends no storage), `owned_leaf` (a value owns an
opaque buffer a borrow can point into and holds no borrow), `owns_elements`
(a native container: it owns its type arguments' values as elements, holds
what they hold, and a borrow can point into its elements), `native_members`
(which type arguments an element-owning type or a borrowing view stores or
views as members: `NativeMembers(element, value, keyed, cursor)`, positions
among its type parameters), `copy_may_raise`
(copying it can throw a C++ exception a bare `except:` catches),
`compares_fixed_ints` (the runtime compares it with every fixed-width int),
`primitive_ops` (the primitive-operation contract: runtime operators that
may allocate, printed with no user method), `zero_value` (the value of
value-initialized storage) and `param_passing` (the parameter convention,
where the default derivation from `is_value_type` does not spell it),
category payloads `int_traits`, `float_traits`, `enum: EnumInfo`,
`record: RecordInfo`, `protocol: ProtocolInfo`). `loan_inert`,
`owned_leaf` and `param_passing` are read through `typesys.loan_class`,
`typesys.is_owned_leaf` and `TpyType.param_passing` (`owns_elements`
through `typesys.loan_class` and `scalar_leaves.native_container_type`);
`zero_value` is read
directly (`zero_value_of`, by `mir/lower.py` and `mir/validate.py`);
`primitive_ops` and `compares_fixed_ints` through
`thir/scalar_leaves.primitive_leaf` and the `typesys.certified_primitive_*`
certificates; `copy_may_raise` by `mir/lower.py` and `mir/validate.py`. The `is_indirecting`
flag is set from `@native(..., indirecting=True)` on the stub class --
it flows parser -> `TpyRecord` -> `RecordInfo` -> `TypeDef` during
`attach_dynamic_type_def`. Cycle detection consults it to decide
whether a native wrapper type breaks recursive size cycles; structural
TPy records (with a `Ptr[T]` field) are recognized separately by
walking `RecordInfo.fields` under type-param substitution in
`tpyc/cycle_detection.py` and do not need the flag. The two borrow
facts and `owns_elements` follow the same path, from
`@native(..., borrowing_view=True)`, the internal
`_iter_yields_ref_tuple_proxies=True` and `@native(..., elements=True)` on
the stub (`_DECLARED_NATIVE_FLAGS`); no TypeDef declares `owns_elements`
statically, so the registry holds no list of containers. `native_members` is
computed once when the stub's record is registered
(`sema/registration._declared_native_members`: the type parameter the
readonly `__iter__` yields, the one `__getitem__` returns, whether that
subscript is keyed) and latched by `latch_native_members`; consumers read it
at a type's arguments through `scalar_leaves.declared_members` and
`scalar_leaves.binds_cursor`. A builtin
stub's declared facts are also latched onto its static TypeDef when the
stub is PARSED (`Compiler._pre_populate_decl_exports`), so no module's
registration order can read the unlatched default. The compiler holds no
list of view types: every borrow/lifetime consumer asks
`is_borrowing_view_type(t)`, which reads only the flag. The fact has one
render consumer too: a native template spells a `readonly[T]` argument
`const T` only when it is a borrowing view (`spells_readonly_arg_const` in
`tpyc/typesys.py`; a storage template keeps `T`). Both read the flag through
`declares_borrowing_view`. `Span`, `varargs` and `SpanIter` apply the same
rule in their `cpp_formatter` without reading it (they render before their
stub is latched), and `latch_declared_native_flags` rejects a stub for them
that does not declare the kwarg or names a different C++ template. Outside a
compilation (no stubs attached, e.g. unit tests) `is_borrowing_view_type`
answers False for every view while those formatters still spell `const T`,
which is why they remain until types render only from their stubs.
Dispatch is qname-based:

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
That identity is also what lets sema change a node's KIND in place: a
`@property` read becomes its getter `TpyMethodCall` at the end of the
read analysis (`become_method_call`, `tpyc/parse/nodes.py`), and every
fact already filed against the object -- its type, its loc, its
narrowing facts -- survives because the tables are keyed on the object
and not on a copy of it.
`tpyc/identity_map.py` provides `IdentityMap` / `IdentitySet`, which
store the key beside the value; every such table on
`SemanticContext`, `SemanticAnalyzer`, `CodeGenContext`, `Compiler`
and `THIRResumableBody` uses them, and their `__deepcopy__` carries
keys over by identity so a snapshot is still looked up with the live
node. The same holds for what a snapshot HOLDS, not just what it is
keyed by: `FunctionTrackingState.__deepcopy__` seeds its memo with
every parse node and registry `FunctionInfo` it reaches, so a restore
never installs a clone of something a later phase compares with `is`
or stamps a fact on. That walk stops at a `TpyType`, so a node
reachable only through one (`PendingGenericInstanceType.expr`) is
still cloned. The live scope, namespaces and function node
(`LIVE_HANDLE_FIELDS`) are not copied at all, for a stronger reason
than identity lookups: the enclosing analysis goes on binding into
them after the restore. A `WeakKeyDictionary` is not available: AST nodes are
plain `@dataclass`es, so they define `__eq__` and are unhashable -- which
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
  on `TypeDef.enum`, not on a separate subclass. An enum's methods live on
  a companion record (`EnumInfo.companion`) that method and property
  lookup reach through `TypeRegistry.receiver_record`; `get_record_for_type`
  never answers it, so no record gate sees an enum as a record.
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
`init_tracker`, `scope_tracker`, `iter_loans`, `flow_facts`, `value_range`,
`numeric_lattice`, `mutation_propagation`, `method_expansion`,
`macros`, `builder_trace`, `function_macros`, `reach_analysis`,
`frame_traits`, `frame_close`, `loop_frames`, `own_copy`, `slot_hint`,
`pending_num`, `context`.
Error classes live in `tpyc/diagnostics.py` (see "Compilation pipeline").

`may_interrupt` decides, per analyzed body and at the end of its Phase-1
analysis (no fixpoint), whether running the body may reach a Ctrl-C check
point (`FunctionInfo.may_interrupt`, False only for an inert body: builtin
operations on types whose `value_ops_run_user_code()` is False and calls to
unmarked bodyless bindings); the record, hash and frame emitters read it to
open the `::tpy::DeferSignals` scope of a `noexcept` body.
`frame_close` decides, per generator or coroutine function, whether closing
its frame may run user code (`FunctionInfo.frame_close_runs_user_code`, a
module-end fixpoint); the frame layout and the loop rules read it through
`TpyType.drop_runs_user_code`. `loop_frames` rejects a generator held across
passes of a loop (or, in a generator or async body, past a `with` block) that
may write what it borrows, failing closed on anything it cannot trace -- the
interim rule until MIR owns that hazard (docs/IR_DESIGN.md, "Loop-bound generators that borrow storage
the loop writes").

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

`pending_num` owns the type of a literal-seeded function local (integer or
float), the one numeric fact sema decides after the fact rather than in
source order. The prescan decides it by each local's FIRST BINDING
(`scan_first_bindings`): one statement, or the sibling arms of one
`if` / `match` / `try` that each bind the name the statement did not see
bound. It records those first-binding groups once, per function
(`ScanResult.first_bindings`, kept as
`FunctionTrackingState.first_bindings`), and the pending locals
(`scan_pending_num_locals`) are those whose first binding stores a bare
literal and that no excluded form also binds (a loop target, a tuple
unpack, a walrus, `with` / `except` / `match` bindings, a nested `def`,
`nonlocal` / `global`, an annotation); a local first bound to a value has that value's type, a numeric
type constructor call included, so there is no declaration resolver. The
same table refuses an annotation that stands past a local's first binding
(`StatementAnalyzer._refuse_later_annotation`). Each pending one gets
a cell on the context (not on the function state, so a settle inside a
rolled-back overload trial stays made) that collects the types stored in it,
and its reads are typed `PendingNumType`. A group of sibling arms shares one
cell, typed arms included (a derived cell whose evidence is the arms'
values; a later store must fit it), so the arm read first does not fix the
type; `PendingNums.check_arm_group` is the group's verdict -- the typed arms
join, and a bare-literal arm beside them is refused unless the family default
widens into their join -- run when the cell settles and again at
`settle_all`, where every arm has been seen. The type reaches only a consumer
that asked for it by naming the node it analyzes (`PendingNums.sink`,
checked at both expression entry points in `expressions`); every other
consumer settles the local on the spot. Operators and conversions over a
pending value are recorded as `DeferredIntOp`s and resolved through the
ordinary `OperatorResolver` / `check_type_compatible` once their operands
settle; `LocalTypeDeduction.resolve_all` settles the rest and rewrites every
recorded type, so nothing after sema sees a pending one.

The element of a list literal is the same kind of fact, decided per numeric
LEAF. A non-empty list literal whose element holds numbers bound to an
unannotated function local owns a cell per leaf: the element itself for a
list of scalars, each numeric member of a tuple, the element of a nested
row at each depth. The element of its `PendingListType` is a type tree whose
leaves are the `PendingNumType`s naming those cells, every part that holds
no number keeping the literal's type; `PendingListType.inner_types` exposes
the tree whenever it holds a leaf, so the settle sweeps and the finalization
rewrite reach leaves at any depth. The record names the leaves by path from
the container (`ContainerLiteralInfo.elem_cells`: `ELEM` the element, then
an int a tuple member, `ELEM` again a row's element), and
`PendingNums.container_cells` hands a reader the `ContainerCells`
descriptor -- the record, the tree (the record's pending type), the cells
in path order, and each cell's path in this container's tree (a row's own
record roots its leaves at the row, the cell's `path` is from the
container first bound, which the refusals name: *(tuple element 0)*,
*(row element)*). Birth
(`PendingNums.new_tree`) collects the values at every leaf path from
every element and every row -- not from the joined element, which keeps one
row only -- and classifies each leaf literal or typed as a scalar birth
does; the rows met on the way take their part of the tree. Rows at one
position are one C++ type, so the records of the rows at one position form
a REPRESENTATION group (`ListLiteralInfo.row_group`, `join_rows`): a row
appended or stored later joins it, and every requirement for a vector on
one member -- a mutation through `g[0]`, a list context on one row, a
differing size, an empty row, an alias that needs one -- is closed over the
group before any record resolves (`LocalTypeDeduction._close_row_groups`,
asking the one predicate the resolution asks, `_container_form`), so the
outer list embeds the type all its rows get. Every copy of the list's
type refers to the same cells, so an element read (`ys[i]`, `xs[0][1]`,
`pop`) is typed by them and follows whatever decides them. ONE structural
zip pairs the tree with another type part by part (`pending_num.zip_parts`:
path, the tree's part, the other's part there), and every pairing walk is
that zip with its own leaf rule -- the family check of a store, the fit
with a typed container, the store itself, the adoption of a container's
non-numeric parts, the rows a `list` context reaches. Stores are each
leaf's evidence (`PendingNums.tree_store` -> `elem_store`; a row stored
through `g.append(v)` or `g[0] = v` alike), a list stored as a row is
linked to the row leaf by leaf (`PendingNums.link`, the one live link,
also a rebinding's and a birth value's that reads another list's leaf: a
shared type constraint, not aliasing -- the row is a copy), and a typed
list stored as a row is a typed container the row element meets. The
store records what it admitted (`ListLiteralInfo.stored_rows`), and the
store's own compatibility check reads that verdict instead of judging the
list again. A list with an undecided element reaches only a
consumer that named the node (`PendingNums.list_sink`: a subscript or method
receiver, `print`, a second name, a row read bound to a name), a declared
slot the value is then coerced to that holds a typed container of numbers,
or, as a literal-element view, the arguments of a call whose one candidate
is generic (`CallAnalyzer._adaptive_list_args`); `_pending_gate` settles the
cells for every other consumer. A value that holds leaves by value -- a
tuple read from a list of tuples -- passes one composite gate
(`ExpressionAnalyzer._pending_composite_gate`, over
`pending_num.value_leaves`): a consumer that named it (the receiver of
`xs[0][1]`, `print`, a tuple unpack, which binds each target to its leaf,
a comparison operand) sees it undecided, every other settles all its
leaves; the declarations, unpack targets and return values that hold such
leaves are rewritten when they settle (`PendingNums.defer`). The deferral
waits for every leaf of the types it names, and a type recorded at a value
that holds a row -- a call's signature at the element
(`PendingNums.when_elem_known`), a local bound to a row (`row = g[0]`) --
is recorded again once the rows' list types are resolved
(`FunctionTrackingState.after_list_resolution`), since that happens after
the settle. That is the single-pass rule numeric locals have
(this module's docstring: nothing is analyzed twice; a use that needs the
type decides it and a later wider store names that use), chosen on
2026-09-29 over re-analysis to a fixed point (TODO.md "Whole-function slot
and element facts"). The slot is the gate's parameter:
`ExpressionAnalyzer.analyze_at_slot` is the analysis whose caller coerces
the value to the hint, and a select or a tuple literal analyzed that way
hands the promise to its operands and elements, which are the values the
slot receives. Which slots hold such a container is ONE predicate,
`PendingNums.slot_containers` (the slot itself, an annotated list local,
the member of an optional slot, each member of a union slot), read by the
gate, by the decision and by the diagnostic spelling of the stored list
(`TypeCompatibility.diag_type`). The typed container decides the element in
ONE place, the coercion check (`TypeCompatibility._list_at_container` ->
`PendingNums.meets` / `elem_context`): the container's element is paired
with the tree part by part (`fits_container`; a part that holds no number
must be compatible as it is, and is then taken at the container's type),
each leaf's container type is one more the leaf holds, the leaves settle,
and each must then equal the container's, so a container confirms or
widens and never narrows. The whole candidate is validated before any leaf
is decided. That check decides only
under its `commit` argument, which `check_type_compatible` -- the call that
produces the coercion -- passes (and `list_at_slot`, for a select operand
the select's own coercion no longer reaches), and which `_check_compat`
hands down only where it peels a wrapper off the same value and slot,
pairs a tuple's elements, or re-checks the union member that admitted the
value; a query (`is_type_compatible`), a probe inside a producing check
and a check inside an overload trial get the verdict alone. A list handed
out undecided that no coercion reached is settled at its statement's end
from what it holds (`FunctionTrackingState.awaiting_container`, per
function state so a generator expression's body leaves its statement's
alone). A list whose first binding holds typed values has no cell to
widen: it is settled at that binding (`PendingNums.decide_at_birth`), as
a local first bound to a typed value has that value's type, and every
later store, container and generic context must fit it.
How a list method or a protocol uses the element -- not at all, as a
value in or out, inside another type, as values the list then holds
(`extend`, `__iadd__`: each is a store, `PendingNums.store_elements`) -- is
asked of the stub by the one container classifier
(`tpyc/sema/list_elem.py`, below).

An empty list (`[]`, `list()`) bound to an unannotated function local has
no family until something gives it one, so its cell is born at the first
evidence. `PendingNums.cell_list` answers "a cell list, born or not" -- the
cells, or the record of an empty list that may still take them
(`seedable`) -- and every store and context site switches on it once. One
birth helper (`PendingNums.new_tree`) serves the literal binding
(`StatementAnalyzer._bind_list_elem_cell`, a comprehension of rows
included, whose one row record is its element's) and the lazy birth alike:
a literal starts a default-based cell, a typed value decides it there, so
an empty list behaves as the literal its first value would have written --
a tuple or a row seeds a tree as `[(1, 2)]` / `[[1]]` would. The
evidence is a value stored into it (`seed_by_store` / `seed_by_stores`,
reached from the store chokepoint `LocalTypeDeduction.observe_store`, a
subscript store through `store_value`, and `store_elements` for `extend` / `+=`), a typed container
it meets under `commit` (`seed_by_context`, from
`TypeCompatibility._empty_list_at_container`, which also takes a declared
numeric view, and from the select pin), or a list literal it is rebound to
(`share_cell`). A value the sink let through pending because the list was
seedable, which then seeded nothing, is forced in one place
(`PendingNums.unseeded`). The alias and rebinding edges
(`ListLiteralInfo.source_literal_id`) point one way and are no rooted tree,
so birth sets the cell on every record connected to the seeded one
(`PendingNums._connected`, cycle-safe), and names the cell after the first
binding's record. A type taken before the birth -- a name read, or a
binding a loop scope restored -- still shows the unknown element; nothing
patches it in place: `list_cells` keys on the record, so any reader that
asks for the cells finds them, the cell-reading helpers (`list_as_known`,
`list_so_far`) replace the element, and finalization rewrites such a node
to the record's resolved type (`SemanticContext.set_expr_type` records it,
`LocalTypeDeduction._finalize_pending_in_bindings`). Lists no cell
covers (module-level, non-numeric, and an empty list a nested body stores
into first, `BUGS.md#empty-list-nested-first-store-rejected`) keep the
older element record on `ListLiteralInfo` and its read guard; no
function-local list whose element holds numbers reaches that path.

Dict and set literals bound to an unannotated function local take the same
cells, and the three kinds are ONE family. A container literal's type is a
`PendingContainerType` -- `PendingListType`, `PendingDictType`,
`PendingSetType`, written or empty -- carrying its record's `literal_id`
and the steps to its parts (`STEPS`: `ELEM` for a list or set, `KEY` /
`VALUE` for a dict); its record is a `ContainerLiteralInfo` (the list's
subclass adds the storage facts, Array or list), and the kind is the
type's class. Every record lives in ONE table by its literal id
(`SemanticContext.container_literals`), a function's records and its
names for them in one list and one map (`pending_resolutions`,
`variable_to_literal`); work for one kind filters that table (the list
storage facts through `list_literal`; the resolver resolves the lists
before the dicts and sets that may hold them). What differs per kind is a
fact of the pending type's class: its steps, the containers it is declared
as (`declares`), the literals it is written as (`written_as`), how a hint
spells it (`HINT_NAME`, `INITS`) and its empty record (`new_record`). A record's TREE is its pending type (`pending_type()`), so a
dict, a set and a list are rooted alike and every walk (`tree_leaves`,
`map_parts`, `zip_parts`, `at_path`, `path_words`, birth's `_collect`, the
pairing with a declared container by `pairs_as` and `container_parts`)
descends tuples and container nodes by their steps. `container_cells` is a
record lookup by the literal id (`SemanticContext.container_record`); a
dict or set inside another container's tree is a node with a record of its
own (`part_of` names the container whose cells it shares), so a read of it
(`dd["a"]`, `ls[0]`) finds the cells by identity too. A store is ONE ENTRY
(`Entry`: a type and the written expression per step), so `store_value`,
`tree_store` and birth treat a list's `append`, a subscript store of any
kind, `add`, `setdefault` and the entries of a written literal alike;
`extend` / `update` / `|=` store their source's entries through
`store_elements`: the source is read open, a container inside its entries
(a row, an inner dict) is linked to the receiver's part (`_link_source`)
-- or, typed, must be what that part is -- and then its numbers settle,
and the runtime update helpers take the source at its own instantiation
and convert each scalar entry, losslessly only (`dict_update`,
`set_update`, `set_symmetric_difference_update`, each constrained by
`widens_to` in `runtime/cpp/include/tpy/type_traits.hpp`). A dict or set
has no storage decision once its leaves settle: a settled one reaches a
consumer that did not name it at its settled form
(`PendingNums.settled_form`, the container its parts spell; a list keeps
its pending type for the Array/list decision), and a method on any
container is resolved at `receiver_form` (the dicts and sets inside it
spelled), so no consumer needs a pending dict or set arm. What a container
method does with an argument that names a part -- insert it, or only look
it up -- is the stub's own declaration, `@native(..., element_effect=...)`
(`FunctionInfo.native_element_effect`), read by ONE classifier over the
three stubs (`list_elem.container_call`, parameterised by the stub's
type-parameter -> step map); a method that names a part -- by an argument,
or by a bound of its own on a class type parameter (`def sort[T:
Comparable]`, which builds no type: `ContainerCall.bounds_parts`) -- with
no declared effect decides the container first (`sort`,
`intersection_update`). Every method call on a cell container goes through
`MethodAnalyzer._cell_container_method`; an argument that names an
unsettled leaf is passed at that leaf (`PendingNums.part_leaf` /
`pass_at_leaf`: a literal adapts to it, a typed value goes through the
pending conversion to it, judged once the leaf settles; another numeric
family is refused), and so are the operand of `in`, the key of a
`d[k]` read and of `del` when the stub's `__contains__` /
`__getitem__` / `__delitem__` declares an effect, so the shared
overload resolver has no pending-leaf rule. A list has no
`__contains__`: `in` compares with the part its `__iter__` yields
(`list_elem.iterated_step`), a lookup as its `remove` / `index` /
`count` declare. A looked-up value is passed at the leaf only when the
part holds it losslessly so far; any other (a wider width, an `int`,
the other family, a pending local, a part that is no single number)
decides the container first, at every lookup spelling of the three
kinds, methods included (`PendingNums.lookup_decides`, asked by
`ExpressionAnalyzer.decide_for_lookup`), so the lookup then takes the
path a decided container takes and never converts its operand down; a
looked-up value reaches its leaf only while the part is open
(`PendingNums.lookup_leaf`). A `get` / `pop` default is handed back, not
compared (`ContainerCall.compares`), and stays a value passed at the
leaf. A call on a container
no cell decides -- an empty one, a module-level or non-numeric one -- goes
through
`MethodAnalyzer._join_container_method`, classified by the same
`ContainerCall`: the arguments that name every part are one entry stored
through `LocalTypeDeduction.observe_named_store` (the first numbers seed
the cells, anything else joins the parts), whatever the call does with
them. That path reads no effect on purpose: such a container is empty,
so the call's parts are its first evidence, or it is a module-level or
non-numeric one, whose parts are known and against which a looked-up
argument still type-checks -- it is not drift from the stub. An empty
container's `extend` / `update` / `|=` seeds through
`store_elements` on the cell path. The
resolved signature of such a call and of an in-place operator is recorded
again once the cells settle (`MethodAnalyzer._settle_elem_method_later`,
`StatementAnalyzer._resolve_inplace_later`), and a record resolves through
`finalize` of its tree (`_resolve_pending_dict_and_set_types`). An empty
container's first entry goes through one entry,
`LocalTypeDeduction.observe_store`: the cells take it (`store_value`,
which seeds through `seedable`), else the parts the container learned from
its uses join it -- the home of the module-level and non-numeric
containers no cell covers; the typed-context arms
(`mark_container_param_context`, `mark_container_return_context`) switch
on the cells once at their top.

Who owns what: the prescan decides which locals are pending, before the
body is analyzed. The `SemanticContext` holds the cells
(`pending_num_cells`), so a settle made inside an overload trial outlives
its rollback, as the scope it publishes to does. The per-function state
(`FunctionTrackingState`) holds the deferred operations
(`pending_num_deferred`) and the conversion placeholders that settled to no
conversion (`pending_num_splices`); a trial snapshots and restores them with
the rest of that state, so they resolve only outside a trial
(`SemanticContext.trial_depth`) -- a resolution inside one would be rolled
back and replayed. At the end, `settle_all` splices the empty placeholders
out of the body, checking that each was found, and `assert_settled` refuses a
body that still holds an unfilled one.

`slot_hint` holds the type-hint value. A `SlotHint` has one of two kinds:
DECLARED (the source states the slot's type; it converts an int into a float)
and INFERRED (the hint's type plus the unsubstituted pattern it came from, so
each position knows whether an inferred local decides it -- such a position
types what the value leaves open and converts nothing). The context carries one
(`SemanticContext.slot_hint_scope`); `ReturnSeed` (`type_ops`) builds every
argument hint from a call's return-hint seed. An INFERRED hint may be FILL-ONLY
(`SlotHint.fill`, its `fill_node` set): the one NON-GENERIC overload candidate
an inferred local alone would pick hands each argument its declared parameter
type that way. A fill-only hint belongs to its argument node alone -- only
`SemanticContext.slot_hint_at(node)` returns it, `expr_slot_hint` hides it, and
no projection keeps it -- and is retargeted to a ternary's arms and an and/or's
operands. It types a literal's or empty constructor's open positions (as a
plain inferred hint, `as_local`), seeds a generic call's or record
construction's arguments, and in `TypeOps.result_bindings` binds only the type
parameters that neither the arguments nor a `@type_param_default` bind. A
lambda gets none, as a declared local's probe gives it no hint. A generic
candidate takes the local's type as its return-hint seed like any generic call.

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
  so the test re-derives both lists (each TypeDef rendered through its
  own `cpp_formatter` -- or, for the dict views, which are spelled from
  their stubs' `@native` names, through its factory and `to_cpp()`
  once an empty compilation has attached the stub records -- and the
  specializations found by scanning every header under
  `runtime/cpp/include/tpy/`) and fails on a value type
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
