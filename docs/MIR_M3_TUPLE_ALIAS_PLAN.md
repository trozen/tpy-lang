# Immutable local tuple aliases: M3.18

Status: implemented; review and merge status are separate.
Base: `08c918e9f4` (M3.16/M3.17 merged).

## Contract and scope decision

Extend MIR inspection to existing, non-reassigned whole aliases of a directly
initialized local constructor tuple:

```python
pair = (Cell(1), 7)
saved = pair
chain = saved
pair[0].value = 9
print(chain[0].value)  # 9
```

The existing C++ owns `std::tuple<Cell, int32_t>` in `pair` and emits
`auto&& saved = pair; auto&& chain = saved;`. These aliases neither copy the
records nor create backing. The inline record identity remains `(pair, 0)`.
The implementation supplies analysis facts and MIR coverage for that emission;
it does not change source acceptance, diagnostics or generated C++.

CPython parity: match for the admitted slice. Alias binding preserves payload
identity and mutation visibility, repeats no evaluation, and introduces no
lifetime extension or new divergence. Canonical backing identity does not add
MIR support for whole-tuple `is`, equality or truthiness operations.

Classification: architectural. The invariant is that every admitted immutable
whole-tuple alias names the same already-initialized backing, with the same
access capability and lifetime, and no independent construction or end event.

Do not combine this with owning tuple rebinding. Scoping probes confirmed that
`pair = (Cell(1), 7); pair = (Cell(2), 8)` emits an owning tuple into a pointer
tuple binding. This is the existing BUGS.md entry headed "A pointer-repr tuple
LOCAL whose literal RHS value-captures an owned element emits ill-formed C++ at
both the decl and the reassign." Saved-alias variants reach the same bad emit.
Correcting that path needs a backing-storage design and `/tpy-fix-bug` analysis;
MIR must not treat the current C++ as proof of correct source behavior.

## Evidence and precedents

- `thir/lower/statements.py`'s storage-tuple alias arm uses
  `is_storage_tuple_alias_decl` from `codegen_cpp/forms.py` and selects
  `LocalBinding.STORAGE_TUPLE_ALIAS`. The emitter spells `auto&&`.
- That producer requires `fn_top = not scope.in_branch`: source alias
  declarations in branches/loops are not admitted. Top-level aliases can be
  read and mutated inside already-supported branches/loops.
- M3.17 supplies positive owned tuple layout and scope placement at the
  constructor-literal declaration. Borrow tuple copies already use
  `MIRTupleCopy`; copying owning tuples remains intentionally invalid.
- The existing coverage and builder `scoped` methods save/restore binding
  environments. Normalization must preserve their lexical boundaries.
- A scratch probe with alias chains, singleton/multiple records, scalar
  members, bidirectional mutations, and `@nocopy` records produced
  `24 16 9 15` under both TPy and CPython across a free function, ordinary
  instance method and constructor tail. Its branch uses an outer alias.
- Direct element binding (`saved = pair[0]`), alias reseating, and branch-local
  whole alias declarations currently reject during THIR lowering. Unpack has
  a separate working producer; this proposal does not add unpack coverage.

## Representation

1. Attach a semantic tuple-storage-alias fact at the existing name-source
   producer, carrying source binding and exact layout/access. Propagate it
   through chains whose root has the M3.17 constructor-local facts. Keep this
   separate from owned initialization: an alias has no storage placement of its
   own. Neither `auto&&`, tuple type alone nor renderer membership authorizes
   MIR coverage. Fields/subscripts accepted by the wider producer gain no
   coverage from this addition.
2. Validate the fact against the bare THIR name initializer, type, layout,
   access and existing producer selection. Coverage must establish a prior
   unconditional direct initialization, even for an alias never used later.
   Reject missing/contradictory facts, forward references, branch-only or
   out-of-scope roots, and changes to the root or any alias binding. Inspect
   actual THIR operations rather than trusting prescan facts alone.
3. Normalize a proven immutable alias to the canonical owner's MIR slot.
   Resolve chains when the alias is declared, not through a later mutable name
   lookup. This is a new, bounded normalization rule: no `MIRTupleCopy`, new
   owning slot, initialization, destruction or lifetime extension is emitted.
   Indexed operations then use the existing `(root, tuple index)` places,
   liveness, dependencies and scope-end analysis.
4. Preserve exact access capabilities initially. Do not canonicalize a
   readonly view onto a mutable root unless validation retains its restriction;
   this batch can fail closed on differing capabilities. Keep alias provenance
   scoped with coverage/builder state. The initial supported roots and alias
   declarations are unconditional body-level locals.

The MIR dump will show operations on the canonical backing; this proposal does
not promise a separate slot or instruction for every Python alias name. An
explicit aggregate borrow operation should be considered when reseatable,
selected or escaping holders enter scope, not simulated by this normalization.

No parser, sema rule, type-system, runtime or stdlib behavior change is planned.
Implementation touches THIR facts/validation and its shared statement producer,
MIR coverage/lowering, tests and status documentation.

## Factored scope matrix

