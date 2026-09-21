# Inline record tuple storage: M3.16/M3.17

Status: implemented. M3.16 supplies the internal storage model;
M3.17 connects the bounded THIR producers.
Base: `ec7a8ebbf4`. This batch addresses part of W1's owning/mixed aggregate
backing item in the [M3 completion checklist](MIR_M3_COMPLETION_PLAN.md).
It does not close that item or complete M3.

## Contract and precedent

Given the existing plain scalar-field record `Cell`, this already compiles:

```python
def example() -> int32:
    pair = (Cell(1), 7)
    pair[0].value = 9
    return pair[0].value
```

Its C++ owns `std::tuple<Cell, int32_t>{Cell(1), 7}` and reads/writes the
inline member through `std::get<0>(pair)`. MIR now covers the tuple declaration
and makes the body inspectable through
`--dump-mir`, without changing source acceptance, diagnostics or generated C++.

Invariant: an inline record element has the identity `(tuple backing, index)`
and the lifetime of that backing; a borrowed element retains its original
referent and does not acquire the tuple's lifetime.

This is architectural work, including the M2 representation prerequisite for
M3's analysis. Extend existing tuple layouts/projections, storage regions,
dependency resolution and normal scope-end inspection. Do not represent inline
members as unrelated record roots or treat an owning tuple as a pointer tuple.

Verified precedents:

- `thir/lower/statements.py`'s `decl.storage_record_tuple` route already selects
  all-VALUE record captures, registers storage-form reads and emits inline C++.
  It now supplies owning tuple layout and placement metadata.
- `thir/lower/storage.py:tuple_layout` requires explicit capture and ownership
  input for an owned payload. Ownership comes from a positive producer fact, not
  from the source tuple type, `auto`, or other rendered C++.
- MIR tuple-index places and separate storage/holder identities also describe
  owned tuple elements. Layout validation, dependency resolution and scope ends
  consume the distinction together.
- M3.9/M3.10 supply direct-declaration placement and activation checks. Reuse
  those checks; tuple hoists are a different storage contract.

## M3.16: internal aggregate storage model

Extend flat tuple payload descriptions to distinguish scalar values, borrowed
records and inline owned records. Use existing record layouts and eligibility
checks for owned members: concrete plain records with bool/int32 fields and
the already-admitted constructors, without custom lifecycle effects.

Provide explicit initialization of one tuple backing. Scalar and borrowed
members consume existing evaluated operands; an owned member consumes its
constructor field operands and initializes the destination tuple/index place.
Do not manufacture standalone record backing and then imply a copy into the
tuple. The operation completes initialization atomically in this bounded,
non-throwing MIR model. Actual exceptional/partial construction is still W3;
constructors with effects remain uncovered. This abstraction also omits
unobservable intermediate constructor temporaries for these hook-free records.

The tuple gets an explicit emitted storage region and one initialization per
activation. Skipped declarations create no end event; a loop-body declaration
can run again only after the previous activation ends. No default construction,
hoisting or repeated assignment to a still-live owning tuple is implied.

Extend the existing tuple construction representation with typed owned-member
initializers and a positive aggregate-initialization fact. Keep borrowed tuple
construction/copy behavior intact. Reject owning tuple copy/move and replacement
until their operations have separate positive facts; merely accepting the new
layout must not enable existing `MIRTupleCopy` to copy owned records.

Update validation, operand/read enumeration, definite initialization,
presence/freshness, region activation, dependencies, storage inventory,
retention, scope ends, dumps and interpreter together. A projected inline
record can be borrowed using its tuple/index
place; scalar field mutation through that alias must remain observable through
the tuple. Const access can be preserved or weakened to readonly, never made
mutable from a readonly source.

At normal scope exit, ending the tuple backing overlaps its inline members.
Reuse root/prefix overlap for retained-alias inspection. Copies of borrowed
members into other holders keep their external dependencies after the tuple
ends; do not report that the external referent died with its holder. Record
initialization inside a tuple must be visible to storage inspection, rather
than disappearing because it is nested in an aggregate operation. One MIR
point can initialize multiple owned member places: inventory all of them,
without classifying initialization as replacement or retaining only the last
member in a map keyed by program point. Re-entering the tuple's region must
not revive aliases retained from an earlier activation.

Internal coverage includes flat mixed owned/borrowed tuples to establish the
invariant across both element kinds. This does not admit mixed source producers.
Pin saved aliases in existing scalar, tuple, Optional and union holders,
including joins, disjoint member indices, scope exits and repeated activations.

## M3.17: bounded THIR producer integration

Attach positive per-element ownership/layout and local placement facts at the
existing all-VALUE constructor-tuple producer. Extend the shared tuple-layout
helper with explicit ownership input; callers lacking that input must not
start inferring owned elements from types. Validate the declaration, literal,
constructor eligibility and layout together.

Admit flat local literals containing bool/int32 and fresh eligible record
constructors, including singleton tuples and multiple owned records. Keep
constructor operands and scalar elements under the existing pure-operand
gates. Their evaluation stays at the reached declaration and in source order.
Allow existing indexed scalar reads and record field reads/writes. No owning
tuple copy, whole-tuple alias, unpack, rebind, parameter, return or call-result
route is added incidentally.

Use the shared statement producer in free functions, ordinary instance methods
and constructor tails, including direct declarations in supported if/while
regions. A tuple whose actual emission requires a hoist remains uncovered.
MIR lowering uses only typed THIR facts and existing constructor summaries.

One source boundary was verified explicitly: `alias = pair[0]` on this owned
tuple currently reaches THIR's disclosed `decl.slot_type` lowering rejection.
Do not extend the frontend as part of this batch. Internal MIR tests exercise
the corresponding alias and lifetime operations; source tests use existing
indexed mutation and `@nocopy` records to detect unintended copies.

