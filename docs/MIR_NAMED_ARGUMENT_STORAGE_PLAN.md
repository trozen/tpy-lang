# Named argument storage: shared placement and MIR consumption

Status: implemented bounded named-argument storage coverage.
Follows the [dependency investigation](MIR_NAMED_ARGUMENT_TEMPORARIES.md)
and the implemented [call interface](MIR_CALL_SUMMARY_INTERFACE_PLAN.md).
This covers part of W1, not all of M3. Tuple completeness remains separate.

## Contract

Given a hook-free `Cell` with one `int32` field and a pure constructor:

```python
def read(cell: Cell) -> int32:
    return cell.value

def eager(value: int32) -> int32:
    return read(Cell(value))

def lazy(value: int32, flag: bool) -> int32:
    return read(Cell(value)) if flag else 0
```

These programs already compile. The extension makes their named argument
backing visible to MIR. Existing C++ remains, schematically:

```cpp
Cell temp = Cell(value);
return read(temp);
```

```cpp
std::optional<Cell> temp;
return flag ? (temp.emplace(Cell(value)), read(*temp)) : 0;
```

The backing lives until its actual C++ block ends. The optional wrapper
exists even when the lazy arm is skipped; its payload does not. Neither
backing has the full-expression lifetime of an inline constructor receiver.
Source acceptance, diagnostics, emission and checker authority stay unchanged.

## One placement authority

Invariant: each admitted named temporary has one semantic storage identity,
declaration scope and ordered initialization site, shared by C++ emission
and MIR; no consumer reconstructs them from generated names or C++ text.

The preparation interface shares `TempState`'s pending queues, conditional
regions and flush decisions. An immutable body-level THIR temporary plan is
prepared before C++ printing. Emission consumes the
plan's decisions; the captured THIR artifact supplies the same plan to MIR.
MIR still consumes THIR, not an emission log or a separately rendered body.

Each entry needs:

- A body-local occurrence identity independent of the `__tmp_N` counter.
- Its initializer THIR, verified storage type, argument form and access.
- Direct or optional backing, separately from initialization placement.
- A declaration anchor and actual enclosing block identity.
- An ordered initialization anchor: statement prelude or lazy operand prefix.
- Scope parentage and repeated activation boundaries, including synthetic
  blocks introduced for conditions.

An initializer does not necessarily run where its `THIRArgTemp` appears.
The emitter renders arguments recursively, queues their declarations, and
flushes eager initializers before the statement. Deferred initializers run
as an ordered prefix before the selected operand's inline work. Nested
initializer temps register before their enclosing temp. The plan must
retain that schedule, not substitute a source or parameter-order guess.

The preparation and emission paths must reuse the same scheduling primitives
and banking eligibility decision (`banks_in_region`). The planner must not
become a second collection of emitter-shaped predicates. C++ naming remains
in emission, preserving module-wide counters and existing output exactly.
There is no dry-run printing pass, duplicate witness accounting or live
counter consumption during preparation.

The first plan covers ordinary bodies whose temporary-producing operations
and scope structure it understands completely. An unhandled temp producer,
unevaluated render or declaration relocation makes the plan unavailable for
MIR. Such a body still follows its existing C++ emission path. Shared
scheduling decisions must be extracted rather than copied into this path.
Absence of a plan is never a new source-language error.

## Scope and initialization boundaries

| Position | Declaration scope | Initialization |
| --- | --- | --- |
| Ordinary statement | Current emitted block | Ordered statement prelude |
| Lazy operand | Statement's declaration block | Selected operand's ordered prefix |
| First `if` condition | Parent block, including statements after the join | Before the `if` |
| Temp-bearing `elif` | Synthetic `else` block enclosing the remaining chain | On entering that conditional leg |
| Temp-bearing `while` condition | One iteration block enclosing condition and body | Each condition evaluation, including the final false evaluation |
| Statement inside a branch/loop body | That emitted body block | Before the statement, or its lazy operand prefix |

For `while`, continue and the normal backedge end the iteration activation
before the next condition; false, break and return end it on exit. Condition
temps survive the true body. Existing full-expression regions remain separate
and nest inside the appropriate block; they cannot shorten named backing.
The MIR backedge must leave and re-enter the iteration region through an
outer-region bridge: an edge within one region does not reset activation.

## MIR integration

Reuse `MIRDefinitions`, record storage, `MIRConstruct`, `MIRBorrow`, optional
backing initialization, engagement facts, region activation and scope-end
inspection. No new lifetime solver or effect-summary rule is needed.

Lower direct initialization at its planned prelude. Declare deferred backing
at its planned declaration anchor, initialize its payload only at the selected
prefix, then borrow the initialized record for `MIRCall`. At the argument
occurrence, read the established binding rather than reconstructing it.
Keep the scalar call result in a surviving region where required.

The existing while builder needs an iteration region enclosing both the
condition and body when that is the emitted shape. Synthetic elif scopes
must likewise come from the shared plan. A function with these owned temps
can be analyzed as a caller but remains opaque as a callee under M4.1's
current no-owned-storage summary contract.

## Producer and consumer

THIR preparation supplies validated typed anchors, storage identities and
scope structure. Emission uses them without changing output. The bounded MIR
consumer lowers the same storage and initialization schedules into existing
regions and engagement operations, retaining argument uses in the debug dump.

The shared queue determines declaration and initialization scheduling before
printing. Emission consumes optional-backing and synthetic-scope decisions
and checks actual registration, lazy-prefix and flush events against the plan,
including anchor identity, block parentage and order. The supported body
structure is traversed by both preparation and emission; these checks guard
against drift between those traversals.

