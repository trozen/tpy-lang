# M3.9/M3.10: late declarations and bounded ordinary hoists

Status: approved 2026-09-20. M3.9 and M3.10 are implemented.
M3.1-M3.8 are merged.
This is an architectural, analysis-only continuation of
[MIR_M3_REGIONS_PLAN.md](MIR_M3_REGIONS_PLAN.md). It changes neither source
acceptance, diagnostics, C++ emission nor existing checker authority.
Keep the work on the current `mir-m3-regions` branch, with one final commit
per step and the same review/readiness gates before the user's merge.

## Concrete contract

Assume `Cell` has an `int32` field `value` and a constructor assigning it.
This already compiles; before M3.9, MIR rejected its non-prefix declaration:

```python
def late(stop: bool) -> int32:
    if stop:
        return 0
    local = Cell(1)
    alias = local
    local.value = 9
    return alias.value
```

The C++ declares `Cell local` after the conditional return and binds
`Cell& alias = local`. M3.9 analyzes that existing placement. The early
return has no `local` storage end; the other return evaluates to 9 before
ending the constructed storage. The same distinction applies to an earlier
return/break/continue inside an emitted branch or loop scope.

M3.10 addresses a different existing shape:

```python
def hoisted(flag: bool) -> int32:
    if flag:
        local = Cell(1)
    else:
        local = Cell(2)
    alias = local
    local.value = 9
    return alias.value
```

Here the emitter declares `Cell* local` before the `if`, with two private
`std::optional<Cell>` backing slots at function entry. Each arm constructs
into its own backing and assigns the pointer. Declaring a backing slot does
not construct its record. The holder's residence, the backing's placement,
and the assignment that makes the source name available are separate facts.

CPython observes the shared mutation in both examples. Python indentation
does not introduce a binding lifetime. MIR observes emitted storage; it does
not endorse a C++ lifetime as a new language rule or repair the already
filed branch-local dangling-reference defect.

## Survey and precedents

- Before M3.9, `mir/lower.py` applied its declaration-prefix restriction separately to
  each scope. Existing expression, initializer, type and form gates are
  independent of that restriction and remain in force.
- `MIRRecordWriteMode.INITIALIZE_REGION` and its scalar-payload counterpart
  already locate construction after evaluation of its initializer. A new
  lifetime-start instruction or synthetic emitted scope is unnecessary.
- `mir/validate.py` already has reset-aware must-assignment and guards
  against repeated construction within one activation. M3.9 removes the
  prefix-specific requirement that scoped initialization dominate every exit.
- `mir/scope_lifetime.py` already computes may-initialization, including
  possibly unengaged body-hoisted OWN sites. It filters possible ends by
  both physical placement and whether construction may have happened.
- THIR's `_lower_if_hoist_predecls` and `_lower_hoist_predecls` decide actual
  ordinary hoist flavors. `_register_frame_hoist` is the separate resumable
  precedent: a frame member registers a binding without another declaration.
  Ordinary MIR must not infer its own flavor from emitted C++ strings.
- `THIRIf.hoist_decls` and `THIRWhile.hoist_decls` describe declarations
  immediately before those control statements, in their containing scope.
  `THIRIf.hoist_slots` is different: the emitter reserves deferred backing;
  `_use_rebind_slot` drains a used slot to function-level `hoist_lines`.
- The ordinary function producer currently leaves
  `THIRFunctionLayout.hoisted_locals` empty, even for real hoists. That field
  is not a sufficient inventory of emitted backing.
- `_lower_record_ptr_slot_decl` chooses `RECORD_HOISTED` for an
  escape-hoisted record. M3.10 supplies its `owned_storage` fact and the
  corresponding BODY placement. MIR consumes those facts rather than
  inferring placement from pointer form.

Compile-only probes confirmed late declarations in free functions, methods
and constructor tails; nested early exits; loop continues before construction;
scalar, scalar-tuple, record-pointer, pointer-Optional and readonly hoists; branch OWN
backing; and a loop-local `RECORD_HOISTED` backed at function scope.
CPython probes confirmed the mutation and branch results. No unsafe C++ was
executed and no new source-behavior defect was found.

## M3.9: declarations at reached points

Invariant: a direct local is initialized zero or once per activation of its
actual emitted scope; reads require definite initialization, while possible
storage ends require possible initialization on the exiting path.

1. Remove only the declaration-prefix restriction. Retain the existing
   duplicate-binding, lexical-map restoration, type/form, initializer,
   readonly, constructor-layout and cycle restrictions.
2. Keep the declaration in its existing emitted region. Its initializer
   remains at its reached CFG point, including any existing conditional
   expression blocks. Do not move it to region entry or add fake braces.
