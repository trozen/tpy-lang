# Full-expression record temporaries: M3.22/M3.23

Status: M3.22 and M3.23 implemented, reviewed and verified; ready for delivery.
Base: `d0dedbee52`. This batch covers part of W1's expression-storage gap in
[the M3 completion checklist](MIR_M3_COMPLETION_PLAN.md). It does not complete
W1 or M3. Tuple-language completeness remains a separate release workstream.

## Contract

With an ordinary scalar-field `Cell` and a pure constructor, this already
compiles to inline C++ constructor temporaries:

```python
def read(value: int32, flag: bool) -> int32:
    return Cell(value).value if flag else Cell(0).value
```

MIR must construct only the selected temporary, retain its storage through
the entire enclosing full expression, and preserve the scalar result after
that storage ends. A while condition creates a fresh activation on each
evaluation, including the final false evaluation. A conditional operand is
not a separate destruction boundary.

This is analysis/debug coverage only: source acceptance, diagnostics, C++
emission and checker authority remain unchanged. CPython may release the
record earlier than C++, after the field read; this is unobservable for the
admitted records, which have no lifecycle hooks or reference-bearing fields.

## Precedent and invariant

Reuse `MIRDefinitions`' verified plain constructors, `MIRConstruct`, existing
storage regions, `INITIALIZE_REGION`, activation validation, liveness,
dependencies and scope-end/retention inspection. A full-expression region
is nested in the actual surrounding storage region. Its scalar result lives
in the parent; its record backing lives in the child. No parallel lifetime
solver or new call-summary mechanism is needed.

THIR supplies positive storage/field identity facts at the actual inline
materialization sites. A constructor used to initialize a named record is
not automatically such a site. Missing facts mean MIR not covered, never a
new frontend rejection. General named `THIRArgTemp` declarations and deferred
optional argument storage have different lifetimes and remain excluded.
Likewise, a constructor-bearing `THIRValueSelect` left operand with
`lhs_temp_cpp` is uncovered: its hoisted declaration introduces a separate
full expression and can extend a subobject's lifetime. Inline bool operators
and lazy right operands do not acquire that declaration boundary.

## Commit boundaries

### M3.22: internal full-expression storage and lowering contract

Add the positive THIR metadata contract and consume it at the internal
THIR-to-MIR boundary. Enclose complete admitted scalar expressions (or
discarded constructors) in a region only when they actually contain such
materialization. Preserve lazy evaluation and parent-resident scalar results.
Use explicit region edges before the next statement, before selecting an
if/while successor, and before return. Validate constructor eligibility and
placement; keep malformed or unsupported materializations uncovered.

Internal tests assert exact lifetime boundaries, skipped initialization,
fresh condition activations, multiple temporaries in one expression, scalar
result survival, and retained references after an end. Retention probes may
use scalar, tuple, Optional and union holders internally; they do not admit
reference escapes from temporary source expressions.

### M3.23: bounded source producers and delivery

Attach metadata at existing inline constructor field reads and discarded
constructor statements; extend the shared direct-field identity producer.
Cover bool/int32 reads in scalar local initialization/reassignment, scalar
field RHS writes, scalar returns, if/while conditions, and their supported
lazy boolean/conditional expressions. Use the same producer in free
functions, ordinary methods and constructor tails. Preserve the existing
pure-constructor-argument and eager-expression-order gates.

Source tests assert MIR structure and execution, plus exclusion boundaries.
Reuse existing source snapshots where they focus on this behavior; add at
most one condensed CPython-compatible case if coverage needs it. No existing
snapshot change is expected. Review the cumulative batch, run the final full
forced suite, and finish with one commit per step on
`mir-m3-expression-temporaries`. The user merges.

## Factored scope matrix

Every axis must admit a cell; metadata alone never opens another axis.

| Axis | Covered | Remaining work |
| --- | --- | --- |
| Position | Free functions, ordinary methods, constructor tails; supported if/while and loop bodies | Module/closure/comprehension/match: W2/W5. Exceptional/context-manager/error-return: W3. Generators/async: W4. Generic execution: M2/M4. Constructor member initializers are not body full expressions. |
| Shape | Concrete hook-free record with bool/int32 fields, existing pure scalar constructor operands, scalar results | Nested/selected records, owning/mixed aggregates, tuple temporaries: W1/M2. Optional/union record construction, str/bytes, Own, Ptr/Span, Box/Rc, containers: W1/W5/M2/M4. Existing wrappers remain usable outside the new producer slice. |
| Slot | Inline constructor receiver/discard, scalar local/return/field RHS/condition | Reference field stores, container elements, globals, captures and reference returns: W5. Argument slots: M4. Temporary-bearing range bounds need their separate capture-boundary audit. |
| Lifetime | Normal full-expression completion, lazy paths, repeated activation | Partial construction, throwing calls and custom destruction: W3/M4. Frames: W4. Lifetime extension and banked argument temporaries: W1. |

Cyclic copy/move and in-place replacement remain a separate W1 task. No
owning-operation cycle gate is relaxed by this batch.

Imported bare constructor names use the same fact producer when their verified
definitions are supplied. Module-qualified constructor syntax currently lowers
through `THIRCall`, not `THIRCtorCall`; that route stays uncovered with the
general-call boundary. This is an analysis gap, not a source rejection.

## Pitfalls and verification

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`, `tuple-equals-scalar`:
  no ownership decision or emitted copy changes; source results are scalars.
  Internal retained-holder tests cover existing tuple/wrapper siblings.
- `same-construct-every-position`: common metadata producer; function,
  method and constructor-tail witnesses; explicit excluded positions above.
- `conditional-operand-evaluates-in-place`: constructor remains on its lazy
  path, lifetime ends at the whole expression, not the arm; observable walrus
  controls and structural MIR assertions distinguish the two questions.
- `generic-equals-monomorphic-twin`: generic MIR remains uncovered; optional
  metadata must not affect generic C++ emission or source acceptance.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: inspect
  emitted C++ and retain existing snapshots byte-for-byte.
- `runtime-template-kind-matrix`, `const-source-const-loop-var`: no runtime
  template or iteration binding change; those producers are not broadened.
- Diagnostic pitfalls: no new source diagnostic, warning, internal spelling
  or valid-Python rejection. Unsupported MIR is an analysis coverage result.

Design probes matched CPython for scalar reads/writes, conditions, discarded
construction and lazy operands. Structural lifetime tests are essential:
hook-free source programs cannot observe destruction timing themselves.

## Review and verification

All seven review lenses are clean: architecture, safety, code generation,
CPython parity, test coverage, conventions and documentation. Review closed
two coverage gaps: global walrus assignments now obey the same exclusion as
ordinary global assignments, and a hoisted select LHS cannot inherit inline
temporary lifetime. Their supported controls and exclusions are pinned.

The final code checkpoint is `5ee84f912f`; subsequent changes only record
documentation and verification. The 48 focused tests pass. Final
`rpytest --force-exec`: 10,280 passed, 23 skipped; 4,180 C++ cases built and
ran, with zero execution-cache skips. Existing snapshots are unchanged.

Independent readiness review accepted reuse of regions/activation, positive
producer facts, parent-resident scalar results and the explicit no-call/no-hook
boundary. Internal retention tests establish analysis behavior, not source
escape admission. The remaining W1-W5 work stays in the completion checklist.
