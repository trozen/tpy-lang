# M3/M4 call-summary interface: first consumer

Status: M4.1 and M4.2 implemented. M4.3/M4.4 add typed writes, void calls
and alias-aware forwarding beyond the reader-only interface described here; see
[call effects](MIR_CALL_EFFECTS_PLAN.md). See also the
[named argument temporary investigation](MIR_NAMED_ARGUMENT_TEMPORARIES.md).
This is architectural analysis work, not a language-rule change. The
bounded interface does not complete M3 or M4.

## Contract and useful first delivery

Given the existing hook-free scalar-field `Cell`:

```python
def read(cell: Cell) -> int32:
    return cell.value

def forward(cell: Cell) -> int32:
    return read(cell)
```

M4.2 makes the forwarding caller analyzable without changing its C++ reference parameter or
`return read(cell);`. Imported aliases and forward declarations use the same
semantic identity. `read(Cell(value))` is the next storage integration, not
silently part of this first call batch.

Invariant: a MIR call consumes an immutable, finalized summary for the exact
selected declaration and signature; every required fact must be proven, and
missing information never means empty effects or dependencies.

## Precedent and actual gaps

- `THIRResolvedCallee` already records qualified declaration identity and an
  exact signature. The producer in `thir/lower/callables.py` checks unique,
  ordinary definitions. Reuse it; `MIRBodyId` contains debug locations and is
  not the interprocedural key.
- `FunctionInfo` has parameter/structural/element mutation facts and
  `return_borrows_from`. Those are useful existing authorities within their
  domains, but do not prove general retention, global/environment effects,
  cleanup or exits. `addr_escapes_params` is a narrow ABI fact, not a retention
  summary. A `None`-to-empty helper cannot certify completeness.
- The mutation graph is deliberately filtered by parameter flow. It omits
  calls without that flow and some readonly/pure calls, then is cleared after
  propagation. It cannot schedule general summary dependencies.
- `--dump-mir` already collects emitted user-module THIR before analyzing
  individual bodies. Its constructor-definition table is the precedent for
  verified definition lookup. MIR already has operand, dependency, liveness,
  storage and region analyses; extend those instead of a parallel checker.

## Phase ownership

Use validated MIR to extract **local** summary evidence. A dedicated operation
transfer inventory determines what a body reads, writes, retains and can do at
exit. This is not an AST purity scan, and MIR coverage alone is not evidence of
harmlessness: covered loops, iterators and storage operations can require more
reasoning than the first extractor supports.

The compiler's workspace analysis owns dependency scheduling, composition and
publication. MIR lowering receives finalized callee entries and never
recursively asks another body to resolve itself. This preserves the
[callable requirements](CALLABLE_PROVENANCE_REQUIREMENTS.md)' separation of
intraprocedural analysis from the interprocedural fixed point.

Placement is explicit: run this analysis over the
captured THIR workspace used by the MIR debug adapter, **after THIR emission**.
The existing `Compiler._finalize_workspace` runs before those bodies exist, so
it is not the insertion point. Do not move THIR production or feed these facts
back into sema/codegen. Existing mutation propagation and borrow checkers remain
authoritative. This does not fix their separate cross-module propagation gaps.

## Summary contract

Use three explicit states:

- **Pending:** workspace scheduling has not resolved the definition. Not a
  usable proof and never an accepted call operand.
- **Opaque(reason):** missing/unsupported definition, incompatible identity or
  signature, recursion, unsupported effects or incomplete evidence. This first
  consumer returns MIR not covered; it adds no source diagnostic.
- **Known(summary):** all dimensions required by the consumer are final.
  Empty effects are explicit fields of Known, never an absent summary.

The interface distinguishes these semantic dimensions:

| Dimension | Meaning | First accepted value |
| --- | --- | --- |
| Identity and signature | Exact selected definition in this compilation | Matching unique ordinary function |
| Reads | Places whose contents the call may observe | Scalar arguments and borrowed scalar-field record arguments |
| Writes and invalidation | Content writes versus replacement/structural invalidation | No externally visible writes or invalidation |
| Result | Value form and returned dependencies, independent of numeric value | bool/int32 VALUE, no returned references |
| Retention | Arguments/environment places retained beyond return | None |
| Exits | Normal return, exceptional exit, nonreturn, and cleanup obligations | Proven normal completion for valid inputs, no exceptional/nonreturn/cleanup effects |

