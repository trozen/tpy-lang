# Cyclic record storage: M3.24/M3.25

Status: M3.24 and M3.25 merged. The subsequent
[local binding consolidation](LOCAL_BINDING_LOWERING_PLAN.md) connects
source loop-local moves to the existing scoped-storage MIR contract.
Base: `9950ab55fa`. Architectural scope: extend the storage/activation
contract across THIR and MIR. This covers part of W1 in the
[M3 completion checklist](MIR_M3_COMPLETION_PLAN.md), not all of W1 or M3.
Tuple-language completeness remains a separate workstream.

## Contract

For a hook-free `Cell` with an `int32` field, this already compiles:

```python
def copied(source: Cell, n: int32) -> int32:
    result = source.value
    for i in range(n):
        duplicate = copy(source)
        duplicate.value = i
        result = source.value
    return result
```

Each iteration emits fresh `Cell duplicate = Cell(source);` storage.
Mutation of the explicit copy leaves `source` unchanged. The copy's backing
ends at the emitted scope exit, including continue, break and return paths.

```python
def replaced(n: int32) -> int32:
    current = Cell(1)
    for i in range(n):
        current = Cell(2 if current.value == 1 else 1)
    return current.value
```

With no retained alias, C++ assigns through the holder:
`(*current) = Cell(current->value == 1 ? 2 : 1);`. MIR reads the RHS before logical
replacement and preserves the backing's physical lifetime. A retained alias
can make the frontend select separate backing instead; MIR consumes that
actual verdict, never infers it from the assignment syntax.

Invariant: each admitted write either initializes fresh storage once per
activation, or records a logical replacement of already-live storage, while
every surviving holder keeps its dependency across loop edges.

Source acceptance, C++ emission, diagnostics and checker authority stay
unchanged. Unsupported analysis means MIR not covered, not a source rejection.

## Precedent and measured boundaries

- `INITIALIZE_REGION`, region transitions and activation validation already
  distinguish fresh activation from repeated initialization in one activation.
  The validator separately bans cyclic copy/move operations today.
- `IN_PLACE` already records the written referent and rebound holder.
  Retention resolves incoming dependencies and checks live-after holders,
  exempting only that holder. Plain and Optional-record lowering reject this
  mode in loops today.
- `MIRDefinitions` verifies concrete bool/int32-field records without special
  member hooks or constructor-body effects. Reuse that eligibility gate.
- Nested explicit copies compile, but take a generic `THIRCall` and local
  declaration without owned-storage facts. The function-top copy route uses
  `_lower_copy_record`/`lower_copy_construct` and `THIRCopy` to emit the same
  `Cell(source)`. M3.25 uses that semantic node at the nested declaration and
  shared named-record copy builtin routes, preserving declaration and
  last-use registrations as well as the emitted bytes.
- At the original baseline, `source = Cell(n); target = source` at the
  source's final use inside a loop failed before MIR with
  `decl.branch_slot_type`. The subsequent local binding consolidation
  resolves that frontend boundary and tests production THIR-to-MIR moves.

Design probes matched CPython without warnings. For source value 3 and
zero/three iterations, explicit and readonly copies printed `3 15 3`,
replacement printed `1 4`, and mutation through a retained alias printed
`0 36`. Keeping the original across replacement printed `11 11` and `14 1`,
using separate optional backing in C++. Constructor-tail and ordinary-method
probes also matched, as did `tpy.copy(source)` and renamed-import `clone(source)`.
Those initial parity probes used arithmetic; MIR witnesses use the supported
scalar assignments/conditions and range induction shown above. General
arithmetic remains outside the current MIR expression vocabulary.

## Commit boundaries

### M3.24: validated internal loop storage coverage

Admit copy/move into region-local record backing at the internal THIR/MIR
boundary using `INITIALIZE_REGION`. Keep one static initializer per backing,
owning-region placement, definite assignment and the proof that returning to
initialization first leaves its activation. Body-owned one-time initialization
must still fail in a cycle.
Fresh activation means a new object lifetime, not a distinct physical address.

