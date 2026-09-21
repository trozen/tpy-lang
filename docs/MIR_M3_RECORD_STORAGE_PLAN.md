# Optional-backed record hoists: M3.14/M3.15

Status: implemented. M3.14 supplies the internal storage model;
M3.15 connects the THIR producers. The batch closes the
optional-storage record-hoist item of W1 in the
[M3 completion checklist](MIR_M3_COMPLETION_PLAN.md), within the admission
limits below; it does not complete W1 or M3. Base: `29fc5fa3fd`.

## Contract and evidence

For a plain `Cell` with an `int32` field and a field-initializing constructor:

```python
def example(flag: bool) -> int32:
    if flag:
        cell = Cell(1)
    else:
        return 0
    alias = cell
    cell.value = 7
    return alias.value
```

This already compiled before the batch. Its unchanged C++ has `std::optional<Cell> cell;`
before the `if`, `cell = Cell(1);` inside it, and `Cell& alias = (*cell);`
afterward. Source `cell` has type `Cell`, not `Optional[Cell]`. MIR now covers
the body using explicit backing and assignment facts.

Invariant: one hoisted source binding has one optional backing per activation
of its emitted enclosing scope; wrapper construction, contained-record
engagement, source availability and logical replacement are distinct facts.

The assignment is `std::optional::operator=`, not `emplace`. On an empty
wrapper it constructs the contained record. On an engaged wrapper it assigns
the existing record, preserving its physical lifetime. Neither behavior
allocates a new per-operation BODY OWN slot.

A single syntactic binding can execute repeatedly:

```python
def repeated(again: bool) -> int32:
    while True:
        cell = Cell(1)
        if again:
            again = False
            continue
        break
    alias = cell
    cell.value = 8
    return alias.value
```

The optional declaration is outside this loop. Conversely, a declaration
inside an outer loop's C++ block ends with that block and is constructed
again on its next activation. Source control-flow nesting alone cannot
decide backing lifetime.

Investigation on 2026-09-21 compiled and ran the branch, repeated-loop,
method and constructor-tail variants under TPy and CPython, with mutation
observed through an alias. Outputs were `7 0 8` and `9 0 10 0`. C++ inspection
confirmed optional assignment and reference aliases in the callable siblings.
`--dump-mir` confirmed the missing-hoist-fact boundary in all four bodies.
Temporary probes were under `/tmp/agents/m314_record_probe.py`; implementation
tests must own their source rather than depend on that file or snippet cases.

## Precedents and representation

Extend the [M3.12/M3.13 physical initialization model](MIR_M3_WRAPPER_STORAGE_PLAN.md)
and the existing record backing/borrowed-holder split. Preserve the
[M3.3/M3.4](MIR_M3_REUSE_PLAN.md) distinction between logical replacement and
physical lifetime, and the [M3.7/M3.8](MIR_M3_REGIONS_PLAN.md) distinction between
holder reset and physical storage end.

Approved representation:

1. Keep one private `RECORD_STORAGE` backing and one `BORROWED_RECORD` source
   holder, allocated at the hoist. An explicit backing representation marks
   this backing as optional rather than a directly constructed record.
   Existing record places still address the contained record; no source
   Optional type or optional-payload projection is fabricated.
2. Add an explicit empty-record-wrapper initialization instruction. Like
   `MIRStorageInit`, it has no source reads or source definition, but it
   constructs only the wrapper. Keep it a distinct typed case: the existing
   scalar default's alternative/value fields cannot describe record storage.
3. Add a distinct optional-backed record assignment mode to the existing
   record-write facts. Initially admit constructor RHS only. Evaluate its
   arguments at the reached write, update the backing, then bind the source
   holder using the existing borrow operation. Reuse the same backing on
   every execution of that binding's assignment site.
4. Track empty/engaged possibilities for these backings in the prepared
   forward-state analysis alongside selection, with a separate typed domain.
   Share its CFG worklist and region transitions; do not encode engagement as
   a source `is None` fact or build a competing CFG solver.
5. Distinguish wrapper-end and record-end events explicitly. A wrapper end
   identifies the backing container but is not itself the end of a borrowable
   record place. The record-end event uses the existing record root and is
   emitted only where engagement is possible. This avoids reporting an empty
   optional's destruction as destruction of a `Cell`.

A single source/storage slot would force aliases and name reads to accept a
different value category. A general owning-Optional payload would require
new aggregate layouts and projection semantics. Neither is necessary for
this bounded extension. The backing marker does not make wrapper objects
source-addressable or change what existing record-root places mean.

## State and consumer obligations

