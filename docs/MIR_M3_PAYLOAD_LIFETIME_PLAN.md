# M3.5/M3.6: inline payload lifetime events and retained aliases

Status: design approved. M3.5 and M3.6 are implemented.
Continue on `mir-m3-reuse-batch`
after M3.3/M3.4; prepare one coherent commit per step, then review the
integrated branch for merge. This is architectural, analysis-only work.

## Concrete contract

Assuming `int32` is imported, this already supported program copies a scalar
before replacing its union payload:

```python
def snapshot(value: int32 | bool) -> int32:
    current = value
    saved = 0
    if isinstance(current, int32):
        saved = current
    current = True
    return saved
```

The relevant C++ shape is a local `tpy::Union<int32_t, bool>`, a narrowed
reference to `std::get<int32_t>(current)`, a copy into the scalar `saved`,
and finally assignment of `true` to the wrapper. The reference is consumed
before replacement; the scalar snapshot remains independent. M3.5 should
inventory the possible end of the old integer payload at replacement.
M3.6 should find no retained payload alias at that write.

A positive conflict requires an internal MIR witness: extract the integer
payload alias, replace the wrapper with the boolean alternative, then read
the old alias. Existing strict MIR validation already rejects that witness.
The new report must explain the conflict without certifying the body valid
or widening source admission.

**Invariant:** an inline scalar wrapper assignment may end its previous
payload only when the active alternative changes or an engaged Optional is
cleared; the analysis checks live incoming aliases to that payload while
preserving the existing, stricter alias-freshness validation policy.

## Evidence and precedent

Extend M3.3's positive write facts, M3.2's referent resolver and M3.4's
incoming-dependencies/post-write-liveness composition. Reuse `presence.py`'s
finite selection and alias-freshness solver; do not add a second freshness
or generation fixed point.