Admit constructor-only `IN_PLACE` in ordinary loops, including the existing
present Optional-record holder shape. Preserve readonly, presence, constructor
purity and exact rebind-owner/projection checks. Multiple replacement sites
can affect one backing; they are not additional physical initializers.

Reuse storage events, dependency propagation, liveness, retention and scope-end
inspection. No new solver, generation counter or general move-state model.
Default moves of these scalar-field records copy scalar payloads; they do not
model general resource consumption. Internal move sources remain fixed owned,
mutable, exact-type, distinct from the destination and eligible under existing
definition checks. Pin both fresh-per-iteration and outer source storage.

### M3.25: source copy facts and integration

Normalize already-supported direct, non-hoisted record copy declarations to
the shared semantic copy node and existing owned-storage/SCOPE facts. Gate
this on the selected copy operation and supported source shape. Do not decode
C++ templates or infer copying from a callee name in MIR. Preserve the emitted
type, source read, readonly behavior, name registration and last-use decisions.
Byte-identical C++ is a delivery gate.

Pin source copies and constructor replacements in covered free functions,
ordinary methods and constructor tails under existing while, range and
native-for CFG admission. No new iterator coverage is implied. Equivalent
copy spellings resolving to the same operation must stay consistent. Imported
definitions use the existing verified-definition input.

Source loop-local move-through was excluded from M3.25. The separate local
binding consolidation now admits it and tests its scoped-storage contract
through production THIR. Hoisted/reused-slot move analysis remains excluded.

## Factored scope matrix

All axes must admit a cell; exclusions retain their owning work package.

| Axis | Covered | Gap / reason |
| --- | --- | --- |
| Position | Free functions, methods, constructor tails; existing if/while/range/native-for regions | Module/comprehension/match: W2. Closures: W2/W5/M4. Context managers, try/finally, error-return: W3. Generators/async: W4. |
| Record shape | Concrete hook-free bool/int32-field records and verified constructors | Nested/reference-valued fields, owning tuples/unions: W1/M2/W5. Custom lifecycle effects: W3/M4. |
| Wrappers/access | Present Optional-record IN_PLACE; readonly copy source into independent mutable storage; borrowed tuple/Optional/union retention probes | Owning wrapper/tuple copy/move: W1/M2 and separate tuple work. Readonly move/replacement remains invalid. |
| Other shapes | Existing scalar CFG controls | str/bytes, views, Ptr/Span, Box/Rc, containers, protocols/enums, generics need their existing M2/M4/W5 facts; no new ownership producer. |
| Slot | Scoped owned local destination; named local/parameter/receiver copy source; local replacement holder | Field/subscript copy sources remain outside this admission. Reference returns, field/container/global stores, captures: W5. Own call boundaries: M4. |
| Operation | Region-initializing copy/move; explicit-copy source producers; constructor IN_PLACE; subsequent source loop-move integration | Copy/move into reusable OWN_SITE, copy/move IN_PLACE and hoisted copy/move analysis remain W1 work. |
| Lifetime | Fresh activation, skipped writes, joins/backedges, normal early exits, logical replacement | Partial initialization, exceptions/destruction, suspension: W3/W4/M4. Banked argument temporaries: remaining W1/M4. |

## Verification and risks

Internal tests distinguish fresh copy destinations on repeated activation,
copy independence with live mutable/readonly sources, and move eligibility.
They cover skipped writes, nested loops, continue/break/return, multiple
replacement sites and RHS reads of the old payload. Negative controls keep
duplicate initialization, body-owned cycles, missing facts, readonly writes
and absent Optional payloads invalid.

Retention tests keep prior-iteration aliases through plain/readonly holders,
borrowed tuples, Optional and union payloads. Include RHS-only/dead-holder
controls and the exact rebind-owner exemption. Scope-end conflicts must remain
visible when a later iteration activates the same static slot.