| Operation | Required state and effect |
| --- | --- |
| Empty wrapper initialization | Valid physical placement; constructs wrapper, leaves record disengaged and source holder unavailable. |
| First constructor assignment | Wrapper must be constructed; engages record and defines source holder; no old record to replace. |
| Assignment when possibly engaged | Same backing; may logically replace contents. Preserve record identity and retained dependencies; no physical payload-end event. |
| Alias or record read | Assigned source holder and engaged live referent are required; wrapper existence alone grants neither. |
| Scalar field mutation | Existing projected write semantics; no whole-record replacement or new engagement. |
| Scope exit | End constructed wrapper; end contained record only on possibly engaged paths. |
| New scope activation | Clear physical construction/engagement at actual storage end; reset source holders according to residence. Never revive an older retained alias by assigning a fresh source holder. |

Validation must check backing representation, destination identity, compatible
record layout, physical placement and the new write mode together. Missing
source assignment remains a definite-assignment proof failure, not a default
value. At joins, require construction/engagement on every path for reads,
while lifetime inventories retain possible construction/engagement. Preserve
existing unique-writer/initialization rules for other storage modes; do not
weaken them globally to admit this mode.

Liveness and shared instruction classification must treat empty initialization
as a physical action only. Dependencies continue to resolve borrowed holders
to the existing private record root. Storage inventory records the new write
mode. Retention checks skip definitely empty first writes and inspect possibly
engaged overwrites against other holders live afterward. The newly assigned
source holder is not itself a retained old-value conflict.

Scope inspection must consume record-end events, not wrapper-end events, for
record loans. Payload lifetime must not invent destruction at an engaged
assignment. Dumps must expose the distinction. The interpreter must preserve
the backing object's identity across engaged assignment, and allocate no
record at empty-wrapper initialization. Update all exhaustive statement
visitors and tests together.

## Positive THIR facts and admission

The shared `_optional_storage_hoist_entry` in `thir/lower/statements.py`
records a positive per-binding backing fact on the hoisted-binding metadata.
The optional-local assignment route attaches a positive constructor-assignment
fact to `THIRAssign`. Validation checks consistency with the selected binding
and layout. MIR must never infer these facts from
`std::optional<...>` strings, pointer membership or a source type alone.

Free functions, methods and constructor tails share the statement lowerer.
The hoist helper also serves for/try/with/match routes: describe the same
storage fact at the shared producer, while their unsupported CFG remains
uncovered. The separate error-return writer needs its own future fact and
cleanup support. Frame hoists remain distinct.

Initial admission is existing monomorphic, synchronous record coverage:
plain non-native records with eligible scalar fields and constructors,
constructor RHS, if/while hoists, normal edges and existing supported record
reads/aliases/field writes. Preserve existing constructor argument and
evaluation-order gates. Copy/move RHS, lifecycle hooks, exceptional effects
and aggregate ownership do not become covered through this route.

No parser, sema language rule, type-system mapping, C++ emission, runtime or
stdlib changes belong to this batch. Missing proof still yields MIR-not-covered;
structurally malformed IR still raises validation errors. Neither authorizes
a new compiler diagnostic or bypasses one.

## Factored scope matrix

The cross-product is intentional: new coverage requires a covered position,
the admitted plain-record shape and a local optional-backed destination.
Any excluded axis keeps that cell uncovered under the named backlog package.

| Position | Coverage or destination of remaining work |
| --- | --- |
| Free function, method, constructor tail | Covered by mutation and hoist-routing sections; receiver member initialization keeps its existing separate contract. |
| Module statement, closure/lambda execution | W2/W5 and M2/M4 identity/effect work. |
| Generator, async | W4 frame storage and W3 cleanup. |
| Comprehension, match arm, for loop | W2 CFG/binding work; producer metadata alone admits no body. |
| Context-manager body, try/finally, error-return body | W3 cleanup/exceptional flow. |
| Generic body | Existing M2 generic representation boundary; instantiated coverage must not be inferred from rendered C++. |

| Shape | Coverage or destination of remaining work |
| --- | --- |
| Plain scalar-field record | New local backing model; bool/int32 fields follow existing eligibility. |
| Scalar, scalar Optional/union | Existing M3.12/M3.13 controls, not new record-backed storage. |
| Borrowed tuple/Optional/union holding an admitted record | Existing holder propagation must retain the same referent; internal mutation/retention controls. Source eligibility remains unchanged. |
| Owning/mixed tuple, owning Optional/union, nested records | Remaining W1 and M2 layout work. |
| str/bytes, Ptr/Span, Box/Rc, containers, protocols | Not this plain-record backing route; W1/W5 and M2/M4 effects as applicable. |
| Own[T], copy/move sources | Existing OWN-site controls stay distinct; extension belongs to remaining W1 cyclic copy/move work. |
| readonly[T] | Existing borrowed/const controls; no optional-backed ownership conversion. Broader propagation remains W5. |

