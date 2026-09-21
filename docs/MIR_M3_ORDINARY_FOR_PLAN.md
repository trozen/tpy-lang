# Ordinary for-loop analysis: M3 W2 batch

Status: approved. M3.19 implements the internal model and THIR facts;
M3.20 connects unit-step int32 range CFG. M3.21 native integration is in
progress. Review/merge status is separate.
Base: `298e4df83d` (M3.18 merged).

## Contract

Extend analysis-only MIR to ordinary range and native-container iteration,
including iterator/source dependencies and borrowed record elements:

```python
def change(xs: list[Cell], stop: bool) -> int32:
    result = 0
    for cell in xs:
        cell.value = 7
        if stop:
            break
    else:
        result = 1
    return result
```

The existing C++ captures `xs` by reference, captures its begin/end iterators,
and binds each `cell` by reference. MIR must preserve that element identity,
the dependency on the source, and the distinction between exhaustion and
`break`. This adds inspection coverage, not new Python acceptance, C++ emission,
warnings, borrow-checker authority or move decisions.

Classification: architectural. The invariant is that each loop captures its
source once, retains every dependency needed by its iterator and live element
holders, and assigns its target only on a successful iteration, with regions
and transfers matching the emitted program.

Tuple-language completeness stays in a separate session and remains a 0.6.0
release requirement. This batch advances W2 and supplies its necessary M2 place
facts; it neither closes all of W2 nor declares M3 complete.

## Evidence and existing patterns

- MIR's `THIRWhile` lowering already separates exhaustion/else from break and
  restores the enclosing loop stack before lowering else. Extend that pattern;
  a for-loop continue targets an advance block rather than the condition.
- `_Builder.scoped` and `MIRRegionFlow` already implement binding restoration,
  inner-first scope exits and per-activation resets. Bind body-local targets
  inside the iteration-body region, before lowering source body statements;
  preserve the other range-target residences described below.
- `_lower_hoist_predecls` already accepts a `THIRHoistedBinding` inventory.
  The for producer currently requests only rendered declarations; request the
  existing semantic inventory as well. Never parse C++ declaration strings.
- `loop_var_binding` is the shared loop/comprehension binding decision. Cheap
  scalars copy, records alias, and composite value types can reference-bind.
  Share that decision with the new semantic facts rather than adding a second
  value-type predicate or extracting meaning from `auto&&`.
- `IR_DESIGN.md` already specifies separate container object, structural and
  summarized element places. Current MIR implements none of these container
  projections or iterator holder kinds; do not disguise them as plain records
  whose dependency inventory is empty.
- Source probes verified existing range target/induction separation and
  mutable record-element loops in free functions, methods and constructor
  tails. Additional compile-to-THIR probes verified every listed native source
  family and readonly list/Array record elements. No source behavior is changed
  by this plan.

## Representation and lowering

### Semantic producers

Carry typed facts for borrowed container parameters and fixed aliases, native
iteration sources, element type/access, target binding mode, iterator source
dependencies and physical placement. Facts must come from the existing type,
route and binding decisions. Missing or conflicting facts fail coverage.
Add positive hoist inventories to range/native THIR nodes through the existing
hoist producer. Check the implicit target independently of body declarations.

No parser, sema language rule, runtime or stdlib change is intended. THIR
producer/validation work and a shared binding-decision extraction may touch
codegen helpers, but generated C++ must remain byte-identical.

### Places, holders and transfer rules

Introduce a borrowed-container holder and a native-iterator holder, with typed
layout/access facts. Represent structural and summarized element regions as
distinct projections of the captured container identity. An element projection
means any element, never a proof that two iterations or indices are disjoint.
Iterator operations retain the required structural and element dependencies;
an element alias independently retains the element dependency. Distinct
structural/element projections do not prove independence from invalidation:
structural writes may invalidate elements. Such writes stay uncovered until
their effect mapping exists; prefix-only place overlap is not that mapping.

Use explicit iterator initialization, availability, element-read and advance
operations. The iterator retains its captured source dependency even if the
source name later changes in internal IR. Source admission initially requires
fixed borrowed bindings. Resolve source aliases to their referents at iterator
initialization, and element aliases at the successful read; never re-resolve
either through a later value of the source-holder slot.
Scalar reads yield values; record reads yield aliases
of the element region, with constness preserved. Aliases stored in already
supported holders keep that dependency after the loop target dies.
Advancing the iterator never retargets a retained element alias: the summarized
analysis region is not a concrete element identity. The test interpreter must
retain the actual selected element. Reject reference-target reseating and
unsupported structural writes through every alias, not only the source name.