## Factored scope matrix

Coverage requires every axis to permit the cell. Unlisted combinations gain
no coverage from metadata alone; the remaining axes stay filed in W1-W5 and M2/M4.

| Axis | Covered | Remaining work |
| --- | --- | --- |
| Position | Free function, ordinary instance method, constructor tail; direct local declaration in existing if/while regions | Module and closure execution: W2/W5. For/comprehension/match: W2. Context manager, try/finally and error-return: W3. Generator/async: W4. Generic execution: existing M2/M4 boundary. |
| Element shape | bool/int32; concrete scalar-field record; singleton and flat multiple-element tuples | Nested tuples/records, owned Optional/union and broader scalars: W1/M2. str/bytes, Ptr/Span, Box/Rc, views and containers: W1/W5/M2/M4 as applicable. |
| Ownership | Constructor-owned record members; existing scalar/borrowed tuple controls; mixed owned/borrowed layouts and readonly access internally | Source mixed-own producers and Own boundaries: W1/M2/M4. Copy/move, tuple aliases and rebind/hoists: remaining W1. |
| Slot | Local tuple backing and temporary borrowed holders; existing scalar parameter operands, scalar returns and scalar field mutations | Owned tuple parameters/returns, reference fields, container elements, globals and captures: W2/W5/M2/M4. |
| Lifetime | Fully initialized direct backing and normal region exits, including skipped declarations and repeated scope activations | Conditional/full-expression temporary backing: W1. Partial initialization, custom destruction and exceptional exits: W3. Escaping/frame storage: W4/W5. |

## Why not combine the rest of W1?

Mixed owning call-result tuples already emit hybrids such as
`std::tuple<Cell, Cell*>`. Their call/return dependency contracts need M4; a
layout extension alone cannot cover the body. Owned Optional/union members
also need selection and payload-end semantics, beyond flat tuple initialization.

Expression temporaries have different physical lifetimes. `THIRArgTemp` can
flush a named local before the statement and live to the enclosing scope end;
conditional banking uses optional storage; `THIRTupleValueToBorrow` creates
inline backing that lasts only through the full expression. They need positive
placement/write facts and covered consumers. A common statement-end lifetime
would be incorrect. Keep this as a separate batch.

Cyclic copy/move/in-place extension is independently useful, but does not
supply missing aggregate identities. It remains W1 work after this batch;
do not relax the current owning-operation cycle gates globally.

## Tests, pitfalls and delivery

- Internal tests cover layout validation, atomic initialization, missing facts,
  placement, uninitialized reads, readonly access, mixed borrowed/owned members,
  alias mutation, loans retained through all existing holders, joins, normal
  exits and activation resets. A tuple copy must not silently accept ownership.
- Unit-owned source tests pin positive THIR facts, MIR lowering/interpreter
  behavior and dump coverage, plus explicit unsupported siblings. No compiler
  test reads from `tests/cases`. Test malformed metadata independently of source
  coverage failures.
- Compare generated C++ and diagnostics to the existing behavior. Existing
  tuple cases focus on moves, unpacking, fields or frames. The new condensed
  `tuple/constructor_owned_local` runtime case pins fresh constructor tuples,
  indexed mutation and nocopy in the admitted synchronous positions. No existing
  snapshot changes are expected; consult before any unexpected refresh.
- `silent-copy-vs-alias`, `tuple-equals-scalar`: source nocopy controls and internal
  alias mutation distinguish identity from copying; test singleton/mixed forms.
- `copy-warning-at-wrong-site`, `no-warning-on-valid-code`: no new copies or
  source warnings; existing explicit-copy semantics remain outside admission.
- `same-construct-every-position`: use the shared producer and pin free/method/
  constructor routing; the matrix names excluded positions.
- `conditional-operand-evaluates-in-place`: no tuple or constructor operand is
  evaluated before its guard. Pin skipped and late declarations.
- `generic-equals-monomorphic-twin`: keep generic coverage explicit; never infer
  concrete layout from the renderer's type spelling.
- `view-not-copy`, `hidden-allocation`: inline storage stays inline; no owning
  view/string conversion or new runtime allocation belongs to this work.
- `runtime-template-kind-matrix`, `const-source-const-loop-var`: no runtime
  template or iteration binding changes. Preserve readonly tuple controls.
- `generated-cpp-readability`: unchanged C++ output is a required check.
- `no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics`,
  `reject-valid-python-only-as-documented-divergence`: debug coverage failures
  stay separate from source diagnostics and cannot reject a valid program.

CPython parity: the source contract preserves fresh-record identity
and mutation. Independent singleton and multi-record `@nocopy` probes produce
the same output under TPy and CPython and compile with deleted C++ copy
constructors. No source behavior, exception, numerical, truthiness or syntax
change is proposed. General exception/cleanup analysis remains uncovered.
Known global tuple copying and mixed-own parameter issues remain separate
(`BUGS.md#global-tuple-ref-storage-form`,
`BUGS.md#consume-own-element-of-mixed-tuple`); neither is claimed fixed.

Confidence: high in the bounded design and producer contract, based on direct
THIR/C++ probes and the sibling survey. Implementation must update the full
consumer set; incomplete layout support is the principal risk. Mixed source
tuples and general temporaries are deliberately separate prerequisites.

Implement on one stacked branch. Finish M3.16 and M3.17 as
one commit each, folding this proposal into M3.16. Run targeted tests per step,
the multi-agent cumulative defect review and readiness/retrospective gates,
and one full forced suite after final code changes. Check master for relevant
movement before preparation. Leave the reviewed two-commit branch unmerged
and unpushed for the user.