3. Replace the blanket initialization-before-every-exit validation rule with
   the zero-or-once contract. Preserve definite assignment at every read,
   unique initialization sites, owner-region checks, and the prohibition on
   revisiting construction without exiting/reentering its owning region.
   An exit that bypasses construction is valid, not malformed MIR.
4. Reuse the current may-initialization end inventory and verify its boundary
   resets. A skipped declaration has no end; a constructed empty Optional
   still has a wrapper end but no present payload end. Must-assignment is
   never replaced by this may analysis.
5. Preserve stale-alias handling: exit/reentry clears activation-local facts
   even if construction was skipped. Outer holders keep referents to expired
   roots; reconstruction does not refresh an old alias. Static site identity
   is not dynamic object identity or a safety proof.

Cover the existing direct-declaration shapes: bool/int32, plain/readonly
record holders, flat tuple holders, borrowed Optional/union holders,
constructor-backed Optional records, and inline scalar Optional/union
storage. Existing copy/move gates remain; cyclic copy/move/in-place writes
are not added. Non-prefix OWN reseats continue to use their existing rules.

## M3.10: positive facts for bounded ordinary hoists

Invariant: every newly admitted hoist has a complete semantic description
from the producer that chose its emission; source binding availability and
record backing engagement are never inferred from the existence of a slot.

The strict validator still rejects reads without definite assignment.
Lowering reports this specific proof failure as `MIRNotCovered`, through
`MIRDefiniteAssignmentError`; structural/type validation failures still
propagate. This also covers valid constant-condition loops whose impossible
zero-trip edge remains in MIR's structural CFG. Constant-edge reasoning is
separate work. This boundary adjustment was approved 2026-09-20.

1. Carry structured hoisted-binding facts beside the existing render data on
   `THIRIf`/`THIRWhile`: source name, semantic type/access/layout, containing
   scope placement and initially unassigned source binding. Produce them at
   the shared hoist decision helpers; reuse existing record/Optional/tuple
   layout classifiers. Unknown or partially described hoists stay uncovered.
   Require complete name/order correspondence with the emitted declarations,
   without parsing `cpp_type` or deriving facts from a string prefix.
2. Register those holders in the containing MIR region before evaluating
   the control statement, and keep their identity through its join. Do not
   emit an initialization value merely because a C++ predeclaration exists.
   Existing writes establish definite availability and referent facts.
3. Initially admit bool/int32 predecls, tuples of those scalars, plain/readonly
   record pointers and pointer-representation Optional record holders for
   if/while. Also admit
   eligible flat borrowed tuple holders at if-chain heads, reusing the tuple
   hoist helper's existing element-const verdict. While borrowed-tuple hoists
   and additional readonly tuple capture forms remain outside current THIR
   admission; preserve those gates and the Optional capture forms the frontend
   does not yet lower. Scalar tuple defaults establish no source availability:
   these existing MIR tuple forms expose copied scalar values, not addressable
   scalar-payload aliases or owned resource lifetimes.
4. For constructor assignments into hoisted record holders, consume the
   existing positive OWN-site operation fact and private BODY backing.
   A skipped arm never engages its backing; a repeated site is reuse under
   the existing OWN-site rules. An unclassified `BRANCH_RVALUE` render alone
   is insufficient and remains uncovered. Do not infer OWN versus IN_PLACE.
5. Add the missing positive backing fact for supported `RECORD_HOISTED`
   constructor declarations at their producer. Lower its body-hoisted
   backing through existing OWN-site initialization/reuse semantics, not
   INITIALIZE_REGION or unconditional INITIALIZE_ONCE. Its pointer holder
   still resides in the source statement's emitted scope. Loop exit must
   not end that BODY backing. Preserve movable/plain-record constructor gates.
6. Keep emission unchanged and check producer/emitter agreement. Do not
   route ordinary hoists through resumable frame handling, treat the empty
   function layout as proof of no hoists, or broaden cleanup coverage.

Scalar Optional/union predecls are explicitly deferred: C++ default-constructs
physical wrapper storage before Python assigns the source name. The current
MIR assigned set cannot encode those two starts independently. Treating the
predecl as a source assignment is wrong; treating its first source assignment
as physical construction is also wrong. Their later design must represent
that distinction. Optional-storage record predecls and owning/mixed tuple
backing have the same additional obligation. Nonempty `hoist_slots`, general
factories and unsupported rebind forms remain uncovered in this step.

## Factored scope matrix

A cell is covered only if all applicable axes and existing operation gates
admit it. Everything else stays explicitly uncovered, not implicitly safe.