Do not conflate readonly parameters, scalar returns, or `@pure` with this full
proof. Results have unknown scalar values; a summary is not an evaluator or
an implementation to inline.

The general root model is parameter, environment and qualified global, with
semantic projections. Parameters remap to evaluated argument places;
environment roots remap to captured places; globals are remap-invariant.
Callee-local roots must never disappear during remapping. Fresh returned
storage requires a caller-site identity. These are extension constraints, not
permission to admit those richer summaries in the first batch. Do not build
unused generic/form/escape machinery merely to populate empty placeholders.
For the first reader-only slice, conservatively reading every borrowed argument
is sufficient; exact per-field read minimization is unnecessary.

The table is immutable once published and belongs to one captured workspace.
Do not cache it across compilations or retain mutable FunctionInfo references.
Missing bodies, duplicate identities and signature mismatches become Opaque.
No error-recovery path may install a Known-empty fallback.

## Bounded producer and orchestration

First summarize unique synchronous nongeneric free functions with bool/int32
results, bool/int32 or borrowed hook-free bool/int32-field record parameters,
and acyclic validated CFGs. Audit constants, scalar reads/local assignments,
bool/int32 comparisons/not, record scalar-field reads, branches and returns.
Require initialized inputs/uses and preserve mutable versus readonly access.
Local aliases, if admitted, resolve through existing dependency facts.

Initially exclude globals, captures, reference-retaining stores, external
writes, owning local construction/replacement, iterators, loop backedges,
checked operations, panic/unwind and custom cleanup from Known summaries.
Each new operation needs an explicit effect/exit rule; a default-empty rule
is forbidden. This proves normal completion for the acyclic slice instead of
mistaking a graph with only return terminators for a termination proof.

Workspace processing is bottom-up:

1. Index actual emitted definitions and inventory all call dependencies from
   THIR, including calls with no arguments or no parameter flow. Missing
   identities and bodies are explicit unavailable dependencies.
2. Extract call-free leaf summaries, then lower acyclic callers using only
   finalized compatible summaries and extract their local evidence.
3. Publish Known or Opaque once; recursive strongly connected components are
   Opaque initially, as are callers requiring those opaque dependencies.

Use graph scheduling rather than repeatedly rescanning all pending bodies.
Each definition is analyzed once when dependencies settle. Bound summary
representation growth; widening must become Opaque, never truncated Known.
No production recursion warning or other M5 diagnostic is introduced here.

Definition coverage and caller coverage are separate: a body can have valid
MIR yet lack a usable callee summary (for example, it contains a loop). A call
to a summarized leaf can occur inside already-covered caller CFG, including
loops, without promising that the enclosing caller itself is summarizable.

## MIR consumer

Add a real `MIRCall` operation. A scalar-result rvalue is sufficient for this
normal-return-only slice. Store the resolved semantic callee and arguments,
validated against the finalized table. Do not encode a call as a constant,
ordinary read, or copied callee body.

Initially accept stable scalar/record names and supported scalar literals as
arguments, with exact arity and compatible forms. Unsupported adaptation,
defaults requiring evaluation, effectful argument expressions and named temps
remain uncovered. No claim about general Python/C++ argument evaluation order
follows from this side-effect-free subset. Future argument lowering must model
the actual producer's sequencing and lazy placement, including kwargs.

The subsequent [named-storage extension](MIR_NAMED_ARGUMENT_STORAGE_PLAN.md)
implements that placement contract for bounded pure record constructors.
Their callers have covered MIR, and the argument backing they keep is
private storage their own summaries leave out (`summaries._private_records`).

At the call, evaluate scalar inputs and borrow holders, keeping their owners
live through the operation. A harmless callee does not make an argument use
optional: lifetime, engagement and availability checks still apply. The result
defines an unknown scalar, clearing prior facts in every dataflow transfer,
not only during branch lowering. Both branch
successors remain possible unless an independent existing proof establishes
otherwise. There are no returned or retained loans in this subset.

