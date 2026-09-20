# M3.7/M3.8: emitted storage regions and retained references

Status: approved 2026-09-20. M3.7 is implemented; M3.8 is next.
M3.1-M3.6 are merged. This continuation is architectural, analysis-only work;
it does not change source acceptance, diagnostics, C++ emission or provenance
authority. Work will stay on `mir-m3-regions-batch`, with one final commit for
M3.7 and one for M3.8, reviewed together before the user's merge.

## Concrete contract

Assume `Cell` is an ordinary record with an `int32` field `value` and a
constructor that assigns it:

```python
def retained_if(flag: bool) -> int32:
    saved = Cell(0)
    if flag:
        local = Cell(1)
        saved = local
    return saved.value
```

Today this emits the equivalent of:

```cpp
Cell initial(0);
Cell* saved = &initial;
if (flag) {
    Cell local(1);
    saved = &local;
}
return saved->value;
```

M3.7 will inventory the end of `local` on the branch exit. M3.8 will report
an internal possible conflict with `saved`, which is read afterward. This is
a real existing compiler defect, already recorded in `BUGS.md` under
"A reference-type local hoisted across an if/else, match, or try/except branch
that aliases a BRANCH-LOCAL source dangles" (the entry has no slug).
The example is for compilation/analysis only; do not execute its dangling C++.

CPython returns 1 for true and 0 for false: an `if` is not a Python binding
scope. Recording the actual C++ lifetime exposes the defect; it neither
endorses that lifetime as the language rule nor repairs the defect. A later
storage/admission change needs its own approved design. Copying every escaping
reference would break shared mutation and is not an implied remedy.

A safe control creates `local` and `alias = local` inside the branch, mutates
`local.value`, reads the new value through `alias` into an outer scalar, and
returns that scalar. Its storage still ends, but no reference is used afterward.
Reading a local field to compute a scalar `return` likewise happens before
the scope ends and is safe within this analysis's coverage.

## Findings and prior art

- Plain record declarations, constructor-backed Optional record declarations,
  and inline scalar Optional/union declarations can emit storage directly
  inside `if` arms and `while` bodies. These have real interior storage ends.
- Effective OWN replacement backing is function-hoisted even if its write is
  nested. A loop-local record captured by an outer alias can also become
  function-hoisted backing. Neither ends at the loop-body boundary.
- `THIRIf.hoist_decls`/`hoist_slots` and `THIRWhile.hoist_decls` precede their
  control statement. They remain uncovered until structured facts describe
  their placement; do not parse their C++ strings.
- A `while`-`else` body has its own emitted braces after the loop. A `break`
  skips it. Its loop frame has already been popped, so control transfers
  inside an inner loop's `else` target the enclosing loop.
- Flattened `elif` chains do not introduce an enclosing synthetic `else`
  storage scope. Effectful condition temporary placement remains excluded.
- MIR currently permits ordinary declarations only in the body-entry prefix,
  assumes BODY duration for owned records/unions, and rejects ordinary
  construction in CFG cycles. Merely lifting the declaration gate is unsound.

Producer precedents are the storage/form decisions in
`tpyc/thir/lower/statements.py`; their emission is in `tpyc/thir/emit.py`
(`_emit_stmt`, `_own_slot`, `_emit_if`, `_emit_while`, `_pop_loop_frame`).
Analysis precedents are M3.3's positive storage-write facts, M3.5's physical
payload-end inventory, and M3.6's strict-validation/internal-inspection split.
Extend shared validation, liveness, presence and dependency propagation rather
than introducing another alias engine.

## M3.7: region facts and normal storage ends

Implemented in `tpyc/mir/region_flow.py` and `scope_lifetime.py`, with producer
facts and boundary transfers in the existing lowering/dataflow passes.
BODY remains the unique body-storage spelling; nested storage uses a region
ID. The end inventory tracks possible initialization so an unexecuted OWN
site does not acquire a spurious record end at function return.

Invariant: every inventoried end names positively identified emitted storage
on a particular CFG exit, after evaluation and before the successor executes;
holder lifetime and pointee lifetime remain separate.

1. Add positive typed THIR placement facts at the existing declaration/backing
   decisions: current emitted region or callable body. Unknown placement stays
   unknown. Associate the fact with owned backing, not just the source name.
   Verify producer/emitter agreement for each admitted arm; no reconstruction
   from C++ names, `cpp_type`, indentation or source block depth.
2. Add body-scoped MIR region identities, parent relationships and entry
   identity. Blocks identify their active region; storage roots identify their
   owner region. Keep CALLER storage external. BODY/region ownership must have
   one unambiguous representation, not two independently maintained facts.
   Give every scoped local holder and activation-local MIR temporary a binding
   residence for availability resets. This is logical availability, not a
   claim that MIR temporaries allocate corresponding emitted storage.
3. Split CFG blocks at region boundaries. Derive exited/entered regions from
   the source and destination region ancestry. Identify edges by source and
   successor arm, not just destination (true/false can share a destination).
   Return has an explicit body-exit edge. Do not also store a second independent
   list of exited regions on each terminator.
