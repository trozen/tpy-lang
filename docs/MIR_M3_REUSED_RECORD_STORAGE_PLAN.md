# Hoisted and reused record writes: M3.26/M3.27

Status: design approved. M3.26 implemented; M3.27 remains to implement.
Base: `228154ec92`. Architectural scope: extend existing THIR/MIR storage
facts and validation for copy/move operations. This advances W1 in the
[M3 completion checklist](MIR_M3_COMPLETION_PLAN.md); it does not complete W1
or M3. Tuple completeness remains a separate workstream.

## Contract

For a verified `Cell` with an `int32` field, this already compiles:

```python
def moved(flag: bool) -> int32:
    original = Cell(3)
    if flag:
        target = original
    else:
        return 0
    return target.value
```

C++ declares `std::optional<Cell> target` before the branch and writes
`target = std::move(original)` inside it. MIR currently reports not covered.
An explicit `target = copy(source)` at that position has the same gap.
The extension analyzes those existing operations and their emitted storage.

Invariant: a write reads its source before initializing or replacing the
selected backing; physical lifetime, payload engagement, source assignment
and dependencies held by surviving aliases remain distinct.

Source acceptance, C++ emission, diagnostics and checker authority stay
unchanged. An unsupported body remains MIR not covered. No new syntax,
storage strategy, write-mode enum, dataflow lattice or resource-move model.

## Precedent and measured boundaries

Reuse M3.3/M3.4's backing-site and retention model, M3.14/M3.15's optional
record backing, and M3.24/M3.25's cyclic copy/move contract. The subsequent
[local binding consolidation](LOCAL_BINDING_LOWERING_PLAN.md) supplies the
source operations; this work must not infer operations from C++ spelling.

Design probes on the base compile and match CPython for zero/multiple
iterations and both branch outcomes:

| Source shape | Existing emitted storage | MIR boundary |
| --- | --- | --- |
| Branch-hoisted last-use move | One optional backing; guarded move write | Assignment lacks `optional_record_assignment` |
| Branch-hoisted explicit copy, including readonly source/receiver | Same backing; guarded copy write | Same missing fact |
| Repeated explicit copy without retained old alias | Assignment through current holder | Replacement requires constructor |
| Repeated copy with an alias retaining the original | Separate body backing for the replacement site | Replacement requires constructor |
| Present Optional-record holder replaced by copy | Assignment through present holder | Optional replacement requires constructor |
| Loop copy whose alias escapes the loop | Body-owned optional slot, assigned at the loop site | Loop copy requires scoped storage |

The last shape can carry the existing warning about repeated storage reuse.
The probe only observes the final alias and does not claim that an earlier
alias keeps an independent old object after the next write.

## Existing model, extended coherently

| Write mode | Meaning to preserve |
| --- | --- |
| `INITIALIZE_ONCE` / `INITIALIZE_REGION` | Existing initialization and activation rules, unchanged |
| `OWN_SITE` | One private body backing per static site; repeat execution replaces that same backing |
| `OPTIONAL_ASSIGN` | Assign into physically constructed optional backing; first engagement differs from replacement |
| `IN_PLACE` | Replace the referent of the exact named holder; exempt only that holder from retention conflicts |

Optional physical backing and an Optional reference holder are different
concepts. Wrapper construction does not assign the source variable or engage
the record. An Optional holder must be present before an in-place write.

`MIRDefinitions` continues to verify concrete bool/int32-field records, their
constructors and absence of custom copy/move/destruction hooks. Copies need
copyable records; reusable assignments retain their movable-record gates.
Move sources remain fixed owned, mutable, exact-type locals, with definite
assignment checked on every path. Borrowed/readonly sources may be copied.

Default moves of these scalar-field records preserve scalar source contents.
This is the existing M3.24 contract, not permission to move arbitrary source
program values. Keep the direct self-move rejection. An `IN_PLACE` target's
root is a holder, so unequal root IDs do not establish referent disjointness:
pin admitted indirect overlap as read-before-write, with ordinary retention
checks. No new disjointness or use-after-move analysis is required here.

The storage, dependency, retention, presence and scope-end consumers already
handle the relevant operations. Extend them only if a regression witness
shows a missing consumer rule; do not add constructor-versus-copy branches
that duplicate the storage mode's decision.

## Commit boundaries

### M3.26: internal storage operation coverage

- Admit `MIRCopy` and `MIRMove` under the three existing replacement/assignment
  modes, retaining placement, type, access, engagement, static-writer,
  activation and exact rebind-owner checks. Keep body initialization in a
  cycle invalid.
- Extend the corresponding internal THIR-to-MIR admission gates and reuse
  `record_value` and existing destination selection. A body-owned hoisted
  declaration remains a reusable site, not a fresh per-iteration region.
- Extend THIR validation of `optional_record_assignment` to semantic copy
  and move values of the same record. Constructor/copy storage forms and
  move source-read forms keep their own invariants; never retag a move merely
  to satisfy a blanket storage-form check. Reject conflicting facts.
- Add internal tests for each mode and all consumer invariants below. The
  constructor-only negative tests become positive operation tests; malformed
  facts, invalid sources and unsupported shapes remain negative controls.

Source bodies whose existing facts are already complete can become covered
in this step. Verify those changed boundaries here; do not add a temporary
gate just to defer their coverage to M3.27.

### M3.27: production facts and source integration

- Emit `optional_record_assignment` for the already-selected hoisted record
  copy/move operation, using the existing record metadata helper and target
  facts. Do not broaden frontend admission or change the source operation.
- Use existing positive `owned_storage`, placement and `RebindStorage` facts
  for escape-hoisted declarations and replacement writes. Do not reconstruct
  backing placement from names or generated templates in MIR.