Extend validation/presence, use/def inventories, liveness, dependency transfer, overlap,
storage/region consumers, dumps and the test interpreter together. Copying a
holder transfers its referents; overwriting a holder kills only that holder's
old referents. A summarized element write cannot strongly update all elements.
There is no new structural mutation operation or invalidation policy here;
unsupported mutation/call paths keep the body uncovered.
Validate that iterator initialization precedes use and that an element read is
reached only on a successful availability edge. Advance invalidates the previous
availability proof; it does not invalidate an already retained element alias.

### CFG and actual storage scopes

For ranges, capture start/stop once in source order. Introduce a typed int32
unit-step induction operation; do not widen arbitrary source arithmetic as an
incidental consequence. Keep an independent induction slot when the body
writes the target or the target survives the loop. Zero iterations do not
assign the target; a previously assigned target keeps its value.
The for region has one initialization entry; head/advance backedges remain
inside it, so only the body activation resets on each iteration.
Reuse strict definite-assignment validation and its dedicated not-covered
fallback when a valid source body's nonempty-loop proof exceeds the solver;
do not weaken validation or promise new constant propagation in this batch.

For native iteration, capture the source and begin/end state once. Exhaustion
enters else; break skips it; continue and normal body fallthrough leave the
body region and advance before testing again. An early return leaves all
active regions. Nested loops, including a break/continue inside an inner
loop's else, must address the correct enclosing loop.

Match physical scopes, not an invented universal loop scope:

- Range bound captures live in the enclosing scope. When the target itself
  is the counter, it lives in the for scope across iterations. A body-written
  target instead has a fresh body binding copied from a hidden for-scope
  counter. A reused/hoisted target remains in its actual outer residence and
  receives the counter value only on successful entry to the body.
- Native source and iterator captures are emitted in the enclosing scope.
  Their physical residence survives the loop and else; their live dependency
  ends when no future operation or retained holder needs it. Fresh element
  bindings have the body activation; hoisted scalar targets keep outer residence.
- Protocol rvalue iteration has a different brace scope ending before else,
  plus a per-next-result backing slot. That producer is excluded until those
  facts and its call effects are represented.

## Factored scope matrix

Every axis must admit a cell. These are MIR coverage limits, not new language
restrictions; remaining cells stay in the completion checklist.

| Axis | Included in this batch | Explicit remainder |
| --- | --- | --- |
| Position | Free functions, ordinary methods, constructor tails; nested supported if/while/for bodies and loop else | Module, comprehension, match: remaining W2. Cleanup/context-manager/error-return: W3. Generator/async: W4. Closure/generic execution: W5/M4. |
| Range | int32 `range(stop)`, `range(start, stop)`, explicit literal +1/-1; bounds already representable as pure scalar reads/literals | Other widths/BigInt: M2. Non-unit/dynamic step checks and throwing bounds: W3/M4. General arithmetic/call expressions remain outside existing scalar coverage. |
| Native source | Fixed borrowed bare-name parameters/aliases: list and Array with int32 or plain scalar-field record elements; set[int32] and dict[int32, int32] key iteration | Owned/rvalue/local-container construction: W1. Fields/globals/captures, views/Span, strings/bytes, Ptr/Box/Rc, custom iterators and combinators: M2/W2/W5/M4. |
| Element | Scalar value or borrowed record, mutable/readonly access according to the producer | bool proxy/storage siblings, tuple/Optional/union/nested-container elements, unpack and consuming iteration: remaining M2/W2; tuple language fixes stay separate. |
| Binding | Fresh per-iteration target; reused/hoisted scalar target; supported body-local storage and hoists with complete facts; element aliases copied into existing supported holders | Reference target hoisted past the loop: tracked source defects below. Incomplete hoist inventories, container-source reseating and escaping into new sinks stay uncovered. |
| Slot | Borrowed container parameter/fixed alias; local iterator/target/holder; existing scalar field access and scalar returns | Container construction/replacement, field/container/global stores of references, reference returns and call boundaries: W1/W5/M4. |

The set/dict rows depend on the same positive native-iterator contract, not an
assumed list layout. Set tests must not rely on incidental iteration order;
dictionary key iteration must preserve insertion order.
Readonly is an access constraint on the same identity, never permission to
copy the element. Concrete Array instantiations are not generic-body support.

## Tests and pitfalls

- Internal IR tests cover iterator dependencies, external origin overlap,
  conservative element joins, source-holder replacement, nested field paths,
  retention past target death, and per-iteration activation/reset. Negative
  tests cover uninitialized/exhausted iterator reads and missing success edges.
