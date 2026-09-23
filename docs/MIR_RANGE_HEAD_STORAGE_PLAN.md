# Range-head temporary storage: investigation and dependency plan

Status: investigation complete; revised implementation scope awaits approval.
No compiler behavior has changed. This follows the implemented
[ordinary-for body storage extension](MIR_FOR_ARGUMENT_STORAGE_PLAN.md).

## Observed boundary

```python
def total(n: int32) -> int32:
    result = 0
    for i in range(read(Cell(n))):
        result += i
    return result
```

With a hook-free `Cell` containing an int32 field and a readonly scalar
reader, this source rejects before MIR at `call.arg_shape.record_f1`.
The same rejection occurs in start, stop, descending and dynamic-step heads,
conditional bounds, ordinary methods, constructor tails and a generic caller.
The stable-argument spelling `range(read(cell))` compiles. CPython executes
the temporary-bearing source, including zero-trip and conditional examples.

The initial proposal to preserve existing C++ while adding MIR coverage was
therefore incomplete: admitting this example requires source lowering first.

Three independent boundaries matter:

- `_lower_range_arg` in `thir/lower/statements.py` lowers start, stop and step
  with temporary creation disabled.
- `_emit_for_range` renders start and stop before capturing their values,
  without a head-temporary flush. Enabling lowering alone is unsafe.
- The shared temporary planner rejects named head backing; MIR range coverage
  independently restricts bound expressions and does not admit these calls.

## Placement design

Reuse `THIRTempPlan`, identity maps, `TempQueue`, emission-event verification
and the existing MIR range builder. No new lifetime kind is needed.

Invariant: capture each range operand exactly once, in start/stop/step order,
before starting the loop; temporary backing follows its emitted containing
block, independently of the counter and per-iteration body regions.

Generalize declaration anchors from statements to THIR nodes so each root
operand can be an anchor. Render, flush and capture one operand before
rendering the next. Lazy payload initialization stays at the selected-arm
anchor. Nested-loop head backing resets with its containing outer activation;
top-level head backing follows the function block. Restore the surrounding
anchor before entering the loop body or else.

MIR must allocate expression-anchored storage separately from initialization:
today both `declare_temporaries(anchor)` and `expr(anchor)` initialize that
anchor, so calling them unchanged in sequence would initialize it twice.
Apply the existing full-expression evaluation-order proof to admitted bounds.

## Source-lowering prerequisites

Per-operand placement solves ordering between bounds, but not arbitrary
ordering inside a bound. For example, hoisting the constructor argument of
`touch() + read(Cell(observe()))` before the entire expression can move
`observe()` ahead of `touch()`. This is a design hazard for the proposed
extension, not a newly reproduced execution defect: the temporary-bearing
range source currently rejects.

Observable destruction is a separate boundary. CPython can release an
unretained anonymous argument after the reader returns; block-backed C++
storage survives longer. The bounded hook-free MIR subset cannot justify
source admission for custom destructors or transitively observable cleanup.
The existing documented timing exceptions for named locals do not by
themselves settle this anonymous-argument case.

Do not import MIR's reader-only checks into source lowering as a new
position-specific purity classifier. Before widening production admission,
establish shared sequencing and cleanup facts, or explicitly approve a
narrow source subset and its remaining limitations. General call-effect
summaries are a prerequisite for that broader analysis, not a complete
solution to expression scheduling or cleanup by themselves.

## Delivery after those prerequisites

1. **Source lowering and THIR placement.** Extend the shared ordinary range
   operand path and per-operand emission anchors. Cover start, stop and step
   coherently, including conditional operands; preserve existing literal,
   numeric, overflow/nonzero, target-hoist and position rules. Existing
   programs should retain their generated C++; new source admission requires
   explicit sequencing and cleanup coverage.
2. **MIR integration.** Initially admit int32 unit-step ranges with the
   existing verified hook-free record arguments and finalized readonly
   scalar-reader summaries. Reuse counter/body/else CFG and storage analysis.
   Missing placement, unknown effects or unsupported shapes remain uncovered.

Each step is one reviewed commit. Native iterable heads remain separate:
their current MIR facts require a fixed borrowed container name, and current
call summaries cannot express container-return ownership or dependencies.

## Scope and verification

| Axis | Initial MIR consumer | Separate prerequisite or work |
| --- | --- | --- |
| Position | ordinary function, method, initialized constructor tail; supported nested blocks | module/closure/comprehension/match W2/W5; cleanup W3; generator/async W4 |
| Head | int32 start/stop, implicit +1 or literal -1 | dynamic/non-unit steps and wider numeric shapes retain existing MIR gaps |
| Argument | hook-free scalar-field record, stable scalar constructor operands, readonly scalar reader | tuple/Optional/union, str/bytes, Own, Ptr/Span, Box/Rc, containers and custom hooks: W1/M2/M4/W5 |
| Storage | operand backing in containing block; captured scalar bounds | returns, fields, container stores, globals, captures and frame-held backing: W3-W5/M4 |
| Evaluation | eager and conditional operands with verified order | general effects and exceptional cleanup require shared facts |
| Spelling | existing resolved free-reader identities and aliases | unresolved/generic/callback/native/protocol dispatch remains M2/M4 |

Tests must distinguish declaration from payload construction, once-only head
evaluation from body re-entry, skipped lazy branches, zero trips, normal
exhaustion, break/continue/return and nested outer-loop activation. Pin actual
MIR coverage and retained-alias scope ends. Use `@nocopy` or observed mutation
at reference boundaries; compare planned and unplanned C++ and generic twins
where source lowering changes. Effects used to expose ordering belong in the
source tests even when those bodies deliberately remain outside MIR coverage.

Pitfalls: preserve reference identity and copy diagnostics; keep scalar,
singleton/mixed tuple and wrapper exclusions explicit; audit all shared source
positions; check lazy and cross-operand sequencing with observable effects.
No view copies, hidden allocations, runtime-template changes or new MIR
source diagnostics are intended. Existing snapshot changes require review
and approval. Document actual admission in LANGUAGE_FEATURES and the M3
checklist; do not mark W1/W2 complete on this slice.

## Recommended next decision

Advance the general M4 call-effect design before implementing this extension.
The [call-effect plan](MIR_CALL_EFFECTS_PLAN.md) records the proposed first batch.
It is already required by M3 cleanup and escape propagation and avoids making
a local range-head workaround the authority for source effects. Revisit the
two-step delivery above when sequencing and cleanup boundaries are explicit.