Source tests compile in-memory source through production THIR and assert
facts, MIR execution and precise exclusion reasons. Add a CLI dump witness.
Reuse focused snippets where sufficient; add at most one condensed CPython
case for copy independence and repeated replacement across callable positions.
Snippet tests establish codegen/parity; internal tests establish MIR lifetime
behavior. Reference-boundary tests observe mutation or use `@nocopy`.

The principal risks are changes to last-use/move decisions during copy
normalization and over-broad removal of cyclic guards. Existing snapshots must
remain unchanged; investigate any difference before proposing a refresh.
Parser, sema, types, runtime and stdlib behavior remain unchanged. No new
dataflow lattice or asymptotic solver cost is expected.

After implementation: targeted tests, all review lenses, final full
`rpytest --force-exec`, readiness/retrospective and one squashed commit per
step on `mir-m3-cyclic-record-storage-ready`. The user merges.

## Pitfalls and documentation

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`: explicit copies isolate
  mutation; aliases share it. No implicit copy or new warning.
- `tuple-equals-scalar`: borrowed tuple retention is a sibling test, not a
  claim that owning tuple producers are complete.
- `same-construct-every-position`: common helper/write modes, callable and
  spelling probes; excluded positions remain in the matrix above.
- `conditional-operand-evaluates-in-place`: no write crosses its guard; RHS
  reads precede replacement; test skipped iterations/branches.
- `generic-equals-monomorphic-twin`: generic emission and MIR exclusions stay
  unchanged; concrete metadata must not alter generic form decisions.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: C++ stays
  byte-identical; no view, heap or additional temporary change.
- `runtime-template-kind-matrix`, `const-source-const-loop-var`: runtime and
  iterator producers are unchanged; retain readonly copy-source controls.
- Diagnostic rules: no new source errors, warnings or internal spellings;
  analysis exclusions are coverage gaps, not new language restrictions.

Update LANGUAGE_FEATURES and the completion checklist as each step lands.
TODO.md indexes this proposal and exclusions. No TPY_FOR_AGENTS or architecture
surface change is expected for this analysis/debug extension.

## Design review

Independent storage/activation review found no blocking design gap. It
confirmed the existing validation and retention mechanisms fit the bounded
contract. Independent CPython probes covered free functions, ordinary methods,
constructor tails, readonly sources, qualified and renamed copy spellings,
and retained-alias controls. The user approved the source move-through
boundary above before implementation.

M3.24 verification: the full MIR unit suite passes (1,235 tests), including
fresh copy/move activations, normal exits, retained-holder siblings and the
unchanged invalid-initialization gates.

## Delivery verification

The final code checkpoint is `181862a276`, reviewed cumulatively against
`9950ab55fa`. Architecture, safety, codegen, CPython parity, test coverage,
conventions and documentation reviews are clean. The review fixes strengthened
the dump assertion to require an actual copy instruction, corrected a stale
copy-route comment and made the retained-alias witness distinguish an accidental
copy from incorrect in-place replacement. All were included in the closing
review; only this verification record follows that checkpoint.

- Targeted source/MIR and new snippet verification: 28 passed, including
  generated C++ execution and CPython parity.
- Full `rpytest --force-exec`: 10,352 passed, 23 skipped in 345.18 seconds;
  all 4,181 executable cases built and ran, with no execution-cache skips.
- Existing snippets and snapshots are unchanged. Saved source probes and
  independent callable/boundary probes emit byte-identical C++ before and
  after the semantic-copy producer change.
- Master remains at the reviewed base; no merge conflict or unreviewed code
  tail exists. The added files are compiler tests, one snippet with its
  generated snapshots, and the implementation plan.

The readiness retrospective, including an independent second opinion triggered
by the 20-file changeset, accepts the existing activation/retention machinery,
bounded semantic-copy normalization and separate internal/source/runtime tests.
It accepts the source-move exclusion as this branch's boundary, not the
frontend defect itself: the subsequent local binding consolidation fixes that
producer. Reused-slot copy/move and the other matrix exclusions remain
open; this batch does not complete W1 or M3.