- THIR-to-MIR fixtures owned by unit tests cover both directions of range,
  bound capture despite later writes, written targets, zero-trip and last-value
  cases, nested transfers, scalar hoists, mutable/readonly native elements and
  every admitted source family. Missing/malformed facts and every excluded
  source producer fail explicitly; compiler tests never open snippet cases.
- Interpreter assertions and `--dump-mir` checks pin the CFG, target assignment,
  canonical source/element places, liveness and retained dependencies. Do not
  use successful C++ execution as evidence that a body actually reached MIR.
- Reuse existing focused source cases where they already pin the contract;
  add one condensed case only for an uncovered source obligation. Borrowed
  elements need mutation-observing or nocopy witnesses. All positions share
  helpers, with free/method/constructor sections and clear subject comments.
- No new source diagnostics or snapshots for existing cases are expected.
  Investigate unexpected byte changes; consult before any existing refresh.

Pitfalls walk: `silent-copy-vs-alias`, `copy-warning-at-wrong-site` and
`const-source-const-loop-var` require access-preserving aliases and observable
mutation. `tuple-equals-scalar` forbids claiming tuple iteration from scalar
coverage; its gaps are explicit. `same-construct-every-position` uses shared
producer facts and the matrix. `conditional-operand-evaluates-in-place` pins
once-only captures and actual reached bindings. `generic-equals-monomorphic-twin`
keeps open generic bodies excluded. `view-not-copy`, `hidden-allocation`,
`runtime-template-kind-matrix` and `generated-cpp-readability` require unchanged
emission/runtime and source-family controls. `no-cpp-in-diagnostics`,
`no-internal-names-in-diagnostics`, `no-warning-on-valid-code` and
`reject-valid-python-only-as-documented-divergence` require unchanged language
diagnostics; debug coverage reasons remain separate from source rejection.

## Known boundaries and risk

- `BUGS.md#ref-loop-var-read-after-loop-copies` and
  `BUGS.md#readonly-loop-var-reseat-const-ptr` block admitting reference target
  hoists as correct aliases. Keep them uncovered; do not fix codegen here.
- `BUGS.md#loop-body-storage-outlived-by-loan` motivates retaining dependencies
  across target/body ends; analysis coverage is not a fix or a safety verdict.
- `BUGS.md#iter-borrow-place-needs-hops` and
  `BUGS.md#property-iter-loan-misses-getter-storage` require precise source
  provenance before fields/accessor sources can join.
- `BUGS.md#iter-invalidation-wording-assumes-borrow` is why an owned protocol
  iterator must not be modeled as automatically borrowing its source.
- `BUGS.md#dict-grows-during-own-iteration` is why structural iterator
  dependencies and element-reference dependencies must stay distinguishable.

No new defect has been established by this design survey. Independent design
review found no CPython-parity blocker in the admitted source slice; existing
defects above are not modeled as intended language semantics. Main implementation
risks are missed consumers
of new holder/projection kinds, overconfident summarized-element updates, and
incorrect region placement. Cover each with negative and cross-pass tests.

Design confidence: high for this explicit scope, based on inspected shared
producers, source-family THIR probes, and independent sibling/parity reviews.
This is confidence in the contract and boundaries, not implementation sign-off.

## Three-commit delivery

1. **M3.19 -- iteration identities and dependency model.** Add semantic source,
   binding and hoist facts, container/iterator holders and summarized regions;
   update every validator/analysis/dump/interpreter consumer with internal
   regression tests. Extract the shared binding decision without byte churn.
2. **M3.20 -- range CFG and target lifetime.** Add THIR range integration,
   int32 unit-step induction, capture/target separation, hoists and all normal
   transfers. Include source-owned analysis fixtures and any needed runtime
   regression sections in this commit.
3. **M3.21 -- native iteration and retained element aliases.** Connect admitted
   native source families, scalar/record bindings, readonly and hoisted scalar
   targets, with interpreter, dependency and source parity evidence. Update
   feature/checklist docs for the whole batch and leave protocol/other W2 work
   visible.

Each is a substantive implementation-and-tests commit, not a separate merge
request. Keep them stacked on one branch, review cumulatively, run one full
forced suite after final changes, and apply readiness/retrospective gates.
Leave three clean commits unmerged and unpushed for one user merge checkpoint.
Update LANGUAGE_FEATURES, IR_DESIGN and MIR_ANALYSIS_PLAN alongside the relevant
implementation. Approval covers these three steps, not tuple completeness,
arbitrary effects or exceptional/frame lifetime semantics.
