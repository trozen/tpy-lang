# Named argument storage in ordinary for-loops

Status: THIR placement and emission checks implemented; MIR mapping follows.
Extends [named argument placement](MIR_NAMED_ARGUMENT_STORAGE_PLAN.md) and
the existing [ordinary for-loop CFG](MIR_M3_ORDINARY_FOR_PLAN.md).
This advances W1/W2; it does not complete either package.

## Contract

```python
def last(n: int32) -> int32:
    result = 0
    for i in range(n):
        result = read(Cell(i))
    return result
```

For a hook-free `Cell` with bool/int32 fields and a known readonly,
normal-returning scalar reader, MIR can analyze argument backing in the
loop body and its `else`. The source already compiles. Existing C++ declares
`Cell temp = Cell(i);` inside the body on every successful iteration.
Lazy arguments instead have optional backing in that block and initialize
only on the selected operand. Source acceptance, C++ output, diagnostics and
production checker authority stay unchanged.

Invariant: named argument backing follows its actual emitted declaration
block and initialization anchor; each loop body activation is fresh, while
counter, captured-bound and iterator/source residences remain unchanged.

## Existing mechanisms and scope mapping

Extend `THIRTempPlan`, its identity indexes, `TempQueue` scheduling, and
emission-event verification. Reuse the existing MIR range/native builders,
region activation, record engagement and scope-end analyses. Do not add
parallel for-loop builders or infer placement from generated C++ names.

| Loop | Scope tree for planned storage | Existing surrounding residence |
| --- | --- | --- |
| Range | containing -> counter -> loop body; else is a child of containing | bound captures in containing; induction in counter |
| Native container | containing -> loop body; else is a child of containing | captured source and iterator in containing |

The range counter scope exists in C++'s `for` statement even without an
explicit surrounding brace. Emission enters that semantic scope without
printing extra braces. Existing body-written and hoisted target bindings
keep their current residence, independently of the induction variable.
Both consumers restore the containing scope before entering `else`.

Normal fallthrough and `continue` end the body activation before advance.
Exhaustion, including zero iterations, reaches `else`; `break` skips it.
Return ends all active regions. Both emit and MIR pop the inner loop before
its `else`, so a break/continue there still targets the enclosing loop.

The planner traverses supported range bounds/native sources solely to verify
that it understands their temporary producers. A named argument temporary in
any head operand makes the whole body unplanned; it is never given a body
scope as a workaround. Unknown statements or nondefault metadata outside the
supported shape also leave the body unplanned. Existing MIR source, binding,
unit-step and summary checks remain authoritative for analysis coverage.

## Scope matrix

All axes must admit a cell. Exclusions remain in the
[M3 completion checklist](MIR_M3_COMPLETION_PLAN.md).

| Axis | Covered | Remaining work |
| --- | --- | --- |
| Position | ordinary free function, method and constructor tail; nested supported if/while/for and loop else | module/comprehension/match: W2; context manager/try/finally/error-return: W3; generator/async: W4; closure: W5 |
| Loop | existing int32 +1/-1 ranges; fixed borrowed list/Array int32 or record elements, set[int32], dict[int32,int32] keys | non-unit/dynamic steps and richer head expressions: M2/W3/M4; protocol, consuming, owned/rvalue sources: W1/W2/M4 |
| Argument | existing pure bool/int32-field record constructors; stable scalar local/parameter/literal operands; readonly borrow | tuple/Optional/union payloads, str/bytes, Own transfer, Ptr/Span, Box/Rc, containers and hooks: W1/M2/M4/W5 |
| Element access | existing scalar values and mutable/readonly record aliases | tuple/Optional/union/nested elements and unpack: M2/W2; no tuple-language extension |
| Slot/use | local backing, known readonly scalar-result parameter, existing scalar local/field RHS/return/condition consumers | reference returns, field/container/global stores and captures: W5/M4 |
| Evaluation | eager/lazy body and else expressions; nested supported blocks | named temps in range start/stop/step or native iterable; unknown producers anywhere in the body |
| Spelling | existing direct/imported aliases with verified identities | module-qualified constructors, open generic bodies and other unresolved routes retain existing boundaries |

Native consuming/frame-source/tuple-lift/pointer-hoist/string-literal
metadata must not accidentally gain placement support. The planner's
structural acceptance does not widen MIR's type or effect contract.

## Delivery and verification

1. Extend THIR preparation and emission scope checks, preserving C++ bytes.
   Cover both loop kinds, both body/else positions, and invalid/foreign scope
   evidence before connecting the MIR consumer.
2. Map those identities to the existing MIR regions. Cover actual source
   lowering, repeated activation, retained-alias scope ends and excluded
   heads/metadata. Each step finishes as one commit on a stacked branch.

Embedded source fixtures belong to compiler tests; do not load snippet files.
Check zero/one/multiple iterations, ascending/descending ranges, written and
hoisted targets, captured bounds, nested mixed loops, lazy branches, early
return and inner-else transfers. Compare C++ with preparation enabled/disabled.
Use structural MIR assertions for declaration versus initialization, fresh
body activation and end events; pure readers make those differences mostly
invisible to output tests. Pin actual MIR coverage, not just successful C++.

Keep `@nocopy` records at argument and iteration boundaries and check borrowed
parameter spelling; use mutation-observing source controls where already
available. Internal retained-holder tests include scalar, singleton/mixed
tuple, Optional and union holders without admitting new argument shapes.
Reuse existing ordinary-loop and temporary snapshots; add a source case only
for an uncovered observable obligation. No existing snapshot changes expected.

## Pitfalls and risk

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`: nocopy/reference
  evidence and unchanged diagnostics preserve argument/element aliasing.
- `tuple-equals-scalar`: retained-holder shape controls share the same scope
  end; tuple source-language completeness remains separate.
- `same-construct-every-position`: free/method/tail share the plan and scope
  mapping; unsupported positions remain explicit in the matrix.
- `conditional-operand-evaluates-in-place`: pin lazy initialization anchors,
  skipped payload construction and re-entry; output parity alone is not proof.
- `generic-equals-monomorphic-twin`: generic source emission remains unchanged;
  open generic MIR stays excluded, with generic/concrete emission controls.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: compare
  emitted bytes; no view/ownership or allocation policy changes.
- `runtime-template-kind-matrix`: no runtime template changes; native source
  families retain their existing tests and positive iteration facts.
- `const-source-const-loop-var`: preserve readonly element facts and bindings,
  without substituting owning copies for borrowed elements.
- Diagnostic pitfalls (`no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics`,
  `reject-valid-python-only-as-documented-divergence`, `no-warning-on-valid-code`):
  no new source diagnostic; unsupported plans only limit MIR coverage.

The main risk is confusing the range counter's lifetime with the body's, or
leaving else under the loop scope. Shared parent checks, malformed-plan tests
and transfer-edge assertions guard both. Preparation still has bounded linear
body walks, with no new interprocedural scan. The CPython-parity design
assessment found no new divergence in this slice; exceptional cleanup and
observable hooks remain outside its proof.