The MIR consumer uses existing record storage, borrowing, optional engagement
and region operations. Its whole-body plan covers ordinary statements and
if/while bodies, plus the bounded range/native body and else scopes in the
[for-loop extension](MIR_FOR_ARGUMENT_STORAGE_PLAN.md). Any unhandled producer
or statement kind, including try, with, match and nested definitions, leaves
the whole body unplanned. Loop-head argument materialization remains excluded.
Lazy while conditions are source-pinned using a ternary;
the existing frontend rejects the boolean `and` spelling with a record
constructor argument; see
[`while-bool-record-argument-rejects`](../BUGS.md#while-bool-record-argument-rejects).

## Factored scope matrix

A cell is admitted only when every axis below admits it. Existing MIR
features outside the new materialization are not implicitly widened.

| Axis | Implemented coverage | Excluded / tracked scope |
| --- | --- | --- |
| Caller | Ordinary free functions, methods, constructor tails; supported branch/loop body statements | Module, closure, comprehension, match: W2/W5; generator/async: W4; context manager, try/finally, error-return: W3; member/base initializers have no body flush point |
| Callee | Exact ordinary function with finalized M4 reader-only, normal-returning bool/int32 summary | Method/static/constructor/native/protocol/callback/generic callees and general effects: M2/M4 |
| Shape | Verified hook-free record with bool/int32 fields; stable scalar local/parameter names or literals as constructor operands; preserve readonly borrowing | Tuple, Optional/union argument payloads, str/bytes, Own transfer, Ptr/Span, Box/Rc, containers, inherited/generic/hooked records: W1/M2/M4/W5; optional implementation backing is not Optional argument admission |
| Result/use | Borrowed argument to scalar-result call; already-covered scalar local, reassignment, return, field RHS and conditions | Reference result, storage in fields/container elements/globals/captures: W5/M4; scalar result placement does not admit those reference escapes |
| Evaluation | Eager and lazy supported expressions with proven prelude/prefix order; multiple pure named temps | Effectful/nested constructor arguments, named temps in range/iterator acquisition, value-select LHS hoists and chained-compare statement expressions need their own placement audit: W1/W2/M4 |
| Lifetime | Actual lexical blocks, synthetic elif block, fresh while/for body activation; normal exit/break/continue/return | Partial construction, exceptions and custom cleanup: W3/M4; frame persistence: W4 |
| Spelling | Direct and imported aliases with the same verified constructor/callee facts | Module-qualified constructor routes without those facts remain uncovered under M2/M4; no inference from the spelling |

A broader expression that could reorder observable inline work across a
hoisted initializer stays uncovered without an ordering proof. Mutation of a
constructor operand is excluded. Early construction is unobservable only
because its operands are stable and its initialization has no hooks or
effects; this is not a general no-exception guarantee for surrounding input
reads. Callees still require the normal-return-only summary contract.
Reordered keyword arguments need every reordered evaluation to be unobservable.
Existing source acceptance and general Python/C++ keyword-argument ordering are not
claimed by this subset.

## Verification and pitfalls

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`: use `@nocopy` and
  generated reference-parameter assertions; MIR calls borrow the planned
  backing. No new copies or warnings. Hook-free source output cannot prove
  destruction timing; structural checks must.
- `tuple-equals-scalar`: do not admit tuple argument forms through scalar
  metadata. Internal retention controls use scalar, singleton/mixed tuple,
  Optional and union holders to pin the shared storage boundary.
- `same-construct-every-position`: one plan for function/method/tail bodies;
  pin first-if, elif, while, return and body-local cases. Exercise direct and
  imported spellings; explicitly test uncovered position/shape siblings.
- `conditional-operand-evaluates-in-place`: assert both declaration and
  initialization anchors, skipped payload construction, nested lazy prefixes,
  multiple-temp order and fresh loop activations. Existing observable
  `short_circuit_arg_temp`, `cond_operand_arg_temp_families` and
  `chain_compare_arg_temp` cases guard emission, not MIR coverage.
- `generic-equals-monomorphic-twin`: generic MIR remains uncovered; compare
  relevant generic and concrete emission controls during the shared refactor.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: no view
  or allocation policy changes; byte-identical snapshots are the gate.
- `runtime-template-kind-matrix`, `const-source-const-loop-var`: no runtime
  template or iteration-binding changes; preserve reference access at calls.
- All four diagnostic pitfalls: no new source diagnostic or rejection;
  unsupported/missing plans return MIR not covered, malformed evidence is a
  validation failure. Existing diagnostics and snapshots remain unchanged.

Embedded source tests must exercise the production plan and inspect real
MIR. Do not have compiler tests load snippet files. Test missing/foreign or
incomplete anchors, invalid access, uninitialized reads, early scope ends,
retained aliases and excluded summaries. Keep ordinary source cases only
where they pin additional observable behavior; add at most one condensed
case for an identified gap.

## Evidence and risk

Source inspection confirms the queues and flush points in `TempState`,
`TempSink`, `_emit_if`, `_emit_while` and the conditional expression renderers.
Standalone probes using `@nocopy Cell` match CPython for eager, lazy, elif
and while callers; emitted C++ has the scopes described above. MIR covers
those bounded temporary-bearing callers and the reader leaves.

The bounded slice preserves CPython behavior through the enclosing-expression
ordering gate, pure construction, exact access preservation and exclusion of
identity/reference escape. Longer C++ block
lifetime is unobservable only because lifecycle hooks and escaping references
are excluded. Preserving C++ text alone does not prove Python ordering.

The main maintenance risk is extending supported body structure without
updating both preparation and emission verification. Planned/unplanned C++
comparisons, anchor-identity corruption tests, and structural lifetime tests
pin that contract. Broader coverage must preserve it. Other tracked hazards
remain in the dependency investigation; this subset does not assume those
source shapes work.
