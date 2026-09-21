# M3.12/M3.13: physical initialization and wrapper hoists

Status: M3.12 and M3.13 implemented, 2026-09-21; merge status is separate.
This is the first batch of W1 in the
[M3 completion checklist](MIR_M3_COMPLETION_PLAN.md#w1-storage-lifecycle).
It is architectural and analysis-only: no source acceptance, diagnostics,
C++ emission, runtime or checker-authority change.

## Concrete contract

```python
def example(flag: bool) -> int32:
    if flag:
        value: int32 | None = 1
    else:
        value = None
    if value is not None:
        return value
    return 0
```

This already runs and returns 1 or 0. Emission declares
`std::optional<int32_t> value;` before evaluating the `if` condition, then
assigns it in each arm. The physical wrapper is initially empty, but Python
has not assigned the source name. Before M3.12/M3.13 MIR left this hoist
uncovered: one assigned set could not express both facts.

The scalar union sibling declares `::tpy::Union<bool, int32_t> value;`.
The runtime default constructor delegates to `std::variant`; its actual first
alternative is value-initialized. That can be false/zero or a monostate,
depending on the ordered layout. It must not be modeled as universally empty.

**Invariant:** physical construction makes storage available for assignment
and lifetime analysis, but only a source assignment makes a Python binding
available to read. Both properties follow the object's actual activation.

## Evidence and precedents

- `THIRHoistedBinding` distinguishes initially unassigned hoists and their
  placement. Its typed facts now carry physical defaults; MIR does not parse
  `hoist_decls` render strings.
- `_value_hoist_entry` in `thir/lower/statements.py` is the shared scalar and
  wrapper declaration producer. `storage.hoisted_binding` records scalar
  Optional/union defaults using `optional_layout` and `union_layout`.
- `mir/validate.py` requires constructed storage for wrapper assignment,
  independently of source assignment. RHS reads still require assigned bindings.
- `MIRPayloadWrite` distinguishes initialization from assignment.
  `payload_lifetime` and `scope_lifetime` separate payload replacement from
  scope ends, including physical defaults without a source write.
- `MIRRegionFlow` distinguishes source residence resets from physical storage
  endings. The two coincide for scoped scalar wrappers, but not in general.

Compile/run probes covered Optional and union hoists in free functions,
constructor tails, and a method's while/else. Native TPy and CPython outputs
matched: Optional 0/1, union 0/2, constructor 0/3, method 0/3. Union probes test
the builtin bool alternative so CPython does not depend on an annotation
converting a plain int into the int32 stub class. Inspected C++ confirms
predeclarations precede conditions and later writes assign existing wrappers.

## Representation and transfer rules

Keep one slot identity per inline wrapper. Its existing payload projection
continues to name the same physical payload. Separate holder/backing slots
would invent an addressable wrapper-alias model: the existing wrapper-copy
operations copy values, while record alias operations refer to records.

`MIRStorageInit` is an explicit physical-default-initialization instruction
in the statement union. It carries a local destination,
source location and typed default selection/value. It is not a source
assignment or an arbitrary expression evaluation. Initially support only
scalar Optional and nonrecursive scalar union wrappers.

| Operation | Physical construction state | Source assignment state |
|---|---|---|
| Wrapper parameter on entry | Already constructed; preserve existing parameter duration | Assigned |
| Physical default initialization | Constructed with actual default selection | Unassigned |
| Ordinary initialized wrapper declaration | Constructed | Assigned |
| Python assignment into a predeclared wrapper | Must already be constructed | Becomes assigned |
| Source residence reset | Follow actual physical placement separately | Cleared |
| Physical storage end | Cleared | Follow source residence separately |

Validation maintains separate must-facts for physical construction and source
assignment. Preserve unique initialization-site, owning-region and
zero-or-once-per-activation checks; a backedge cannot repeat construction in
the same activation. Ordinary reads, copies, extracts and branch conditions
of an inline wrapper need both source assignment and live constructed storage.
Preserve existing placement/residence constraints rather than permitting an
assigned-but-dead wrapper read. In particular, `value = value` cannot read the
physical default before the first Python write. Parameter controls preserve
scalar Optional's by-value convention and scalar union's borrowed CALLER
storage; a local predeclaration must not change either convention.

These must-facts do not replace the scope-end inventory's may-initialization
facts. Conditional construction can produce a possible storage end at a join
without proving that assignment into that storage is valid on every path.

Physical default initialization seeds presence/selection facts without
granting a source read. Source assignment validation still precedes presence
reasoning: the physical tag must not make an invalid source read disappear
through an allegedly impossible edge. Initialization also respects existing
freshness invalidation; reconstruction never revives an old payload alias.

The first Python write is physical ASSIGN, not INITIALIZE. Switching away
from a union's default scalar alternative can therefore end its old payload.
Assigning into an initially empty Optional has no old payload to end. On an
early exit with no Python write, the Optional wrapper still ends; a scalar
union also ends whichever default payload is active.

Every instruction consumer handles it: validation, selection/freshness,
liveness, dependencies, payload/scope ends, record-retention inspection, dump,
reachability/slot inventory and the MIR test interpreter. Centralize instruction
use/definition classification where shared; do not duplicate a new CFG walk.
Physical initialization has no source uses or source definition. Lifetime
inspection of an old payload is not a source-level read. The interpreter must
not expose its physical default through an unassigned source binding.

THIR supplies optional/union layout, actual default selection/value, physical
placement and initially-unassigned source state at the producer choosing the
predeclaration. MIR validates complete name/order correspondence and fact
compatibility before lowering. Missing/unknown facts remain uncovered; invalid
MIR remains a validation error. Ordinary uninitialized bool/int32 predecls must
not acquire invented false/zero values through this extension.

`THIRHoistedBinding.physical_default` carries `THIRWrapperDefault` with the
actual tag and typed scalar/None value; its optional/union layout describes
the physical wrapper. The shared `storage.hoisted_binding` producer records
these alongside the existing declaration spelling. Optional payload literals
contextualized with the wrapper type are checked at that destination, including
the exact coercion, scalar type and int32 range; C++ emission does not change.

Direct MIR tests pin construction versus assignment, actual default tags,
must-construction versus may-end joins, repeated activation and stale aliases.
Pipeline tests pin copied snapshots after reseats, free/method/constructor
siblings, while/else, nested guard writes, break/continue/return scope ends,
emitter placement and damaged producer facts. Existing record/tuple/readonly
and parameter controls remain in the MIR suite.

## Batch boundaries

1. **M3.12: physical initialization as an independent MIR event.** Add the
   instruction and shared classification, strict state transitions, all
   analysis/dump/interpreter handling, and hand-built MIR tests. Existing
   THIR coverage remains unchanged. This is a complete internal IR contract,
   not unused metadata awaiting a later consumer.
2. **M3.13: scalar wrapper hoists through the complete pipeline.** Extend the
   shared THIR producer and validation facts; lower the new instruction at
   actual predeclarations; admit eligible Optional/union if/while hoists in
   existing free/method/constructor bodies. Add source-to-THIR-to-MIR tests,
   emitter-agreement checks and update coverage docs.

Develop and review these together, one final commit per step, one full forced
suite at the final batch state. Do not merge to master. Keep the work stacked
above M3.11 until the user's merge, or continue from master if it already
contains that work. The plan checkpoint can be folded into the implementation
batch when preparing its final history; preserve M3.11 as its own commit.

## Scope matrix and following storage work

Coverage is the intersection of these axes and existing operation gates:

| Axis | This batch | Explicit remaining work |
|---|---|---|
| Position | Existing synchronous monomorphic free functions, methods, constructor tails; if/elif/else and while/else | Module and closure bodies, generators, async, comprehension, context managers, try/finally, error-return, match and for: W2-W5; generic obligations: M4 |
| Shape | Optional bool/int32; nonrecursive unions of supported bool/int32 and optional None, preserving ordered layout | Optional-storage records, owning/mixed tuples/unions: W1; other scalars, str/bytes, views, Ptr/Span, Box/Rc and containers: M2 facts plus W1/W5 |
| Slot | Local wrapper hoists in their actual containing scope; existing parameter and scalar-return controls | New wrapper parameter reseats/returns, fields, elements, globals/captures and escapes: M2/W5/M4 |
| Qualifier/operation | Existing scalar wrapper copies, selection, assignment, readonly-read controls and normal exits | New readonly writes, Own contracts, record copy/move/IN_PLACE cycles and custom effects: W1/W3/M4 |

Optional-storage record hoists are the next distinct W1 backing problem:
`_optional_storage_hoist_entry` emits `std::optional<Cell>` for source type
`Cell`. That is implementation backing, not source type `Optional[Cell]`.
It has one backing per binding in the containing scope; existing BODY OWN
backing has one site per replacement operation. It needs positive backing and
constructor-assignment facts, and a third distinction: whether the record
payload is engaged. Do not admit it by reusing scalar wrapper layout or by
mislabeling its writes OWN_SITE. Owning aggregates and cyclic copy/move/in-place
also remain explicit W1 boxes; this batch does not complete all storage work.

## Tests, pitfalls and approval gate

- Direct MIR tests: default Optional absence and None/scalar-first union tags;
  first/repeated source writes; source-unassigned reads/tests/copies; missing-arm
  and zero-trip joins; same-activation double initialization; loop-region
  reentry; mismatched layout/default/placement facts.
- Lifetime tests: early return/break/continue before source assignment; empty
  Optional versus active default union payload; same/different alternative
  writes; stale aliases after clear/reconstruction; BODY versus scoped controls;
  conditional construction distinguishes must validation from may-end inventory.
- Pipeline tests: both wrapper families across free/method/constructor bodies;
  nested if/while/else; source snapshots retained across wrapper reseats; guard
  and RHS writes stay at their reached evaluation points. Existing source
  rejection and MIR-not-covered cases remain negative controls.
- Aliasing/copy and tuple symmetry: scalar wrapper copies preserve snapshots;
  record/tuple/readonly controls preserve shared mutation. No new owning tuple
  admission or copy-warning site is introduced.
- Position symmetry and generic parity: shared producers feed every admitted
  position; generic/resumable routes retain explicit gates, not fabricated facts.
- Conditional evaluation: physical defaults evaluate no source RHS, and neither
  hoisting nor analysis moves the actual source writes or their operands.
- Views, hidden allocation, runtime template kinds, const loop slots and C++
  readability: no runtime/emitter change; compare existing emission for the
  wrapper probes and inspect for extra materialization.
- Diagnostics and valid Python: source behavior remains unchanged. Physical
  defaults cannot authorize unbound reads, suppress a valid diagnostic, or
  create a new warning. Independent design-time CPython assessment: match
  under these constraints.

Prefer unit-owned source and direct MIR tests: runtime output alone cannot
test the distinction. Add a snippet only for an uncovered source-level contract,
not to duplicate an existing runtime witness. No existing snapshot change is
planned; any such change requires consultation.

Update the completion checklist and the existing MIR status/coverage docs with
each delivered step. Confidence: high for the scalar-wrapper batch; record
engagement is deliberately not represented by an unverified shortcut here.
The approved design introduces an instruction and a new invariant for every
MIR consumer; changes to that contract require renewed consultation.
