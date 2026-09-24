# Explicit proof for borrowed operations

Status: implemented, reviewed and verified internally. This extends the internal
[storage-origin proof](MIR_STORAGE_ORIGIN_DESIGN.md) after its
[coverage audit](MIR_STORAGE_COVERAGE_AUDIT.md). Work stays on
`mir-storage-origin-proof`; source admission and production enforcement remain
outside this increment.

## Contract and examples

For a plain record `Cell` and a known free function
`observe(owner: Cell) -> readonly[Cell]` that returns its parameter:

```python
def forward(owner: Cell) -> readonly[Cell]:
    saved = observe(owner)
    alias = saved
    return alias
```

This already compiles. The existing C++ remains:

```cpp
const Cell& forward(const Cell& owner) {
    const Cell& saved = observe(owner);
    const Cell& alias = saved;
    return alias;
}
```

The new behavior is internal: the declaration, alias declaration and return
each have explicit obligations tied to their actual MIR operations. A
whole-body proof can certify them even though this body creates no argument
backing. Mutation of `owner` after the call remains visible through the result.

Reseating one holder must preserve the other holder's origin:

```python
def first_alias(first: Cell, second: Cell) -> readonly[Cell]:
    saved = observe(first)
    alias = saved
    saved = observe(second)
    return alias
```

C++ still reseats `const Cell* saved`; `alias` remains a `const Cell&` to
`first`. A local owner is different from a parameter origin:

```python
def local_read(value: int32) -> int32:
    local = Cell(value)
    alias = local
    local.value = 7
    return alias.value
```

This must retain the emitted `Cell& alias = local` and can certify its local
use. Returning that alias or retaining it after the owner's storage ends must
not certify. Unsafe witnesses that source lowering already rejects stay at
the internal IR test boundary; the feature admits no new source forms.

## Invariant and precedent

Every recorded supported borrow operation maps to its exact emitted MIR
operation and receives positive evidence only when origin completeness and
whole-body lifetime/escape checks succeed for that exact generation request.

Reuse `THIRStorageFacts`, the builder correspondence in `MIRLoweredStorage`,
`MIRDependencies.resolve_referents`, and the existing scope, payload,
replacement, freshness, call-effect and return-escape analyses. There is no
second lowering walk or new dataflow solver.

This is architectural because it adds an operation-level proof contract
across THIR and MIR. Parser, sema, type rules, emission, runtime and stdlib
behavior do not change.

## Inventory and correspondence

Extend the current obligation inventory for supported plain-record bindings:

- Record declarations and reseats even when the value is a name, field or
  coercion. Stable-looking syntax is not evidence of storage lifetime.
- Include the existing `THIRAssign` alias/storage-borrow forms alongside
  `THIRVarDecl`, `THIRPtrLocalDecl` and `THIRPtrLocalRebind`.
- Record explicit borrowed returns using the function's selected
  `resolved_callee.signature.borrowed_result` contract. Do not infer return
  ownership from an expression's form or type spelling alone.
- Use existing typed alias/storage facts and MIR binding eligibility.
  Existing recorded unsupported sinks remain explicit gaps; narrowing this
  increment to plain records must not silently discard their obligations.

Pass the selected return contract into collection and validation, alongside
the exact body and plan. Fact validation still recomputes the inventory and
checks identity, including that return contract.

Extend `MIRLoweredStorage` with an immutable identity-keyed map from a THIR
statement to its emitted operation points. Capture the final holder write
or explicit return terminator in the existing builder arms; intermediate
call/conditional expression results are not the source sink. Use a tuple of
points per statement so repeated occurrences cannot overwrite one another.
The number of mapped points must match the number of inventoried occurrences
of each statement identity; an earlier point cannot discharge a later
occurrence that lowering skipped after a return.

Current CFG pruning preserves block IDs and statement indices. A missing or
pruned operation must produce an explicit uncovered result, not silently
remove its obligation. Proving unreachable obligations discharged is a later
precision extension. Constructor wrappers retain the original body statement
identities; the outer certificate stays bound to the original constructor.

## Shared proof, separate request contracts

Keep the existing storage-only API and its nonempty required-root rule.
Add a typed operation-proof entry point/result for a nonempty set of borrow
operations, sharing the same internal lifetime computation. An operation-only
result must not be usable as a storage-only certificate through an empty
root-subset query.

The operation result retains immutable demanded points and explicit backing
roots. Its query checks those exact demands and the same MIR function; it has
no roots-only certification query. Origin witnesses and the combined reached
local roots describe the proof but cannot replace its original demands.

For each requested operation:

1. Validate that it identifies a supported plain borrowed-record holder write
   or a return with the selected borrowed-result contract.
2. Resolve a write's destination in the dependency state immediately after
   the write; resolve a return's value at its terminator. Use full referent
   states, not live-only states: a dead destination still has an obligation.
3. Require complete nonempty origins. Retain parameter-derived origins as
   external to this body; do not equate external with globally durable or
   mutually disjoint storage.
4. Add reached internal storage roots to the explicit backing roots. This
   includes ordinary local record storage, even when the temporary-backing
   inventory contains nothing for that owner.
5. Run the existing whole-body lifetime and escape analyses once. Share the
   preparation and analysis results across all requested operations.

The adapter chooses operation proof when obligations exist, storage-only
proof for backing-only requests, and no certificate for an empty inventory.
Only an overall Certified result discharges all requested obligations. Keep
conflicts at their actual MIR use/end/return sites; do not label every
operation conflicting when another operation caused the conflict. Missing
correspondence or analysis withholds certification conservatively.