Every axis must permit a cell. Exclusions are coverage gaps, not new language
restrictions; the [M3 completion checklist](MIR_M3_COMPLETION_PLAN.md) retains
the remaining W1-W5/M2/M4 work.

| Axis | Coverage | Excluded / tracked |
| --- | --- | --- |
| Position | Unconditional local declarations in free functions, ordinary methods and constructor tails; uses inside existing if/while regions | Branch/loop alias declarations and hoists: existing THIR boundary/W1. For, comprehension, match and module execution: W2. Cleanup/with/try/finally/error-return: W3. Generator/async: W4. Closure execution: W5/M4. |
| Shape | M3.17 flat tuples: bool/int32 and concrete plain scalar-field owned records, singleton/multiple records | Nested/selected aggregates, Optional/union owned payloads: W1/M2. str/bytes, views, Ptr/Span, Box/Rc, containers and generic execution: existing M2/M4/W5 boundaries. Scalar and borrowed tuple behavior remains an unchanged control. |
| Binding | Bare-name aliases and chains, fixed root and alias bindings, exact matching access | Rebind/reseat/move/delete, hoists, element binding and unpack: remaining W1/M2. Differing readonly access fails closed. |
| Slot | Already initialized body-local backing and aliases | Params, returns, fields, container elements, globals and captures: existing M2/W2/W5/M4 boundaries. |
| Ownership | Fresh constructor-owned source tuple | Borrow tuple copies retain existing semantics; source mixed ownership/call results, Optional wrappers and consuming boundaries remain separate. |

## Tests and pitfalls

- Unit-owned source fixtures assert positive facts, alias chains, interpreter
  mutation, canonical places and one backing/end inventory. No compiler test
  reads from `tests/cases`. Compare generated C++ with the current producer.
- Malformed THIR guards cover unused forward aliases, mismatched sources,
  layouts/access, missing facts, branch-only roots, out-of-scope names,
  reassigned roots/aliases, move/delete and whole-root replacement through all
  write/consume spellings. Erasing alias declarations must not erase a failed
  definition/initialization obligation.
- The condensed runtime case `tuple/constructor_owned_alias` supplies a focused
  pin beyond the existing field-alias, borrowed-member and escape cases:
  free/method/constructor sections, singleton/multiple records, mutation through
  both source and alias, independent member indices, scalar reads and `@nocopy`.
  Branch outcomes and repeated uses of an outer alias exercise CFG consumers;
  they do not imply nested alias declaration coverage.
- `silent-copy-vs-alias`, `tuple-equals-scalar`: require observed shared
  mutation and deleted-copy controls; do not mistake owning tuple copying for
  an alias. `copy-warning-at-wrong-site` and `no-warning-on-valid-code`: no new
  copy or warning is expected.
- `same-construct-every-position`: use the shared producer; the matrix names
  exclusions. `conditional-operand-evaluates-in-place`: aliases only bind an
  existing name; no constructor or payload evaluation may be repeated/moved.
- `generic-equals-monomorphic-twin`: generic admission stays closed.
  `view-not-copy` and `hidden-allocation`: no new storage or conversion.
  `runtime-template-kind-matrix` and `const-source-const-loop-var`: no runtime
  template or iteration producer changes; exact access is preserved.
- `generated-cpp-readability`: unchanged existing emission.
  `no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics` and
  `reject-valid-python-only-as-documented-divergence`: failures remain debug
  MIR coverage reasons, with no new source diagnostic or acceptance rule.

Update LANGUAGE_FEATURES, IR_DESIGN, MIR_ANALYSIS_PLAN and the completion
checklist with the exact admitted slice when implemented. No existing snapshot
changes are expected; consult before any unexpected refresh. New snapshots, if
needed, include C++ execution and CPython parity in the same generation pass.

## Adjacent defects and follow-on decision

The broader tuple feature is not assumed correct:

- The constructor-literal rebind emit mismatch above blocks that producer.
- `BUGS.md#resumable-alias-identity` records frame backing overwritten under
  surviving aliases; `BUGS.md#frame-tuple-reassigned-owning-alias` records
  remaining frame owning/borrow mixing and whole-alias gaps.
- `BUGS.md#readonly-auto-tuple-copy-fact` and
  `BUGS.md#optional-tuple-unpack-readonly-fact` block relying on those sibling
  metadata paths without their own checks.
- `BUGS.md#global-tuple-ref-storage-form` and
  `BUGS.md#consume-own-element-of-mixed-tuple` remain outside this local
  slice.
- Synchronous owning-call tuple rebinds reuse optional backing in the emitter.
  Before proposing their admission, probe retained aliases across replacement;
  do not infer safety from the working owning-call-to-borrow transition.

Confidence: high for the bounded immutable-alias normalization, supported by
producer inspection, mutation/nocopy execution and independent sibling/parity
reviews. Rebinding remains a separate design decision and is not implementation
work authorized by approval of M3.18.

Delivery: one M3.18 implementation commit on a temporary branch, folding this
plan into it. Run targeted tests, cumulative multi-agent review,
readiness/retrospective gates and one full forced suite after final changes.
Leave the squashed branch unmerged and unpushed. This increment neither closes
W1 nor completes M3; do not invent a second increment just to fill a batch.