| Axis | M3.9 / M3.10 coverage | Filed gap |
|---|---|---|
| Position | Existing monomorphic synchronous free functions, methods and eligible constructor tails; if/elif/else and while/else | Module statements, closures/captures, generators, async, comprehensions, context managers, try/finally, error-return, match, for-loops; generic bodies |
| Shape | M3.9: existing direct scalar, record, tuple, Optional and union forms, including existing readonly and Own operations. M3.10: the explicit holder/backing list above | Other scalars, str/bytes, views, Ptr/Span, Box/Rc, containers, owned aggregates; scalar Optional/union and optional-storage record hoists; while borrowed-tuple hoists and whole-readonly auto tuple metadata gaps |
| Slot | Local bindings and positively described private backing; existing parameter referents, field reads/scalar writes and scalar returns unchanged | New parameter/return forms, field storage/replacement, container elements, global/capture sinks, borrowed escapes and frame slots |
| Operation | Existing initialization/alias/scalar-write forms; late reached declarations; positive OWN-site reuse; normal exits | New calls/effects, factories, cyclic copy/move/in-place, destructor/exception/suspension effects, unknown or partially described hoists |

Remaining cells stay under the M2-M5 work packages in `TODO.md`. This batch
does not complete M3, change the language lifetime rule, or switch authority.

## Tests, docs and readiness

Implementation touches THIR nodes, the shared declaration/hoist producers and
emitter agreement checks; MIR coverage/lowering, validation and existing
activation/end consumers; unit tests and documentation. Parser, sema language
rules, type-system rules, runtime and stdlib behavior remain unchanged.

- Unit-owned source probes compare producer facts, emitted placement and MIR
  results for free functions, methods and constructor tails. Never load the
  snippet corpus from compiler code or its unit tests.
- M3.9: return/break/continue before and after construction; zero iterations;
  nested loops and while-else; conditional initializers; absent versus skipped
  Optional; read-before-init across activation reset; repeated construction;
  alias mutation; singleton/mixed tuples and Optional/union siblings.
- Exercise initialize -> skip -> initialize with a retained old alias,
  alongside dead-holder and reseated-holder controls. Keep stale scalar
  extractions invalid until a fresh extraction. Use bounded independent
  activation traces; production dataflow stays finite and conservative.
- M3.10: assignment on both arms versus a missing arm; holder scope versus
  backing duration; caller aliases versus private OWN sites; readonly and
  pointer-Optional forms; zero-trip loops and body/else joins; repeated hoisted
  backing; missing/mismatched/partial metadata. Keep default-constructed
  wrapper and nonempty-hoist-slot exclusions pinned.
- Retained dangling witnesses are analysis-only. Add a condensed runtime case
  only for behavior not already pinned; any reference boundary must observe
  shared mutation. No new warnings/errors/panics or C++ snapshot churn is
  intended. Unexpected existing snapshot changes require investigation and
  approval, not automatic regeneration.
- Update this plan, `MIR_ANALYSIS_PLAN.md`, `IR_DESIGN.md`,
  `LANGUAGE_FEATURES.md` and `TODO.md` with actual status. Review both steps,
  complete readiness/retrospective gates, and run one full forced-exec suite
  on the finished batch. Keep one final squashed commit per step on a branch.

## Pitfalls and risk assessment

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`, `tuple-equals-scalar`:
  preserve existing operation facts; test shared mutation and scalar/singleton/
  mixed holders; file excluded owning/default-constructed siblings explicitly.
- `same-construct-every-position`: apply the factored matrix and producer probes
  in free functions, methods and constructor tails; other positions stay gated.
- `conditional-operand-evaluates-in-place`: initialization stays after its
  guard, once when reached; no source evaluation moves with a predeclaration.
- `generic-equals-monomorphic-twin`: no generic-body admission or instantiation
  verdict changes; generic coverage remains later work.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: no emitter
  behavior change; compare C++ placement and retain existing snapshots.
- `runtime-template-kind-matrix`: no runtime template changes.
- `const-source-const-loop-var`: preserve readonly layout/access facts; no new
  iterator or frame-local admission.
- `no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics`: new facts appear
  only in internal analysis/debug output, not source diagnostics.
- `reject-valid-python-only-as-documented-divergence`,
  `no-warning-on-valid-code`: coverage failure changes neither source acceptance
  nor warnings, and an empty conflict set authorizes no safety decision.

Confidence: high for the bounded contracts above, based on current producer
and emitter inspection, compile-only probes, the existing activation passes
and independent parity review. Main risks are conflating availability with
physical initialization, accepting incomplete hoist facts, and retaining old
activation state. Validation and negative tests must target those invariants.
Extend the current worklists and per-region indices; do not enumerate paths
or add a separate alias engine. Cleanup/destruction and physical-versus-source
initialization for default-constructed hoists need separate designs.