| Slot kind | Coverage or destination of remaining work |
| --- | --- |
| Local | New optional backing and source holder; existing alias locals. |
| Parameter | Existing scalar constructor operands and borrowed-record controls; no new wrapper parameter form. |
| Return | Existing scalar observations; reference escape remains W5/M4. |
| Field | Existing scalar field reads/writes and constructor initialization; new reference stores remain W5/M4. |
| Container element, global | W5 escapes and W2/M2 identities; no new admission. |

## Pitfalls and parity

- `silent-copy-vs-alias`: mutate after binding aliases, in both directions;
  examine emitted references. Retained aliases across replacement need conflict
  evidence, not a passing test that canonizes divergent output.
- `copy-warning-at-wrong-site`: no new copies or warnings; compare constructor
  assignment and pointer/OWN control routes with their existing output.
- `tuple-equals-scalar`: internal `(alias,)` and `(alias, scalar)` holders must
  retain the same backing; owning tuples remain explicitly W1 work.
- `same-construct-every-position`: free/method/constructor probes confirmed
  the shared producer; exclusions are recorded in the matrix and TODO index.
- `conditional-operand-evaluates-in-place`: hoist only empty storage;
  constructor operands stay at reached assignments. Pin skipped writes and
  existing supported guard writes, without widening eager operand admission.
- `generic-equals-monomorphic-twin`: no generic admission; assert its existing
  boundary rather than deriving storage from C++ spelling.
- `view-not-copy`, `hidden-allocation`: no emission change or new owned
  string/view representation; optional backing stays inline.
- `runtime-template-kind-matrix`: no runtime template changes.
- `const-source-const-loop-var`: no iteration binding change; readonly/pointer
  hoists remain separate positive-fact controls.
- `generated-cpp-readability`: existing C++ must remain byte-identical.
- `no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics`: no new source
  diagnostics; MIR dump vocabulary remains an explicitly requested debug view.
- `reject-valid-python-only-as-documented-divergence`,
  `no-warning-on-valid-code`: uncovered analysis never rejects or warns on
  source programs; test source diagnostics remain unchanged.

An independent CPython-parity design assessment found no new divergence from
this analysis-only extension. The runtime mutation probes match CPython.
That does not establish parity for aliases retained across replacement:
Python gives a new `Cell` to each constructor binding, whereas engaged optional
assignment can overwrite the object an old alias still observes. MIR must
expose possible logical conflicts while faithfully representing physical
storage. It must not bless that difference as a new language rule.

Evaluation order, exception behavior, numeric/string behavior, truthiness and
syntax acceptance are unchanged. Exceptional cleanup remains uncovered. Known
adjacent issues are `BUGS.md#loop-if-hoist-alias-early-del` and
`BUGS.md#loop-body-storage-outlived-by-loan`; their different backing/lifecycle
shapes must not be folded into this extension. No new defect was established
by the probes, and neither known defect is claimed fixed.

## Delivery and gates

1. **M3.14: record-wrapper storage model.** Add representation, physical
   initialization and write facts, validation/engagement, all consumers,
   dumps and interpreter support. Internal IR tests cover first engagement,
   repeated assignment, empty exits, mixed joins, missing/duplicate facts,
   reads before assignment, same-activation backedges, new activations,
   logical conflicts and actual record ends. Keep THIR admission closed.
2. **M3.15: THIR producer integration.** Add the positive facts at shared
   hoist/write producers and connect bounded if/while lowering. Unit-owned
   source tests check emitted C++ agreement, mutation identity, free/method/
   constructor symmetry, early return/break/continue, missing/damaged facts,
   unsupported siblings and distinct OWN-site controls. Verify the CLI dump
   for the newly covered bodies.

Use one stacked branch and finish each implementation step as one commit.
Fold the approved design checkpoint into M3.14 when preparing those commits.

Tests should exercise MIR directly or through unit-owned THIR sources; no
compiler test may reach into `tests/cases`. Existing snippets remain C++/CPython
controls. Add one condensed snippet only if corpus review finds the specific
alias/hoist behavior lacks a focused runtime pin; do not duplicate incidental
coverage blindly. Corpus review found focused hoist coverage in
`control_flow/hoist_nonvalue_branch`, `hoist_nonvalue_inner_scope` and
`hoist_nonvalue_read_after_loop`, including mutation and nocopy guards; no
additional snippet was needed. Existing snapshots should not change. Consult before any
unexpected existing snapshot update.

Delivery gates: targeted tests per step, independent multi-agent defect review,
master semantic-merge audit as needed, one full forced suite after the final
implementation changes, then readiness/retrospective and commit preparation.
Leave the branch for the user to merge.

Confidence: high in the producer semantics and backing/holder model, supported
by code inspection, sibling surveys and runtime probes. The main implementation
risk is conflating wrapper and record ends in an existing consumer; M3.14's
internal state/activation tests are the gate before opening THIR coverage.
