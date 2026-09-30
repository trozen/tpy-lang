# M3 completion checklist

Current planning index, updated 2026-09-22. Implementation exists through
M3.27; review/merge status is separate. The numbered increment plans below record
their own bounded contracts, not completion of this checklist.

The unchecked items are now scheduled under the breadth-first B-steps of the
[MIR analysis plan](MIR_ANALYSIS_PLAN.md#breadth-first-order); each carries
its step as a prefix. The W1-W5 groups remain the dependency view, not the
order.

## Completion contract

The [MIR analysis plan](MIR_ANALYSIS_PLAN.md#scope-matrix-and-remaining-increments)
defines M3 as full required CFG/regions, liveness and holder/loan propagation.
Its exit gate includes exceptional cleanup, destruction, suspension/resume,
joins and dependencies carried by every relevant alias, aggregate and closure.
The concrete consumer requirements remain in
[CALLABLE_PROVENANCE_REQUIREMENTS.md](CALLABLE_PROVENANCE_REQUIREMENTS.md).

An unsupported body is an explicit coverage gap, never evidence of safety.
M3 completion cannot be obtained by silently narrowing those requirements.
Conversely, this checklist does not require MIR-backed C++ emission, SSA,
exact-index disjointness or move optimization.

## Implemented foundation

These boxes describe the current admitted subset only:

- [x] Scalar CFG, evaluation-order gates, body-scoped places, strict validation
  and `--dump-mir` inspection.
- [x] Backward liveness and forward reference dependencies through supported
  record, tuple, Optional and union holders, including conservative joins
  ([M3.1/M3.2](MIR_M3_LIVENESS_PLAN.md)).
- [x] Bounded backing reuse and internal logical replacement conflicts
  ([M3.3/M3.4](MIR_M3_REUSE_PLAN.md)).
- [x] Inline scalar wrapper payload-end events and retained-alias inspection
  ([M3.5/M3.6](MIR_M3_PAYLOAD_LIFETIME_PLAN.md)).
- [x] Normal emitted storage regions, activation resets and retained-reference
  inspection at scope exits ([M3.7/M3.8](MIR_M3_REGIONS_PLAN.md)).
- [x] Late direct declarations and bounded ordinary hoists
  ([M3.9/M3.10](MIR_M3_DECLARATIONS_PLAN.md)).
- [x] Literal/not boolean edges and coherent unreachable inventory cleanup
  ([M3.11](MIR_M3_CONSTANT_CFG_PLAN.md)).
- [x] Explicit physical scalar-wrapper initialization, independent of source
  assignment, with validation and lifetime consumers, and scalar Optional/union
  hoists from THIR ([M3.12/M3.13](MIR_M3_WRAPPER_STORAGE_PLAN.md)).
- [x] Optional-backed record hoists with separate wrapper construction, record
  engagement and source assignment, including logical overwrite and normal
  scope-end inspection ([M3.14/M3.15](MIR_M3_RECORD_STORAGE_PLAN.md)).
- [x] Flat inline-record tuple backing, internal mixed ownership, normal
  lifetimes and bounded constructor-literal THIR producers
  ([M3.16/M3.17](MIR_M3_TUPLE_STORAGE_PLAN.md)).
- [x] Immutable whole aliases and chains of constructor-owned body-local
  tuples, normalized to their original backing
  ([M3.18](MIR_M3_TUPLE_ALIAS_PLAN.md)).

## Remaining work packages

These are dependency groups, not promises of one commit each or a percentage
estimate. Each implementation batch must name the boxes it closes and leave
the others visible.

### W1: storage lifecycle

- [x] Admit scalar Optional/union hoists with actual default selection facts.
  Physical construction is distinct from source assignment in M3.12;
  M3.13 connects the THIR producer ([wrapper storage plan](MIR_M3_WRAPPER_STORAGE_PLAN.md)).
- [x] Model optional-storage record hoists: wrapper lifetime, record engagement,
  source availability and per-binding backing identity. Do not substitute the
  existing per-operation BODY OWN-site model. The
  [M3.14/M3.15 plan](MIR_M3_RECORD_STORAGE_PLAN.md) records the approved bounded
  batch. M3.14 implements the internal model; M3.15 connects positive THIR facts
  for bounded plain-record if/while hoists.
- [ ] **B1** (view temporaries: B2). Cover remaining owning/mixed aggregate
  backing and materialized expression
  temporaries, with positive producer facts for placement and each write.
  The [approved M3.16/M3.17 batch](MIR_M3_TUPLE_STORAGE_PLAN.md) covers flat
  constructor-owned local tuples, with mixed ownership at the internal IR
  boundary. M3.16 implements the internal model; M3.17 connects THIR producers.
  Nested/selected aggregates, mixed source
  producers and expression temporaries remain separate work.
  The approved [M3.22/M3.23 batch](MIR_M3_EXPRESSION_TEMPORARIES_PLAN.md)
  models hook-free record constructor full-expression temporaries. M3.22
  supplies the internal lowering contract; M3.23 connects scalar constructor
  reads/discards in ordinary expressions and conditions. Aggregate
  materialization remains open.
  The [named argument storage plan](MIR_NAMED_ARGUMENT_STORAGE_PLAN.md)
  adds shared THIR placement and bounded MIR consumption of hook-free
  scalar-field record arguments at known readonly reader calls. Eager/lazy
  initialization, if/elif scopes and fresh while activations are covered;
  unplanned bodies and richer argument materialization remain open.
  The [ordinary-for storage extension](MIR_FOR_ARGUMENT_STORAGE_PLAN.md)
  connects the shared plan to existing range/native loop bodies and else
  scopes; loop-head materialization remains separate.
  The [range-head investigation](MIR_RANGE_HEAD_STORAGE_PLAN.md) found that
  constructor-argument bounds also need source lowering, operand sequencing
  and explicit cleanup boundaries; general call-effect design is recommended
  before implementing that extension.
  The [named argument temporary investigation](MIR_NAMED_ARGUMENT_TEMPORARIES.md)
  identified the need for the M3/M4 call-summary interface. M4.1/M4.2 now
  cover bounded calls with stable arguments. The named-storage consumer uses
  that interface while retaining actual emitted block lifetime rather than
  full-expression lifetime. These slices do not close W1.
  [M3.18](MIR_M3_TUPLE_ALIAS_PLAN.md) additionally covers fixed whole aliases
  of body-local constructor tuples; alias reseating, owning tuple rebinding
  and hoists remain open, including the source defects listed in that plan.
- [ ] **B1.** Extend cyclic copy/move/in-place operations where their existing emitted
  behavior is representable; preserve activation and retained-reference rules.
  The approved [M3.24/M3.25 batch](MIR_M3_CYCLIC_RECORD_STORAGE_PLAN.md)
  covers fresh region-local copy/move internally and constructor IN_PLACE
  replacement, then connects existing source copies/replacements. The
  [local binding consolidation](LOCAL_BINDING_LOWERING_PLAN.md) additionally
  connects source loop-local moves to the same region-initialization model.
  M3.24's internal activation/retention contract and M3.25's bounded source-copy
  integration are implemented.
  The implemented [M3.26/M3.27 batch](MIR_M3_REUSED_RECORD_STORAGE_PLAN.md)
  extends existing hoisted/reused record write modes to copy/move. M3.26
  implements the internal contract and admits source replacements with complete
  existing facts; M3.27 supplies the hoisted copy/move THIR facts and verifies
  source integration. Broader move sources, owning aggregates and excluded
  position/shape cells keep the box open.

Exit evidence: source binding availability, physical construction, payload
engagement, replacement and end events remain distinct on every admitted path.
Tests include skipped writes, repeated activation, surviving aliases and
normal early exits. Custom destructor effects also require W3.

### W2: additional ordinary control flow and bindings

The [approved ordinary for-loop batch](MIR_M3_ORDINARY_FOR_PLAN.md) groups
M3.19-M3.21 into one delivery: iteration identities/dependencies, range CFG,
and native scalar/record-element loops. M3.19 implements the internal
iterator/dependency model and positive THIR facts. M3.20 connects int32
unit-step range CFG, once-captured bounds and target residences. M3.21 connects
fixed borrowed list/Array scalar and plain-record elements, set[int32] and
dict[int32, int32] keys, with readonly and retained element aliases. This batch
does not include tuple-language fixes. Protocol iteration and the other W2
positions remain open after this batch.

- [ ] **B3.** `for` and iteration regions, including iterator/element dependencies,
  loop binding availability, `break`, `continue` and `else`.
- [ ] **B3.** Comprehension evaluation/binding regions and materialized temporaries.
- [ ] **B1.** `match` selection, guard evaluation and bound holders.
- [ ] **B5.** Other required binding positions, including module and closure execution,
  when their M2 identities and M4 effects are available.

Exit evidence: the CFG models emitted evaluation order and actual scopes for
the required positions, with no dependency lost at a join or implicit binding.
Richer constant propagation is an optional precision extension, not the next
critical-path package by default.

### W3: cleanup and exceptional flow

- [ ] **Cleanup.** `try`/`except`/`finally` and exceptional edges from supported operations.
- [ ] **Cleanup.** Context-manager entry/exit, including suppression and early transfers.
- [ ] **Cleanup.** Destruction/cleanup effects, explicit `del`/drop and partial initialization
  on failing paths.
- [ ] **Cleanup.** Error-return bodies and their cleanup/control-transfer conventions.

Exit evidence: each relevant normal and exceptional exit runs the correct
cleanup in order, with dependencies retained until the actual last use/end.
Call/destructor effects require M4 summaries; an ordinary scope-end inventory
alone does not satisfy this package.

### W4: suspension and frame lifetime

- [ ] **B4.** Generator and async frame-held bindings and backing placement.
- [ ] **B4.** `yield`/`await`, resume paths and dependencies retained across suspension.
- [ ] **B4.** Abandonment, close/cancellation and frame cleanup, composed with W3.

Exit evidence: a suspended frame retains every borrowed dependency it may use
after resumption or during cleanup, including the callable requirements for
frame-carried loans. Frame storage is not ordinary body-local storage.

### W5: complete propagation through required holders and escapes

- [ ] **B5.** Closures and their copied/moved/aliased environments.
- [ ] **B1-B3.** Required aggregate nesting, views, pointers and container-held references.
- [ ] **B2/B3.** Return, field-store and container-insert escape channels.
- [ ] **B5.** Call/forwarding boundaries and place-granular effects, using finalized
  M4 summaries and the remaining M2 place/operation facts.

Exit evidence: killing the original local never loses a dependency retained
by another holder; required admitted/rejected programs in the callable
requirements have explicit coverage and observable analysis results. TPy's
invalidation policy remains distinct from exclusive-borrow rules.

## Dependencies and delivery

Delivery order follows the B-steps; the dependencies below still hold
inside them. W3 and W5 need defined
M4 interfaces; W4 needs frame facts and W3 cleanup semantics. M2 representation
gaps remain prerequisites for their corresponding shapes, not work made
complete by starting M3.

The [M4.1/M4.2 call-summary batch](MIR_CALL_SUMMARY_INTERFACE_PLAN.md)
defines the interface needed before named argument storage can gain useful
source coverage. It keeps interprocedural orchestration in workspace analysis
and extracts local evidence from validated MIR. Both increments are implemented;
the [bounded named-storage consumer](MIR_NAMED_ARGUMENT_STORAGE_PLAN.md) now
uses that interface. Broader argument shapes and general M4 summary
obligations remain open.

The [call-effect batch](MIR_CALL_EFFECTS_PLAN.md) extends that interface
with typed scalar-field writes, alias-aware forwarding and ordinary void
setters. M4.3 leaf extraction and M4.4 call consumption/forwarding are
implemented for analysis only. Exception/cleanup, storage
invalidation and escaping dependencies remain separate M3/M4 obligations.

The [M4.5/M4.6 borrowed-return batch](MIR_BORROWED_RETURN_PLAN.md) adds
whole-parameter result origins and caller alias/forwarding propagation.
Returned holders participate in existing storage analyses; projected/aggregate
results and general escape channels remain W5 work. Production authority is
unchanged.

The [borrowed-argument storage batch](MIR_BORROWED_ARGUMENT_STORAGE_PLAN.md)
connects verified named constructor backing to returned holders, including
ordinary loop-body activations. The [storage-origin design](MIR_STORAGE_ORIGIN_DESIGN.md)
implements steps 1-2: per-body backing facts and borrowed-sink obligations,
internal lifetime evidence, and an adapter binding the exact THIR/plan/request
to backing places allocated by the ordinary MIR builder. It checks complete
origins and explicit returned borrows as well as scope, replacement and payload
ends. The [full-expression correspondence extension](MIR_FULL_EXPRESSION_EVIDENCE_PLAN.md)
connects already-modeled constructor temporaries to the same evidence through
their exact expression-region roots. The select-slot extension adds the
bounded plain-record ternary consumer described below; richer actuals and
general escape channels remain W1/W5/M4 obligations. Broader coverage measurement and
the step 3 production-authority decision remain open; this internal API changes
neither source acceptance nor diagnostics.

The implemented [select-slot batch](MIR_SELECT_STORAGE_PLAN.md) shares
placement with the bounded plain-record ternary consumer. It preserves named
slot scopes, including parent-scoped while-head backing, and leaves repeated
condition emplacement and production enforcement outside the first consumer.
Safe complete bodies can certify; the known plain branch-local alias escape
is an internal Conflict with no coverage gaps. Internal tuple/Optional/union
retention witnesses use the actual select root; source wrapper sinks remain
unplanned. Record and/or consumers and broader producer shapes remain open.

Batch work around shared invariants. Specify commit boundaries before coding,
review the cumulative batch, and run the full forced suite after its final
implementation changes. Each implementation step should finish as one commit
on a stacked branch; merging remains the user's decision. Small precision
extensions can join a relevant batch instead of becoming separate deliveries.

M4 owns finalized interprocedural summaries and per-instantiation obligations.
B6 owns compatibility/admission decisions and the checker-authority
transition. Completing M3 alone neither replaces the current provenance logic
nor authorizes new source diagnostics. All unchecked boxes above remain open.
