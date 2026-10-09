# Local binding and storage lowering

The source-lowering contract and its implemented checkpoints.

## Contract and diagnosis

```python
for i in range(3):
    source = Cell(i)
    target = source
    target.value = 9
```

An eligible assignment uses the same source operation in function, loop
and branch bodies. A first binding in an arm that is used after the branch
needs a hoisted destination, while a copy initializer retains explicit-copy
semantics at either destination. Both operations use existing sema facts.

The shared contract is:

    semantic binding and consume eligibility
    + actual source/destination forms and lifetimes
    -> operation, storage write and resulting capabilities

Move eligibility is permission, not an instruction to emit a physical move.
Generator/async frames can keep an alias into their own live source storage;
that name must not become movable merely because sema marked it owned.
Preserve CPython-visible aliasing, evaluation order and lifetime. No new
language restriction, implicit copy fallback, allocation or warning policy.

## Scope boundaries

The supported family includes reference-record and container moves at
scoped and hoisted destinations, and explicit copies into escape-hoisted
record storage. Live sources, third aliases and live closure captures
retain their shared object. Frame-held aliases retain the frame's storage
and suspension lifetime. Tuple completeness remains a separate workstream.

## Existing decisions to reuse

- Sema's move-through and last-use/borrow facts decide consume eligibility.
- The binding table's classifier (`classify_binding`, `tpyc/thir/lower/
  bindings.py`), `HoistFlavor` and the shared hoist helpers describe ordinary
  and hoisted local storage. Extend them coherently.
- `_rebind_slot_target`, `_rebind_rvalue_source_ok` and
  `_lower_storage_value` share stored-payload and source decisions.
- `RebindStorage.OWN/IN_PLACE` remains the per-site replacement authority.
- `FrameLocalKind` and frame write/alias helpers retain frame layout
  authority. Globals retain their static-storage adapter.
- Existing `THIRMove`, `THIRCopy`, declaration, assignment and frame-write
  nodes represent the operations. `storage.py` records semantic facts for
  MIR; its narrower type coverage must not become a frontend admission gate.
- Record declared type, source access, constness and actual movability
  together with the selected binding. Keep pointer aliases non-movable (the
  record's `movable`); do not derive ownership from C++ spelling.

## Factored scope and test matrix

Rows compose by applicability. Controls preserve existing behavior; they do
not claim new support for all type/position combinations.

| Axis | Core coverage | Controls / separate contract |
| --- | --- | --- |
| Callable | Free function, method, staticmethod, constructor tail | Closures keep capture facts; member initialization keeps its own storage contract |
| Control flow | if, while, range/native-for, with, try/finally, match, error-return | Zero/multiple iterations, break/continue/return, exception cleanup and RHS-before-write |
| Resumable | Existing frame adapters | Generator/async aliasing, constness and suspension lifetimes; no new frame planner |
| Other positions | Existing expression/global adapters | Comprehension guard placement and target scope; static global storage |
| Types | Reference records, list/dict/set/bytearray, Array | Scalar/value-record twins; no reference-storage handling for ordinary values |
| Wrappers | Existing supported forms | Optional/union pointer-versus-storage, readonly, Own, singleton/mixed tuples; incomplete tuple producers stay separate |
| Other shapes | Existing supported forms | str/bytes views, Ptr/Span, Box/Rc, generic/concrete twins and protocols; no qualifier stripping to force admission |
| Destination | Fresh local, hoisted local, rebind backing | Frame/global/capture homes retain existing selection |
| Downstream use | Mutation or nocopy guards after binding | Param, return, field and container writes keep owning-slot ABI/warning policy |

One condensed runtime case should name its position sections and annotate
the subject lines. Reference tests must observe shared mutation or use
`@nocopy`; read-only output cannot detect accidental copies. Add internal
tests for actual THIR operations/facts and source-to-MIR behavior, without
compiler unit tests reaching into snippet sources.

## Pitfalls and gates

- No silent copies or misplaced copy warnings. Live-source, live-third-alias
  and live-closure controls must retain mutation visibility.
- Singleton/mixed tuples preserve element capabilities; no whole-wrapper
  move inferred by stripping Optional, union or readonly metadata.
- Every position consumes the same source decision; equivalent direct,
  imported and module-qualified copy spellings need matching controls.
- Operands evaluate once where written, below their guards and before a
  replacement. Destructor/exception order is part of the test contract.
- Generic/concrete twins preserve form and established warning policy.
- Views stay views; no hidden string/container copy or heap-backed slot.
- No runtime template change planned; any such change needs its own kind
  matrix. Frame alias pointers retain source constness.
- Generated code remains readable; no new C++/internal names in diagnostics,
  no new valid-source rejection or spurious warning.

Behavior-preserving changes require byte-identical existing snapshots;
source-acceptance improvements replace the corresponding rejection pin.
Inspect generated code before refreshing snapshots. Run targeted tests
while developing, the full suite at completion, and specialist review and
readiness checks before delivery.

The design preserves accepted behavior and extends rejects-valid cases.
A proposal to alter accepted behavior requires the CPython-parity design
review. Primary risks are wrong movable promotion, lost constness, short-lived
backing, and changed cleanup/evaluation order. Admission gates must retain
their source-form and storage constraints as coverage grows.

## Implemented checkpoints

1. Source operations and owned-local registration share existing THIR nodes.
   The storage-value helper consumes an explicit source-read policy, including
   the indirect payload read for moves. Initializer/registration order, copy
   result types, comprehension scope and admission gates remain unchanged.
   The plain copy-declaration arm passes the declared result type; the
   storage-value adapter keeps the payload type at slot declarations and
   writes. Both use the same copy-construct lowering.
2. Scoped and hoisted moves, container siblings and escape-hoisted copies
   use the shared operations. Consume eligibility stays in its declaring
   function rather than leaking into a same-named nested local. The condensed
   runtime case covers control flow, callable positions, non-copyable
   containers, live aliases and captures, suspension, onward ownership
   transfer, explicit-copy spellings and escaping field references.
3. Production THIR-to-MIR tests cover while, range and native loop moves:
   scoped owner facts, INITIALIZE_REGION, zero/repeated activation,
   continue/break/return, interpreter values and debug collection. This
   checkpoint left hoisted moves outside MIR; M3.26/M3.27 subsequently cover
   them under existing storage modes. The local binding consolidation itself
   changed no MIR admission, solver or validation rule.

## Remaining boundaries

Match's payload-family admission and storage-flavor classification answer
different questions: captures have narrower writers than ordinary
assignments, and the admitted family still needs an owning or borrowing
destination. Reassigned owners and capture/borrow hoists retain their
strategy and constness restrictions (`BUGS.md#match-nonvalue-hoist-unadmitted`).
The two-arm parameter-alias witness has no sema borrow-declaration entry;
it therefore does not enter match's borrow-pointer route. A record field
capture sharing a name with an ordinary binding still rejects at its
capture-write gate. These boundaries do not warrant dropping the gates.

The broader local-slot redesign remains in TODO.md. M3.26/M3.27 cover bounded
hoisted/reused-slot record copy/move analysis; broader move sources and owning
aggregates remain MIR storage gaps. Tuple completeness is separate.