- Add source-to-MIR tests for actual produced combinations across free
  functions, ordinary/static methods and constructor tails within existing
  if/while/range/native-for admission. Pin storage modes, backing identities,
  interpreter values, retention results and the debug dump.
- Equivalent direct, renamed-import and module-qualified copies resolving to
  the same semantic operation share coverage. Unproduced combinations stay
  internal coverage; do not alter source semantics to manufacture witnesses.

## Factored scope matrix

Every row is an admission condition. The intersection defines the batch;
excluded cells remain owned by the named work package, not silently closed.

| Axis | Covered by regression sections | Exclusion / owner |
| --- | --- | --- |
| Callable | Free function, ordinary/static method, constructor tail | Module and comprehension execution: W2; closures/lambdas and call effects: W2/W5/M4 |
| Control flow | Existing if, while, unit-step int32 range and supported native-for; joins, skipped writes, break/continue/return | Match guards/bindings and broader iteration: W2; with, try/except/finally, error-return and custom cleanup: W3/M4 |
| Suspension | Existing analysis-exclusion controls | Generator and async frame writes: W4; neither uses ordinary body backing |
| Record | Concrete hook-free bool/int32 fields; bool/int32 scalar controls | Nested/reference-valued fields and aggregates: W1/M2/W5; custom hooks: W3/M4; generic/protocol/enum families retain existing definition gates |
| Wrappers | Plain and present Optional holders; optional physical backing; readonly copy sources | Owning Optional/union/tuple copy or move: W1/M2; no qualifier stripping to force admission |
| Retained holders | Plain/readonly alias, singleton/mixed borrowed tuple, Optional and union leaves | New owning tuple producers/nesting: separate tuple work and W1/W5 |
| Other types | Existing unsupported-body controls | str/bytes views, Own call boundaries, Ptr/Span, Box/Rc and containers need W1/M2/M4/W5 facts; no new source/storage family |
| Source | Named local/parameter/receiver copies; fixed-owned local moves | Field/subscript/accessor sources, reassigned/hoisted move sources and general calls remain outside existing `record_value` admission |
| Destination | Hoisted optional backing, private body site, mutable plain/present Optional referent | Param reseating stays excluded; reference returns, field/container/global stores and captures: W5/M4 |

## Verification

Internal cases distinguish first engagement from replacement, one repeated
site from distinct sites, and old-holder retention from rebinding the current
holder. A live third alias must remain visible to retention; RHS-only and dead
holders must not become live-after conflicts. Exercise all borrowed holder
shapes in the matrix, including a loop-carried alias from an earlier write.

Read the source before overwriting: include self-copy and indirect scalar
move overlap. Preserve readonly source/destination distinctions. A move source
may be body-owned or freshly activated in the loop; skipped initialization
and reuse after region end must still fail validation. A repeated write must
not fabricate a new physical lifetime or erase a prior scope-end conflict.

Source tests assert the actual emitted mode before checking the MIR result.
Use zero/multiple iterations, both branch outcomes, nested regions and normal
early exits. Strengthen retained-copy witnesses by mutating the saved old
object after replacement and observing both objects. Custom hooks, effectful
constructors, reference fields and excluded positions remain MIRNotCovered.

Reuse existing `auto_move/local_storage_binding` snippet sections for scoped
and hoisted moves/copies, live aliases, copy independence and escape storage.
Only add a condensed snippet if the inventory lacks a meaningful runtime
distinction; do not duplicate source-to-MIR tests with parity-blind snippets.
Unit tests use in-memory source, never read the snippet corpus.

Existing generated C++, diagnostics and runtime output must be byte-identical.
THIR debug metadata and MIR dump coverage may change. Run targeted internal
and source checks during each step; cumulative specialist review and one full
`rpytest --force-exec` after the final code changes, then readiness. Each
implementation step finishes as one commit on a stacked branch; master merge
remains the user's decision.

## Pitfalls, risks and documentation

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`: preserve the selected
  source operation; mutation/nocopy controls and byte-identical snapshots.
- `tuple-equals-scalar`: retained tuple leaves carry the same dependency as
  their scalar alias; owning tuple completeness is not inferred from that.
- `same-construct-every-position`: one record operation/write contract;
  callable and copy-spelling probes, with exclusions stated in the matrix.
- `conditional-operand-evaluates-in-place`: writes stay below their guards;
  source reads precede replacement; skipped paths retain availability rules.
- `generic-equals-monomorphic-twin`: no generic emission or eligibility
  change; unsupported definitions remain explicit coverage boundaries.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: no C++
  change or new view/container/heap-backed operation.
- `runtime-template-kind-matrix`, `const-source-const-loop-var`: runtime and
  iterator production are unchanged; retain const-source alias controls.
- Diagnostic pitfalls: no source diagnostic or rejection change; debug
  analysis limitations remain distinct from language restrictions.

Primary risks are mistaking optional wrapper construction for initialization,
reinitializing one reused site as fresh storage, dropping surviving aliases,
and relaxing source eligibility while lifting destination restrictions.
Existing finite dataflow lattices and solver complexity remain unchanged.

Expected code touchpoints: `thir/validate.py`, `thir/lower/statements.py`,
`mir/validate.py`, `mir/lower.py`, and focused tests. No parser, sema, type
system, runtime or stdlib behavior change is planned. Update this plan,
LANGUAGE_FEATURES, the M3 checklist and TODO as each step lands.

Independent internal-model survey and CPython-parity assessment found no
design blocker within this boundary. No new untracked source defect was
found. Confidence is high for the existing model; implementation must still
verify each admitted operation against the consumer and source matrices.