Temporary-plan presence is required for recorded argument backing that needs
it, not as a blanket prerequisite for parameter-only operations. Ordinary
local storage retains its existing MIR placement/duration checks. Select-slot
and full-expression backing remain explicit gaps; removing the blanket plan
check must not clear those gaps.

Retain exact source, request, facts, plan, definitions, summary snapshot and
MIR identity checks. A cloned body, missing origin on a feasible path, changed
plan, empty operation demand or dropped obligation cannot reuse a proof.

## Scope matrix

Coverage is the intersection of these axes, subject to existing whole-body
MIR coverage. No cell grants broader source admission.

| Axis | Included | Explicit gap / reason |
|---|---|---|
| Positions | Ordinary free functions, methods and fully initialized constructor tails for local binds/reseats | Borrowed returns only for eligible free-function contracts; methods/static methods lack that contract and constructors have void returns |
| Control flow | Existing if/while/ordinary-for CFG, joins and repeated holder writes | Pruned/unmapped sinks remain uncovered; no new CFG or expression coverage |
| Plain record / readonly record | Supported record aliases, call results and reseats; readonly access preserved | Native/value/protocol/inherited/generic record eligibility is unchanged |
| Scalar | Existing scalar operations surrounding the borrow | Scalar assignments/returns are not borrow obligations |
| Tuple / Optional / union | Existing represented payloads may supply a plain-record extraction origin and remain in whole-body analysis | Aggregate sink/return contracts, nested payload generalization and new extraction forms are deferred |
| Own | Existing owned record/tuple storage may be the origin of a plain borrowed alias | New owned call/return contracts are deferred |
| str / bytes / Ptr / Span / Box / Rc | No new proof coverage | Their borrow/view/ownership contracts need separate work; lowered `PTR_ADDR` is not source `Ptr[T]` coverage |
| Slots | Local declarations/reseats, eligible plain borrowed returns; parameters as origins | Field/container/global/capture stores need escape contracts; parameter reseats remain outside existing MIR coverage |
| Module scope | No new coverage | Existing `__tpy_init`/global design backlog (W2/W5) |
| Closures / comprehensions / match | No new coverage | Existing body/capture coverage backlog (W2/W5) |
| Generators / async | No new coverage | Frame and resumption analysis (W4) |
| try/finally / with / error-return | No new coverage | Exceptional flow and cleanup analysis (W3) |
| Generics / overloads | No new coverage | Existing callable identity/specialization coverage backlog |

These gaps remain in the M3/M4/W5 backlog in `TODO.md`; this increment does
not implement select-slot lifetime extension or fix the separately tracked
select-slot escape.

## Validation and delivery

Delivered as one additional implementation commit on the existing branch.
Code changes are bounded to THIR storage facts/publication, MIR lowering
correspondence, the shared evidence implementation and its adapter, plus
their unit tests. This plan, the storage design, audit follow-up,
`LANGUAGE_FEATURES.md`, `ARCHITECTURE.md` and `TODO.md` describe the internal
contract.

Tests must cover every recorded operation, not merely a body verdict:

- Parameter-only direct/forwarded aliases and returns; selected parameter
  origins; readonly and mutation-visible aliases after holder reseats.
- Local-owned and named-argument origins, alias chains, scope ends, loop
  activations and returned local origins, including unsafe hand-built IR.
- Ordinary method and constructor local sinks using the shared paths;
  existing payload-derived record aliases and explicit excluded siblings.
- Dead destinations, unsupported/pruned operations, missing origins at a
  feasible operation, incomplete joins, unknown callees and stale identity.
- Empty operation demands fail validation; storage-only empty roots still
  fail; bodies with neither obligations nor backing requirements receive no
  certificate. Changed point sets, changed explicit backing demands and
  roots-only queries cannot reuse an operation certificate.
- Missing temporary plans with actual argument backing remain uncovered,
  while a parameter-only body's absent temporary plan does not itself block
  otherwise complete evidence.

Reuse the mutation-sensitive `calls/borrowed_return_effects` and
`calls/borrowed_argument_storage` cases for emitted behavior. New assertions
belong in internal evidence tests; add a source section only if an observable
boundary is genuinely absent. Expected existing snapshot churn: zero C++,
runtime output and diagnostic changes. Run targeted internal checks, then
the full forced suite after implementation and review fixes.

Pitfalls: aliasing/tuple symmetry/read-only access are preserved by consuming
existing binding and payload facts; owning operations are not relabelled as
borrows. No copy, view conversion, allocation, evaluation-order, runtime
template or numeric behavior changes. Position/generic gaps stay explicit.
Unknown origins cannot certify. No new source rejection or warning is added,
and internal reasons do not become production diagnostics.

Confidence: high for this bounded internal contract. Compile-only probes
confirm parameter-forwarding, reseats retaining an earlier alias, and
mutation-visible local aliases already emit the reference shapes above and
lower to MIR; the missing part is inventory/correspondence/evidence. The
main implementation risks are operation-point completeness, confusing owned
storage with external origins, and accidentally bypassing existing coverage
or exact-identity checks. No generated-code change is intended.

Verification on 2026-09-24: the full forced suite passed with 11,016 tests
passed and 23 skipped; 4,241 C++ cases built and ran without execution-cache
skips. Targeted post-merge checks passed (1,882 tests), as did the final proof
regressions (107 tests). Specialist review and the readiness retrospective
are complete. No source, diagnostic or generated-code snapshots changed in
this increment; this result does not establish production proof coverage.