Update validation, operand uses/definitions, liveness, dependency transfer,
retention, storage/region inventories and dumping together. A hand-built call
must not bypass summary validation. Each MIR body retains the immutable
summary entries its calls reference, so standalone analyses and dumps validate
the same contract as lowering; they must not require a live sema registry or
invent a default when an entry is missing. Local validators check the published
entry's structure and signature, not re-prove another body's semantics. The
entry must come from that body's captured workspace and exact selected
definition; a stale or foreign entry cannot substitute for missing evidence.
Later throwing/nonreturning calls require
real CFG exit semantics; never invent a successful return edge for them.

## Delivery, tests and exclusions

**M4.1: immutable contract and local extractor.** Identity/definition validation,
states, complete narrow operation semantics and summary unit tests. No call
admission yet. Include unsupported and malformed boundary tests.

**M4.2: workspace scheduling and first call consumer.** Forward/imported and
aliased calls, unconditional dependency inventory, recursive/missing/opaque
cases, real MIRCall and all affected analyses, debug integration and docs.

The separate [named-storage extension](MIR_NAMED_ARGUMENT_STORAGE_PLAN.md)
uses this interface for eager/lazy backing with actual emitted block lifetime.
Broader materialization and tuple completeness remain separate.

| Axis | First batch | Explicit remaining work |
| --- | --- | --- |
| Caller position | Existing covered ordinary free/method/constructor bodies and supported CFG | Module, closure, match and comprehension execution: W2/W5; exception/context-manager/error-return: W3; generator/async: W4 |
| Callee position | Exact ordinary free definitions, forward/imported aliases | Method/constructor/static method identities, callbacks, native/protocol dispatch and generic instantiation: M2/M4 |
| Shape | bool/int32 values and existing borrowed plain-record arguments; retain readonly | Tuple/Optional/union arguments/results, str/bytes, Own, Ptr/Span, Box/Rc, containers and nested aggregates: M2/M4/W5 |
| Slot/boundary | Argument use, scalar result to already-covered local/return/condition positions | Named temp backing: W1; reference returns, fields, container elements, globals and captures: W5/M4 |

Tests must distinguish Pending/Opaque/Known-empty, exact versus mismatched
signatures, forward order, cross-module aliases and cycles, zero-argument
dependencies, unknown native/callback calls, invalid/unavailable record holders,
call-point liveness, fresh scalar results and both boolean branches. Include
callee mutation, global access, escape and exception/loop exclusions so absence
of one old FunctionInfo flag cannot accidentally grant Known status.

Use embedded source unit tests through production THIR collection, never
compiler tests reading snippet files. Source tests should show two calls
separated by a mutation observing different values. Since that alone cannot
detect a copy during a readonly call, also require a `@nocopy` witness or a
generated-C++ reference-form assertion. Reuse focused existing cases where
adequate; add at most one condensed case for missing observable coverage.

Pitfalls: reference boundaries must preserve aliasing; tuple/generic siblings
stay explicitly uncovered, not silently coerced into scalar cases. Lazy calls
must occur only on their CFG branch. Scalar result tests must not require a
constant-return summary. No view copies, allocations, runtime templates,
iteration constness, C++ formatting or source diagnostics change. Preserve
existing snapshots and test excluded siblings' source behavior. Update
`LANGUAGE_FEATURES.md` and architecture docs when implementing. Review the
cumulative batch and run the full forced suite after final code changes.

Every affected MIR transfer must account for calls and their exact source
argument forms. This bounded interface does not supply general call summaries.

Implementation: `mir/call_contract.py` holds the immutable bounded contract;
`mir/summaries.py` extracts local evidence; `mir_workspace.py` schedules the
captured definitions; `mir/collect.py` reuses the analyzed bodies for the CLI
dump. The first read set conservatively includes all parameters. MIR bodies
retain the published summary objects, and validation requires call operations
to reference those same objects. Their scalar results carry no borrowed roots.
The bounded summary size depends on the signature, not forwarding depth.

Embedded source tests pin direct/forward/imported calls, ordinary method and
constructor-tail callers, loop callers and recursive/opaque dependencies.
Named-storage coverage has its own tests in the linked follow-up. Internal
checks pin missing/foreign/effectful summaries,
argument liveness, expired backing retention and unknown boolean results.
The `@nocopy` source witness and generated reference-parameter assertion pin
the call's no-copy boundary. Existing snippet snapshots are unchanged; no new
snippet case is needed for this analysis-only extension.