`runtime/cpp/include/tpy/union_type.hpp` inherits `std::variant` assignment.
`tpyc/thir/emit.py` emits ordinary wrapper assignment and narrowed scalar
references. For the admitted scalar alternatives, same-alternative variant
assignment preserves the contained object's lifetime; switching alternatives
ends the old one. Engaged-to-engaged Optional assignment preserves its
payload, while clearing it destroys the payload. These are distinct from
M3.4's logical record replacement events. See the C++ draft's
[variant assignment](https://eel.is/c++draft/variant.assign) and
[Optional assignment](https://eel.is/c++draft/optional.assign) contracts.

Record Optional clearing emits `nullptr`; record union reseating replaces
a pointer alternative. Neither operation ends the pointed-to record's
lifetime. Copied record holders retain their pointee, whereas copying a
scalar wrapper creates independent inline payload storage.

Native and CPython probes agree on scalar snapshots, scalar same-alternative
assignment, Optional copies/clears, and record Optional/union aliases. Record
probes mutate through another alias and observe the shared value. There are
no proposed source semantics, diagnostics, allocation or C++ emission changes.

## M3.5: positive payload-write facts and physical end inventory

Use one typed fact slot on `MIRAssign`: rename `record_write` to
`storage_write`, accepting the existing `MIRRecordWrite` or a new
`MIRPayloadWrite`. One instruction performs one write category; parallel
optional fields would introduce an unnecessary both-supplied state. Keep
all three record modes and their interpretation unchanged. Consumers select
the record or payload arm explicitly.

Payload facts distinguish `INITIALIZE` from `ASSIGN` for supported local
inline scalar Optional and nonrecursive union wrappers. Stamp these in the
existing THIR declaration/assignment lowering arms, using typed layout facts,
never emitted C++ strings. Pointer-record wrappers do not receive scalar
payload facts. Mixed wrappers with scalar and pointer alternatives remain
outside this first physical-end inventory.

Validate operation/layout/target compatibility, at most one static
`INITIALIZE` fact per local wrapper, initialization outside CFG cycles,
and definite initialization
of the destination before an assignment. Reuse definite assignment by making
fact-bearing assignment require the previous destination storage. Existing
branch-first/loop-first declaration restrictions stay in place. Earlier
analyses may still accept legacy MIR without these facts; the new inventory
returns `MIRNotCovered` if a relevant wrapper write lacks its positive fact,
including unreachable instructions and the initializing write. An untagged
initial write followed by a tagged assignment remains inventory-uncovered;
an unused, unwritten slot needs no invented initializer. Facts must never
default to initialization.

Expose a structured result from the existing presence solver: incoming
selection facts and separately identified selection/freshness failures.
Preserve strict validation's rejection behavior and first-error ordering.
Expose feasible incoming program points separately from sparse selection
facts: a missing wrapper fact at a reachable point means the complete layout
alternative domain, while an absent point has no feasible incoming state.
Inventory only feasible points from this solver, using facts before each
write after RHS evaluation. Liveness and dependencies may keep their existing
syntactic-edge reachability and conservatively overapproximate aliases.
Never interpret an unknown tag as an empty domain or an absent point as a
successfully analyzed unknown state.

| Write | Possible old scalar payload ends |
|---|---|
| Initialization | None |
| Known same alternative / engaged Optional assignment | None |
| Known different union alternative | Old selected scalar payload |
| Engaged Optional to absent | Old scalar payload |
| Already absent to absent | None |
| Copy from a wrapper with unknown alternative | Each feasible old payload whose alternative could change |
| Wrapper self-copy, including unknown alternative | None |
| Record pointer wrapper reseat/clear | No record pointee end |

Represent events using the existing wrapper-root plus payload projection
places, keyed by `MIRPoint` and bound to the exact immutable function instance.
This is a may-end inventory, not a claim that an end occurs on every path.
Self-copy must preserve source/destination correlation explicitly; independent
tag-set products would invent a change. Distinct correlated copies may remain
conservative. Absent alternatives contribute no scalar payload end.

## M3.6: inspection without weakening validation

Introduce an explicit internal inspection entry point. It prepares a body
only after structural, type, definite-assignment and payload-selection checks
succeed. Alias-freshness failures remain report data. Preparation must collect
selection failures even when an earlier stale-alias failure exists; stopping
at the first issue would create a validation bypass.

Keep `validate_function`, existing public analysis APIs and source lowering
strict. Do not add a `skip_validation` switch or catch-and-ignore
`MIRPresenceError`. Extract private payload-inventory, liveness and dependency
cores that consume the prepared artifact; ordinary public APIs continue to
validate before calling the same cores. The strict inventory API introduced
in M3.5 must not prevent M3.6 inspection from reaching its shared core.
Bind every composed result to that exact function instance.
Avoid validator -> public analysis -> validator recursion.

For each physical end event, intersect incoming referents with the ended
payload place and retain only aliases live after the write. Scalar payload
event places identify storage directly: match those identities against
incoming retained referents. Do not resolve them as pointer-holder leaves;
the existing resolver correctly finds no scalar holder dependency there.
Reuse that resolver for holder/pointee paths and the existing liveness rules.
Scalar snapshots are values, not loans;
scalar copies into tuples or Optionals must not become aliases by analogy
with record aggregates. This batch invents no scalar Optional borrow lane.

Report physical possible conflicts and strict freshness-policy failures
separately. Same-alternative assignment can have no physical end while still
invalidating an alias under the existing strict policy. A -> B -> A must not
revive an old alias: the existing freshness solver requires re-extraction,
even if the final static payload place equals the original one. Event-time
checking requires no unbounded storage generation identities.
Reports identify possible conflicts at static storage places; they do not
associate aliases with unique dynamic lifetime generations or claim
path-feasible causal traces.

Extend `--dump-mir` with the inventories for currently covered source bodies.
Stale-alias bodies rejected by source-to-MIR lowering remain uncovered there;
positive conflict witnesses exercise the internal inspection API. Reports
provide neither source diagnostics nor a safety boolean. Empty conflicts do
not authorize a move, borrow, new source acceptance or checker replacement.

## Factored scope matrix

A cell is covered only when its position, shape, slot and operation are all
covered below and already admitted by MIR. All other cells are explicit
follow-on gaps registered through this plan's TODO link.

| Axis | Covered | Deferred / not applicable |
|---|---|---|
| Position | Existing MIR ordinary function, method and constructor bodies; already covered branches/loops | Module execution, generators, async, comprehensions, closure execution, context managers, try/finally, error-return bodies and match lowering remain coverage gaps |
| Shape | Inline supported scalar Optional and scalar-only nonrecursive union; ordinary scalars as snapshot controls | Tuples are snapshot/borrow distinction controls, not new owned payload storage; strings/bytes, Own, mixed/owning unions, views, Ptr/Span, Box/Rc and containers require later storage contracts |
| Qualifiers | Already supported readonly scalar reads and borrowed record holder controls | No readonly write admission or qualifier relaxation |
| Slot | Initialized local wrapper writes; existing parameter reads and copied local wrappers | Parameter writes, wrapper fields, container elements, globals, escaping stores/returns and captures stay out of scope |
| Operation | Existing construct/copy/assignment, selection and scalar extraction; joins/loops under current declaration gates | General lexical ends, del, cleanup, exceptions, calls and resumable frame lifetime remain later work |

The same inspection logic applies to every admitted body kind. Tests must
exercise source producers for functions/methods and eligible constructor
bodies; a producer blocked by earlier THIR coverage must be recorded as such,
not presented as tested MIR coverage. No frontend extension is implicit.

Known boundaries: scalar union assignment inside an `isinstance` narrowed
branch can hit THIR `decl.narrowed_rebind`; the concrete example writes after
that branch. Parameter assignment remains excluded alongside
`BUGS.md#scalar-union-parameter-reassign-const`. Owned inline union reseating
requires a separate storage model and has
`BUGS.md#union-inline-slot-block-scoped-reseat`; do not route this batch through
that path or quietly fix its emission.

## Tests, docs and review gates

M3.5 pins source -> THIR -> MIR positive facts, malformed/missing facts,
initialization versus assignment, same/different/unknown alternatives,
self-copy, independent wrapper copies, Optional engagement/clearing,
unreachable writes and cyclic assignments. Exact event assertions are the
primary oracle. Preserve existing record-write and retention tests through
the fact-slot rename.

M3.6 pins last use before replacement, scalar snapshot surviving replacement,
re-extraction, retained stale aliases with exact payload/holder assertions,
same-tag freshness without physical end,
unknown-tag possible conflicts, A -> B -> A, joins/loops, incompatible prepared
results, and selection errors hidden behind an earlier freshness failure.
Strict validator tests must still reject every previously rejected alias use.
Use bounded concrete traces as an independent event/conflict oracle, with
freshness policy tested separately from physical lifetime.

Keep source/parity witnesses only where they pin the selected emission and
value/reference distinction; avoid duplicating unrelated corpus coverage.
If needed, add one condensed case with scalar snapshots and record alias
mutation controls, with descriptive subject-line comments. No invalid native
dereferences, panic cases or new source error cases are required: malformed
and stale-alias witnesses belong at the MIR boundary. Existing C++/diagnostic
snapshots should not change; obtain approval if any unexpected change is needed.

Update this plan, `MIR_ANALYSIS_PLAN.md`, `LANGUAGE_FEATURES.md` and `TODO.md`
as each step lands. No agent-facing language or runtime documentation changes
are needed. Run targeted MIR tests during implementation, full verification
after final edits, multi-agent defect review and readiness/retrospective gates.
Reconcile master before the final integrated gate. Keep M3.5 and M3.6 as two
commits after M3.3/M3.4, folding this proposal checkpoint into M3.5 when
preparing the batch. Merge only after both steps and the integrated gate.

## Pitfalls and risk assessment

- `silent-copy-vs-alias`: scalar snapshots and shared record mutation are separate controls.
- `copy-warning-at-wrong-site`: no new copy policy or warnings.
- `tuple-equals-scalar`: scalar tuple payload copies do not retain scalar aliases.
- `same-construct-every-position`: use the factored scope matrix and source producer checks.
- `conditional-operand-evaluates-in-place`: consume emitted CFG order; do not hoist writes.
- `generic-equals-monomorphic-twin`: generic bodies remain explicitly uncovered; existing collector tests pin this gate.
- `view-not-copy`: views remain outside this storage contract.
- `hidden-allocation`: analysis only; no runtime storage or allocation changes.
- `const-source-const-loop-var`: preserve qualifier and parameter-write gates.
- `generated-cpp-readability`: no C++ emission changes.
- `no-cpp-in-diagnostics`: no new source diagnostics.
- `no-internal-names-in-diagnostics`: MIR debug names remain debug output only.
- `reject-valid-python-only-as-documented-divergence`: no new source rejection; preserve explicit coverage misses.
- `no-warning-on-valid-code`: possible internal conflicts do not become warnings.

Confidence: High for the bounded emitted lifetime contract and its sibling
distinctions, supported by native/CPython probes and the existing analysis
patterns. The main implementation risk is preserving strict validation while
separating selection from alias freshness; differential validator tests and
inspection rejection tests are mandatory. The mechanical metadata rename
touches MIR consumers broadly but introduces no parallel emit path. Retain
finite per-point tag sets; do not enumerate paths or add growing generations.
Normal compilation has no new analysis cost. General lifetime safety and
replacement of provenance remain separate future design work.

## M3.5 implementation checkpoint

`MIRAssign.storage_write` carries record or scalar payload facts. Source
producers stamp the payload modes; validation enforces local inline layout,
single acyclic initialization and definite assignment before replacement.
`MIRPresence` exposes immutable feasible-point facts and classified issues,
and `MIRPayloadEnds` inventories possible ends at those points. `--dump-mir`
prints the inventory without changing strict selection/freshness rejection.

Tests cover scalar Optional and union writes, self-copy with unknown tags,
independent wrapper copies, selection, infeasible points, loops, missing facts
and malformed initialization. Source producer tests cover free functions,
methods, readonly source wrappers and eligible constructor bodies. Union
constructor parameters remain an existing coverage gap: the covered
constructor witness uses a scalar parameter and an annotated union local.
Record pointer wrappers have no pointee end; mixed scalar/reference wrapper
writes remain explicitly uncovered. No snippet snapshots or emitted C++ change.

## M3.6 implementation checkpoint

`inspect_payload_lifetimes` prepares structurally valid MIR and rejects all
payload-selection failures before running shared private analysis cores.
Freshness failures remain classified data in `MIRPayloadInspection` alongside
the end inventory and possible conflicts. The existing public validators and
analysis entry points still require strict validation. Composition checks
the exact immutable function instance; missing facts remain uncovered and
do not erase freshness issues.

Tests pin positive stale-alias conflicts, same-tag and self-copy freshness
without physical ends, snapshot and re-extraction controls, A -> B -> A,
loops, rejected selection/definite-assignment failures and mismatched inputs.
A bounded concrete generation trace independently checks physical end events
and requires all concrete conflicts to appear in the conservative report.
`--dump-mir` exposes the new report only for already covered source bodies;
it does not bypass lowering to print rejected stale-alias bodies.