4. Admit declarations only in a prefix of each covered scope, with the same
   type/form/initializer gates as the existing body prefix. The prefix must
   finish before any user transfer can exit that scope. Preserve lexical
   binding maps for all sibling shapes, including tuple/Optional/union maps.
   Synthetic `THIRNarrowAlias` bindings may precede declarations in that
   prefix; they do not turn a narrowed arm into a non-prefix declaration site.
5. Give scoped initialization an explicit once-per-region-activation contract.
   Validate region entries, declaration dominance and that every path back to
   an initialization leaves and reenters its owner region. Initializers may
   themselves lower to several CFG blocks; prefix does not mean first block.
   Direct uses require an active binding residence. Reset local binding
   availability at activation boundaries: a previous iteration cannot justify
   a read before this iteration's declaration. Keep the existing cycle guards
   for body-lifetime construction, copy/move and in-place replacement. A fresh
   loop local is not an OWN_SITE write into reused body-lifetime backing.
6. Inventory normal ends in a dedicated analysis result. Include record roots
   and scalar-wrapper storage, with possible engaged/selected inline payloads
   determined by the existing presence solver. A pointer wrapper's end never
   means its referenced record ends. Do not erase provenance of ended roots.
   Expose the solver's existing branch-refined outgoing edge facts and
   feasibility; terminator-entry facts alone cannot describe each exit edge.
7. Apply region boundary effects to the existing presence/freshness analysis.
   Ended scalar-wrapper payload aliases become stale; reentry/reconstruction
   cannot revive them. Forget region-local selection/condition facts when
   leaving an activation. Preserve strict public validation; the internal
   inspector may examine freshness failures as M3.6 does, but never selection,
   structural, typing or definite-assignment failures.
8. Expose region ownership and normal-end inventory in `--dump-mir`, preserving
   explicit uncovered results. This is debug analysis, not a compilation gate.

| Transfer | Storage regions ending |
|---|---|
| Arm fallthrough | Arm and any exited nested scopes |
| Loop-body fallthrough / `continue` | Current iteration and exited inner scopes |
| `break` | Current iteration and exited inner scopes; bypass loop `else` |
| Normal loop completion | No unentered iteration; execute `else` if present |
| `else` fallthrough | Its own emitted scope |
| Scalar/void return | All active local scopes, then body storage |

Nested regions end inside-out. This inventory does not model destructor calls,
their effects, exception unwinding or a complete destruction sequence.
All return-value evaluation precedes its exit event.

## M3.8: references retained across an end

Pair each end with incoming dependency facts at its source terminator and
holder liveness on that particular successor edge. Use destination `live_in`,
not the union over both successors; return has no reference continuation in
the admitted scalar-return subset. Reuse existing root/field overlap rules.
Ending a private record also ends its inline descendants; ending a reference
holder does not end external pointees. Keep infeasible edges distinct from
unknown selection facts.

Report the edge, ending storage place, and retaining holder leaf. Include
scalar holders, flat tuples, Optional/reference-union payloads and scalar
payload aliases already modeled by dependency propagation. Reseating an outer
holder before the boundary can remove its old dependency; a dead holder alone
does not produce a read-retention conflict.

A static loop storage ID represents repeated activations. Check retention at
each exit: same-address reconstruction must not hide a conflict at the previous
end. Preserve outer-holder referents across exit/reentry, even when their
storage has ended. Joins remain conservative; no generation-sensitive safety
proof, disjointness proof or source diagnostic follows from an empty result.
Read liveness also does not settle destruction-time uses or keep-alive/drop
timing. Those effects remain outside coverage, even when a holder is read-dead.

## Factored scope matrix

A cell is covered only when every applicable axis below is covered and the
existing form/operation gate admits it. Other cells remain explicit gaps in
`TODO.md`; this batch does not complete M2 or M3.

| Axis | Covered | Gap / reason |
|---|---|---|
| Position | Already admitted ordinary synchronous free functions, methods and constructors; structured `if`/`elif`/`else`, `while`/`else`; normal transfers | Module statements, closures/captures, generators, async, comprehensions, context managers, try/finally, error-return, match, for-loops: placement or cleanup needs separate modeling; existing generic-body exclusions stay |
| Owned storage | Plain records and already supported constructor-backed Optional records; inline bool/int32 Optional/union storage | Owned tuples, mixed/owning unions, recursive storage, containers, str/bytes, views, Ptr/Span, Box/Rc and expression temporaries: no positive placement/effect coverage |
| Holders | Existing plain and readonly record borrows; flat tuple, Optional and union borrowed payloads; scalar payload aliases | New aggregate layouts, whole-readonly auto tuple facts, arbitrary mutable aliasing: existing gaps remain |
| Slot | Parameter referents remain CALLER; local declarations in each scope prefix; existing inline field projections; existing OWN backing stays BODY | General hoists, declarations after control flow, frame slots, global/container/capture sinks, borrowed returns, arbitrary field stores: no lifetime extension/admission implied |
| Operation | Existing bounded initialization, aliasing, scalar writes and control flow; fresh constructor-backed loop activation | Cyclic copy/move/in-place, calls and callee effects, destructor execution, exceptions, suspension: retain explicit exclusions |

