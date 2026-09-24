# Borrowed results backed by argument temporaries

Status: MIR returned-temporary propagation, shared storage facts and internal
[storage-origin evidence (steps 1-2)](MIR_STORAGE_ORIGIN_DESIGN.md) are implemented.
Eager borrowed-call local declarations compile; borrowed-call ternaries and
pointer declarations/reseats that materialize arguments reject as not yet
supported, pending the step 3 coverage/compatibility decision and production gate.

## Contract

For a hook-free `Cell` with an int32 field:

```python
def observe(cell: Cell) -> readonly[Cell]:
    return cell

saved = observe(Cell(value))
print(saved.value)
```

The argument is built once, into named backing declared before the statement;
`saved` aliases it without copying or extending its lifetime:

```cpp
Cell __tmp_1 = Cell(value);
const Cell& saved = observe(__tmp_1);
```

MIR substitutes the backing as the returned holder's origin. A result
dependency contains only the actual roots the callee summary selects, so the
unused temporary of `choose(flag, owner, Cell(7))` does not become a root of
the returned `owner` alias. Loop bodies declare fresh backing per iteration.
The plain `Cell` parameter is intentional. Its inferred readonly access still
uses the named argument path. An explicitly `readonly[Cell]` parameter binds
the constructor inline with full-expression lifetime, a separate
representation MIR does not relabel as block storage.
Safe bindings can still emit the separately tracked warning
`BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling`.

## Forms that stay closed

A ternary of borrow calls whose arms materialize arguments, and a
temporary-bearing `PTR_ADDR` declaration or reseat, reject as not yet
supported (`calls/error_borrowed_call_ternary_argument`). Admitting them needs
a proof that no holder outlives the backing. Holder scope alone is
insufficient. Here the optional backing dies at the end of the `if` block
while `outer` still refers to it:

```python
outer = observe(owner)
if flag:
    saved = observe(Cell(1)) if choice else observe(Cell(2))
    outer = saved
print(outer.value)
```

Direct copies, borrowed-call forwarding and alias chains all retain the
backing's storage origin. This shares the underlying limitation tracked by
`BUGS.md#call-source-escape-through-alias-unchecked`; the existing
`THIRSlotEmplace` select slot has the same escaping-alias defect, tracked as
[select-slot-alias-escape-unchecked](../BUGS.md#select-slot-alias-escape-unchecked).

## Shared THIR contract

`PTR_ADDR` declarations and reseats render their value, drain pending
declarations, then bind; the validator's flushable-rebind kinds and the
temporary planner recognize the same capability. Lowering grants no
temporaries there today, so emission is unchanged.
That flush/validation contract establishes well-formed temporary declarations
for an already-built THIR body; it does not authorize a source route or prove
that a borrowed holder stays within the backing's lifetime.

Lowered functions, constructors and module-init bodies publish
`THIRStorageFacts` independently of whether their `THIRTempPlan` exists.
Argument backing links to its plan placement or carries
an uncovered reason; select slots are explicit uncovered siblings; inline
full-expression constructors are bounded by their full expression. Borrowed
local and `PTR_ADDR` sinks record obligations over the backings in their
value. No holder names, C++ text or source offsets identify storage.

## MIR consumer

Borrowed-expression lowering initializes planned anchors and shares the
scalar path's enclosing argument-order proof before consuming named actuals.
Calls substitute selected actual holders through the existing dependency
transfer; no new solver or storage representation is introduced. Callers
containing local backing remain opaque as callees. The lazy ternary route is
exercised on a hand-built THIR body because no admitted source reaches it.

`certify_thir_storage(MIRStorageRequest(...))` binds the facts and placement
plan to the ordinary builder's actual storage roots and composes the internal
lifetime evidence. Its three verdicts are Certified, Conflict and Not covered.
The request captures the exact THIR function/constructor, definitions and
summary inputs; a copied body or a different request cannot reuse evidence.
Select-slot and inline full-expression producers stay explicit coverage gaps.
This API changes neither source acceptance nor generated C++.
Ordinary MIR coverage only establishes a complete representable body; it is
not a lifetime certificate. Workspace analysis and `--dump-mir` retain that
distinction. Callers with local backing still publish opaque summaries.

## Scope matrix

Axes are factored; a combination is covered only when every relevant axis is.

| Axis | Covered slice | Explicit boundary |
|---|---|---|
| Source sinks | Existing single-assignment borrowed-call local declaration | Lazy borrow-call ternary arms, temporary-bearing pointer declarations/reseats: storage-origin step 3 |
| MIR callers | Ordinary free functions, methods, initialized constructor tails; supported if/while/for bodies | Module/comprehension/match: M3 W2; closure: W5; try/finally, with, error-return: W3; generator/async: W4 |
| MIR callee | Known ordinary nongeneric synchronous free function | Method/static/constructor/native/protocol/callback/generic callees and recursion: M4 |
| MIR actual | Named temporary containing a hook-free bool/int32-field record, passed readonly; stable scalar constructor operands | Inline full-expression arguments, mutable argument access, custom hooks, arbitrary nested effects: W1/M4/W3 |
| MIR result | Existing whole borrowed record and readonly result, parameter-derived | Tuple/singleton, Optional/union, str/bytes views, Own, Ptr/Span, Box/Rc, nested records and containers: M2/M4/W5 |
| Holder | Local result and existing call forwarding | Field/container/global/capture stores and returned local backing are not certified escape channels: W5 |

## Verification and pitfalls

- `silent-copy-vs-alias`: the source case uses `@nocopy` and mutates the
  owner after a borrowed call returning it.
- `same-construct-every-position`: the source case covers function, method,
  constructor, while and range positions of the admitted declaration.
- `conditional-operand-evaluates-in-place`: the lazy MIR witness checks one
  construction per selected arm and uninitialized backing on the other edge.
- `view-not-copy`, `hidden-allocation`: no new C++ is emitted.
- Diagnostic pitfalls: the closed forms keep their existing located reject.

Scope-conflict tests must not execute dangling reads. Function-exit scope
inspection treats successor liveness as empty; the evidence API separately
resolves returned origins and reports a local backing return as a conflict.
`BUGS.md#call-source-escape-through-alias-unchecked` and
`BUGS.md#borrow-call-container-temp-dangling-warning` remain open.
