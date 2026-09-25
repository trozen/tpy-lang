# Select-slot placement and lifetime evidence

Status: both implementation steps are complete: shared placement and the
bounded MIR consumer. This extends the internal proof, not production
admission; the corpus measurement is recorded in
[the coverage audit](MIR_STORAGE_COVERAGE_AUDIT.md#select-slot-correspondence-follow-up).

## Observable contract

For a plain scalar-field record, this already compiles:

```python
def choose(flag: bool, owner: Cell) -> int32:
    saved = owner if flag else Cell(2)
    alias = saved
    owner.value = 9
    return alias.value
```

The result is 9 on the borrowed arm and 2 on the fresh arm. Existing C++ is:

```cpp
std::optional<Cell> __select_slot_1;
Cell& saved = flag ? owner : __select_slot_1.emplace(Cell(2));
Cell& alias = saved;
owner.value = 9;
return alias.value;
```

Only the selected constructor runs. The empty optional is physical backing,
not a source `Cell | None`. The holder aliases either the existing object or
the optional's payload; copying the holder does not copy the object.

This batch preserves that C++ and connects its actual storage to MIR's
existing proof. A fully covered safe body can certify. The following plain
record alias escaping a branch-local slot produces an internal Conflict:

```python
outer = owner
if flag:
    saved = owner if choice else Cell(2)
    outer = saved
return outer.value
```

Reserve Not covered for the explicitly excluded bodies or holder shapes;
an accidental coverage gap must not satisfy this primary regression check.

The regression requires Conflict with no coverage gaps, tied to the actual
slot root at scope end. Returning an alias that may retain a select root
also conflicts; returning a caller-owned origin can certify.

The existing source defect remains
`BUGS.md#select-slot-alias-escape-unchecked`. The internal API does not change
source acceptance, issue a diagnostic, extend storage lifetime or fix that
production defect. Enforcement remains the separate compatibility decision
in [the storage-origin design](MIR_STORAGE_ORIGIN_DESIGN.md).

## Placement evidence from current emission

`THIRSlotEmplace` serves both mixed ternary arms and fresh `and`/`or` operands.
Selection itself remains `THIRIfExpr` or `THIRValueSelect`; no new select IR
node is needed. The emitter declares a named optional before the statement,
then emplaces at the selected operand. Its named-declaration channel differs
from the anonymous argument-temporary queue.

| Position | Actual select backing lifetime |
|---|---|
| Ordinary statement / first if condition | Enclosing emitted block |
| Elif condition with named storage | Generated nested else block |
| Loop body | Body block, freshly activated each iteration |
| While condition | Enclosing block, including repeated emplacement |
| While condition also containing argument temporaries | Select still outside the loop; argument backing inside the rewritten iteration |

Compile-only probes on the merged tree confirmed the last three rows. A mixed
head renders the following shape; moving the optional inside would change
the existing lifetime and is outside this batch:

```cpp
std::optional<Cell> __select_slot_1;
while (true) {
    Cell __tmp_2 = Cell(0);
    if (!(/* condition using select slot and argument temporary */)) break;
    // body
}
```

The source probes use a method on the selected receiver in conditions. Direct
field reads and free-call selected arguments encountered existing frontend
coverage boundaries. Planner/IR condition tests must therefore be explicit
internal witnesses when no admitted source reaches the corresponding MIR
shape; this work does not add those frontend routes or method summaries.

## Invariant and existing mechanism

Every demanded select producer maps to its actual optional backing root,
with the emitted declaration scope, conditional construction point and
replacement behavior; alias analysis must preserve those origins through
every supported holder and use.

The implementation extends `THIRTempPlan` and `THIRTempPlacement` in
`thir/temp_plan.py`, the planned `TempSink` checks in `thir/emit.py`, and the
existing planned-storage and borrowed-expression paths in `mir/lower.py`.
There is one placement plan and one lifetime checker. Roots are captured at
allocation, never inferred from names, C++ text or slot numbers.

The two implemented steps follow the approved batch:

1. **Shared select placement.** Placement identities cover the two
   existing producer kinds, with separate anonymous and named scheduling
   channels, including two declaration scopes within one while statement.
   A select's initialization anchor is the producer itself, not the enclosing
   expression entry. Emitter verification preserves declaration order,
   temporary numbering, flush timing and lazy evaluation. Unrelated named
   declarations and unsupported bodies remain outside the plan. The existing
   storage-fact inventory publishes and validates select placement.
2. **Bounded MIR consumer and evidence.** Mixed plain-record ternaries lower
   through the existing borrowed-expression CFG, using verified hook-free
   bool/int32-field constructors and stable scalar constructor operands.
   Optional backing is allocated at the planned scope and initialized empty
   with `MIRRecordStorageInit`; the selected operand fills it through the
   existing `OPTIONAL_ASSIGN` contract and borrows the actual root. Emplacement
   requires the constructor/layout's move eligibility. Readonly holder access
   remains independent of mutable backing. The existing adapter connects the
   exact node, retaining inventory, request identity, engagement, scope-end,
   replacement and escape checks.

Declaration lookup permits multiple scopes for one statement. Exact producer
identity and per-channel ordering checks remain necessary: the emitter can drain
anonymous declarations to an iteration buffer before flushing named ones to
the parent block, so one global callback order is not the physical output
order. Do not weaken validation to counts alone.

The first consumer covers a slot initialized at most once per declaration
activation, including fresh loop-body activations. Repeated while-head
emplacement is a placement witness and explicit MIR coverage boundary in
this batch. A later consumer must demonstrate old-alias invalidation before
covering it; this plan does not change the solver or treat the reused root
as a fresh object automatically.

## Scope matrix

Axes are factored: a combination is included only when all applicable axes
are included and the complete body is otherwise representable.

| Axis | Included | Boundary / existing followup |
|---|---|---|
| Selection | Mixed plain-record ternary, either arm orientation, nested supported ternaries | Record and/or truthiness remains uncovered by MIR; the shared placement producer still covers it where the planner can represent the body |
| Producer | Unique verified hook-free movable record constructor, stable bool/int32 operands | Owning-return calls, hooks, containers, native/value records, richer fields: M3 W1 / M4 |
| Other arm | Existing supported borrowed-record expressions, mutable or readonly access | Broader projections and call contracts: M2 / M4 |
| Callable | Free function, method, initialized constructor tail | Member/base initializers retain their existing slot restriction |
| Control flow | Straight-line, if/elif scopes, ordinary loop body and else; break/continue/return | Repeated condition emplacement is not admitted by this MIR consumer; unsupported loop heads remain uncovered |
| Special bodies | Inventory remains visible | Module/global, comprehension, match: W2; cleanup/with/try/finally/error-return: W3; generator/async: W4; closure: W5 |
| Sinks | Supported local declarations/reseats and eligible borrowed returns; parameter-derived aliases | Field/container/global stores and general escapes: W5; no new source sink admission |
| Holder shapes | Plain record and readonly aliases; internal MIR retention witnesses connect singleton/mixed tuples, Optional and union holders to the actual select root | Source wrapper sinks remain unplanned; direct aggregate selects/materialization and broader holders remain W1/W5 |
| Other types | Existing scalar selections unchanged; both-borrow forms need no backing | Own/direct-init uses its existing prvalue route; str/bytes, Ptr/Span, Box/Rc and generic records gain no coverage |

These exclusions remain the tracked W1-W5/M4 work in
`MIR_M3_COMPLETION_PLAN.md` and the storage-origin backlog in `TODO.md`.
This is not tuple completeness or a new language restriction.

## Validation, pitfalls and delivery

Use compiler-local THIR/MIR fixtures, never compiler tests reading
`tests/cases`. Existing `control_flow/ternary_mixed_category_alias` and related
select cases pin emitted C++, selected-arm effects and mutation-visible
aliasing. Add a source section only if an observable contract is not already
pinned. Expected existing snapshot churn: zero.

- Placement: both orientations, nested slots, named plus argument storage,
  declaration ordering, if/elif, body activations and both while-head shapes.
  Compare actual emission and plan, including bodies remaining unplanned.
- Evidence: safe aliases and alias chains, mutation visibility, readonly
  access, holder reseats, early return and dead versus retained aliases.
- Negative evidence: known block escape, repeated-emplacement boundary,
  retained scalar/singleton-tuple/mixed-tuple/Optional/union aliases, missing
  or pruned roots, stale plans/facts, unsupported effects and incomplete bodies.
  Connect retained holders to the actual select root; generic solver tests
  alone do not establish the new correspondence. Never execute dangling C++.
- Keep exact-demand certificates: a dropped backing or operation, empty
  inventory or reused request/body must not acquire a certificate.
- Re-run the audit with the same population and report actual transitions;
  27 select-backed bodies in the earlier sample is not a promise of 27 new
  certificates. Other body and summary blockers still apply.

Pitfalls: preserve aliasing and warn/copy behavior by unchanged emission;
check tuple/scalar retained-origin symmetry internally; share the producer
across positions while keeping unsupported routes explicit; preserve lazy
evaluation and source order restrictions; require positive type/constructor,
placement and engagement facts. No allocations, view conversions, runtime
templates, numeric rules, loop constness, diagnostics or source rejections
change. Generic twins remain coverage boundaries rather than inferred facts.
Generated-code checks include slot naming, order and scopes, not only output.

Compiler-local evidence tests require the known plain escape and each internal
retained-holder shape to conflict at the actual select root. Source wrapper
sinks retain their backing obligation and remain Not covered. Missing plans,
stale facts, dropped roots or operations, unreachable producers and pruned
roots cannot acquire a certificate. These witnesses do not add aggregate
source coverage or change the solver.

CPython parity: the safe example matches shared mutation and conditional
construction. Existing select escape and cleanup-timing defects are not fixed
or endorsed (`BUGS.md#select-slot-alias-escape-unchecked`,
`BUGS.md#select-slot-extends-fresh-lifetime`). No new divergence is proposed.

Confidence: high for the bounded first consumer and reuse of existing
optional-record evidence; broader repeated-head coverage is deliberately not
claimed. Review the cumulative two-commit batch, run targeted checks and one
full forced suite after implementation fixes, then the readiness gate. Keep
the work stacked on the current branch; merge remains the user's decision.