No new Own or readonly language rule is introduced. They retain their existing
storage and access meanings. Existing M3.3 OWN writes remain supported; missing
facts for escape-hoisted initial declarations produce uncovered, not invented
loop-local storage. `BUGS.md#union-inline-slot-block-scoped-reseat` is a related
pointer-union backing defect outside this producer slice.

## Tests, reviews and readiness

- Source-to-THIR-to-MIR unit probes assert positive placement facts and actual
  emitted backing placement: nested direct storage versus body-hoisted OWN.
  Cover free functions, ordinary methods and eligible constructor tails,
  including constructor-backed Optional records and narrowed-branch prefixes.
  Inspect unsafe examples without building/running their generated C++.
- Structural negatives cover missing/wrong region ownership, illegal entries,
  inconsistent edge identities, read before activation initialization and
  cycles that revisit initialization without leaving its region.
- Inventory tests cover nested scopes, skipped branches/zero iterations,
  both branch arms, normal/break/continue/return, while-else and nested-loop
  control from an else body. Pin return evaluation before ends.
- Retention tests pair unsafe/safe/dead/reseated holders for records, borrowed
  tuple/Optional/union leaves, scalar payload aliases and inline child fields.
  Add loop-carried old aliases and stale-alias reconstruction negatives. Use
  internal MIR for states the frontend rejects; no compiler test opens files
  from the snippet corpus.
- Use an independent bounded trace oracle with per-activation storage tokens
  for small CFGs to catch loop identity/reset mistakes. Production analysis
  remains finite and static.
- Add a condensed runtime case only for source behavior not already pinned:
  safe nested scopes must mutate a shared record and observe it through its
  alias, with matching CPython output. Give each subject a descriptive comment.
  Do not duplicate ordinary runtime cases merely to claim MIR coverage.
  M3.7 adds unit-owned source/emission/analysis probes; it adds no runtime
  case because source behavior is unchanged and existing alias/rebind cases
  already pin the shared-mutation behavior. The final suite still executes
  those cases; no unsafe generated probe is run.
- No existing C++/diagnostic snapshots are expected to change. Seek approval
  for verified unexpected changes before refreshing them. Run targeted checks
  during implementation and one full forced-exec suite for the finished batch.
- Review region/CFG invariants, lifetime and alias correctness, CPython parity,
  tests, conventions and docs, then complete readiness/retrospective gates.
  Squash each step separately; leave the final two commits on the branch.

Implementation touches THIR metadata/producers and their emitter agreement
checks, MIR lowering/nodes/validation, existing dataflow boundary transfers,
the new inventory/inspection consumers and debug collection/dumps. Parser,
language type rules, runtime and stdlib behavior stay unchanged. Update this
plan, `MIR_ANALYSIS_PLAN.md`, `LANGUAGE_FEATURES.md`, `IR_DESIGN.md` status and
`TODO.md` as each step lands. New analysis runs only when requested, as today.

## Pitfalls and risk assessment

| Pitfall | Contract |
|---|---|
| silent-copy-vs-alias | No new copies; runtime controls observe mutation across alias boundaries |
| copy-warning-at-wrong-site | No warning/copy-policy change |
| tuple-equals-scalar | Existing aggregate holder leaves participate; owned tuples explicitly excluded |
| same-construct-every-position | Factored matrix and uncovered tests; no inference from free-function coverage |
| conditional-operand-evaluates-in-place | Edge events follow reached evaluation; no condition/temp relocation |
| generic-equals-monomorphic-twin | Generic-body gates unchanged; no new instantiation semantics |
| view-not-copy | View storage and conversion remain outside the new slice |
| hidden-allocation | No emitted/runtime change or allocation |
| const-source-const-loop-var | Existing readonly facts survive; no mutable upgrade at an alias boundary |
| generated-cpp-readability | Byte-identical source emission expected; debug identities have stable ordering |
| no-cpp-in-diagnostics | No new source diagnostics |
| no-internal-names-in-diagnostics | Internal IDs remain in the explicitly requested debug dump |
| reject-valid-python-only-as-documented-divergence | No source rejection; emitted scope is not Python scope |
| no-warning-on-valid-code | Possible-conflict inventories never become source warnings in this batch |

Confidence: medium until implementation validates region-entry/reset behavior
against nested loops and the independent trace oracle. The main risks are
incorrect producer placement, retaining initialization across activations,
reviving stale scalar aliases, and claiming safety from read liveness alone.
The design addresses these with positive facts, validated region transitions,
existing freshness checks and explicit non-authority. Missing placement or
effects stay uncovered rather than being approximated as safe.

Dedicated parity review found no new divergence in this analysis-only feature.
The branch-local dangling source example remains the already tracked silent
divergence; it is an inspection regression, not accepted language behavior.
