# MIR call effects: design and first writing-call batch

Status: M4.3 and M4.4 implemented for analysis and debug inspection. This extends the
implemented [M4.1/M4.2 interface](MIR_CALL_SUMMARY_INTERFACE_PLAN.md).
It advances M4 and supplies foundations for M3 W3/W5; it does not complete
general effects, exception handling, escape analysis or production admission.

## Contract

```python
def assign(cell: Cell, value: int32) -> int32:
    cell.value = value
    return cell.value

def forward(cell: Cell, value: int32) -> int32:
    return assign(cell, value)
```

For a verified hook-free record with bool/int32 fields, infer that `assign`
may write parameter 0's `value` field, without replacing the record, retaining
an argument, returning a borrow or taking a non-normal exit. Remap that effect
through `forward`. The C++ remains a reference-parameter call.

Include ordinary setters returning `None`, such as
`def set_flag(cell: Cell, flag: bool): cell.flag = flag`. They need a real
no-result MIR call statement, not a fabricated scalar result.

Invariant: every admitted call consumes a finalized summary of the exact
selected definition; its effects are substituted through the actual argument
referents, and writing contents never implicitly means ending storage.

## Baseline evidence and precedent

Source probes for scalar-returning writes, void setters, forwarding, local
aliases, branch-selected aliases and repeated actual arguments compile and
match CPython. `@nocopy` plus mutation observations guard reference semantics.
MIR summarizes these writing leaf bodies and remaps their effects through
callers; unsupported storage operations, roots and exits remain Opaque.
This is an analysis extension, unlike the source-lowering prerequisite found
by the [range-head investigation](MIR_RANGE_HEAD_STORAGE_PLAN.md).

Reuse these existing authorities:

- `THIRResolvedCallee` and its exact declaration/signature identity.
- `MIRDefinitions` for field layout, access and hook-free record eligibility.
- `MIRCallSummary` and Pending/Opaque/Known states; broaden their proven
  contract without installing default-empty evidence.
- `analyze_dependencies` and `resolve_referents` for aliases and joins.
- The existing workspace dependency scheduler for leaves, forward/imported
  definitions, missing definitions and recursive components.
- Existing eager-expression effect gates and lazy CFG construction.

Sema's mutation facts remain useful in their current domain, but do not prove
absence of retention, globals, exceptions or cleanup. Do not use those facts
to manufacture a complete MIR summary, duplicate their analysis in an AST
walker, or feed new MIR evidence back into production checking.

## General effect direction

Keep the interface's dimensions distinct: observed places, content writes,
storage/structural invalidation, result form/dependencies, retained dependencies,
and possible exits/cleanup obligations. General summaries will need parameter,
environment and qualified-global roots, with projections and substitution.
A callee-local or fresh-result root must never vanish during remapping.

Implement only dimensions with a concrete consumer. The first batch adds
parameter-relative scalar-field writes; all other non-read effects still
require explicit empty proofs. Do not add unused root variants or pretend that
normal-return-only evidence represents exceptions or suspension.

Later dependency groups are:

1. **Storage and escape effects:** returned/retained dependencies, replacement
   and container invalidation, global/environment roots and their substitution
   into live-loan analysis. These unblock W5 and more of ordinary W1/W2.
2. **Exit-specific effects:** distinguish normal, exceptional and nonreturning
   paths, then add MIR exceptional successors and cleanup with W3. Effects
   occurring before a throw must survive on its exceptional edge.
3. **Callable and instantiation obligations:** receivers, callbacks, native
   contracts, generic instantiations and frame effects, composed with W4/W5.

These are dependency groups, not one-commit promises. M5 still owns source
diagnostics and the explicit authority switch. Call summaries alone will not
fix expression sequencing or materialized-argument destruction timing.

## First batch representation and proof

### Typed may-write paths

Replace index-only write placeholders with immutable parameter-relative
places: parameter index plus a typed field path. Reuse `THIRFieldIdentity`
components; importing `MIRField` into the contract would create an import
cycle because MIR nodes already depend on the call contract.

Initially a write path contains exactly one bool/int32 field of a borrowed
plain-record parameter. Validate index, field owner/name/type, actual verified
layout and mutable access. Empty paths (whole-record writes), nested fields,
wrapper payload replacement and structural writes remain unsupported.
Keep conservative reads of all parameters; field-precise read minimization
is not required. Keep invalidates, returned borrows and retention empty,
with normal completion proven over the existing acyclic operation subset.

Writes are MAY effects, unioned across branches. They are not assignments of
a known value and do not imply every path performs the write. A scalar return
is still unknown; the summary is not an evaluator.

### Direct extraction and forwarding

Extract direct scalar-field writes from validated MIR. At each operation use
the existing dependency state to resolve the target through local aliases.
Convert all resolved parameter origins to symbolic field paths. Missing or
unsupported origins make the summary Opaque, never partially Known.

At a call, substitute each effect's parameter with the corresponding actual
borrow holder and resolve its referents at that program point. For a forwarding
summary, map those origins to the caller's parameters. Union the effects with
direct writes. Use one shared remapping helper for extraction and an inspectable
call-site effect inventory; do not introduce a second alias solver.

`inner(x, x)` must merge both formal effects onto the same actual referent.
An alias selected from `a` and `b` contributes both possible roots. Distinct
external parameter roots remain potentially aliased: a readonly formal is
not an immutable snapshot when a different writable formal can alias it.
Keep typed root identity and existing may-overlap semantics; do not infer
disjointness from different parameter indices.

This flat domain is bounded by the caller signature and verified fields;
deduplication prevents forwarding-depth growth. Broader projections and
result expressions need explicit size limits before they are admitted.

### Caller operations and analysis

Keep scalar-result calls as `MIRCall` rvalues. Add an explicit effect-only
statement wrapping the same call payload for `VoidType` calls in discarded
expression position. Share argument construction and contract validation.
Do not create a dummy result slot or accept a void call in a value position.

All statement consumers must classify this new statement before accessing
`.target` or `.value`: validation/definite assignment, operand inventory,
liveness, dependencies, presence, engagement, storage/payload/scope ends,
retention, summary extraction and dumps. A no-result call defines no binding
and uses all arguments, including write-only borrowed arguments.

For this bounded contract, scalar-field writes preserve referent edges,
wrapper selection and storage engagement. They create no replacement or
payload-end event and no borrow conflict merely for mutation. No current MIR
analysis caches scalar field values. A local snapshot such as `old = cell.flag`
retains its old value; subsequent field reads observe the changed storage.
Do not add a scalar solver or indiscriminately clear unrelated holder facts.

Mark writing calls effectful in `_Coverage.writes`. Preserve existing eager
ordering exclusions: `assign(cell, n) == read(cell)` must not gain coverage
when the generated C++ operand order is unproven. Existing lazy CFG arms can
sequence calls conditionally. `ordered_temporary_expression` must no longer
treat every Known call as harmless; retain its conservative no-effect proof
for named-argument materialization. Stable argument restrictions remain.

Update debug output to display actual effects instead of labelling every
call a reader. No compiler invocation outside MIR inspection gains new checks.

## Implemented layers

1. **M4.3: typed write contract and leaf extraction.** Validated field paths,
   direct write extraction through alias/branch provenance, and void-returning
   leaf summaries. Summary validity is separate from consumer coverage: a
   richer certificate cannot bypass lowering and standalone validation gates.
2. **M4.4: writing-call application and forwarding.** The no-result statement,
   shared validation/argument helpers, call-site remapping and transitive
   summaries compose with effect-aware ordering and every statement consumer.
   Recursive and unknown callees remain Opaque.

The remaining effect families and source-coverage boundaries are listed below;
these layers do not change production checker authority.

## Scope matrix

| Axis | First batch | Explicit remaining scope |
| --- | --- | --- |
| Callee | resolved ordinary nongeneric free definitions; acyclic scalar operations, direct writes and known calls | method/constructor/static callees, closures/callbacks, native/protocol and generic dispatch: later M2/M4 |
| Caller | existing ordinary free/method/constructor-tail bodies and supported if/while/for CFG | module/comprehension/match W2; with/try/finally/error-return W3; generator/async W4; closure W5 |
| Shape | bool/int32 values and existing borrowed hook-free bool/int32-field records; scalar or void result | tuple/Optional/union parameters/results, str/bytes, Own, Ptr/Span, Box/Rc, containers and nested records: M2/M4/W5 |
| Access | local aliases and branch unions; mutable actual for written parameter; readonly readers unchanged | field/container/global/capture roots as call arguments, returned or retained aliases: later M4/W5 |
| Effect | may-write a scalar field without replacing backing; conservative reads; explicit no invalidation/retention | record/wrapper replacement, structural mutation, globals/environments, cleanup/throw/nonreturn: W3/W5/M4 |
| Evaluation | stable arguments; standalone, returned, sequenced or lazy scalar calls; standalone void calls | arbitrary argument effects, unproven eager operand order, range-head materialization: separate sequencing work |

Existing local tuple/Optional/union holders may remain live across these calls;
that does not admit wrapper parameters or wrapper mutation. Existing readonly
named temporary arguments remain subject to their stricter placement rules;
this batch adds no mutable-temporary source or MIR admission.

Constructor callers are exercised through an existing scalar-parameter tail
with local record storage. Borrowed-record constructor parameters remain
outside the current constructor lowering boundary. Holder controls use the
existing tuple parameter and record-only union forms; mixed record/scalar
unions and new tuple-local alias forms are not added here.

## Verification and pitfalls

- Pin exact symbolic fields for direct/conditional writes, branch-selected
  aliases, alias reseating, two same-typed fields, repeated actual arguments,
  forwarded/imported aliases, void setters and mixed scalar/void forwarding.
- Inspect call-site mapped effects, not just the summary at the leaf. Confirm
  untouched fields are absent and possible alias origins are never lost.
- Prove argument liveness/availability and readonly checks remain; scalar
  writes preserve record borrows and live scalar/singleton/mixed tuple,
  Optional/union holder dependencies without false lifetime conflicts.
- Pin pre-call scalar snapshots, unknown scalar results, calls in both lazy
  branches, and rejection of unproven eager/named-temp evaluation order.
- Negative controls: Pending/Opaque, stale/malformed identity or field layout,
  unknown roots, recursion, loops in the callee proof, owned backing, hooks,
  globals, borrowed returns/retention, wrappers and exceptional exits.
- Use embedded compiler sources, not snippet-file reads. The probe's existing
  C++/CPython parity is baseline evidence; add one condensed mutation-observing
  `@nocopy` case only if the existing focused corpus does not pin these source
  boundaries. Actual MIR evidence belongs in the compiler tests.
- Preserve existing C++/diagnostic snapshots; any unexpected churn requires
  investigation, not automatic regeneration. Include generic/position twins
  as unchanged-source controls where the shared producer can reach them.

Pitfalls covered: reference identity and copy-warning placement by observable
mutation and nocopy; tuple-vs-scalar by holder controls; shared positions by
the scope matrix; conditional evaluation by call-point/CFG assertions; generic
equivalence by unchanged producers and explicit unresolved-summary boundaries.
Views, allocation, runtime templates, loop constness and C++ formatting do not
change. No new source warnings, rejections or internal diagnostic names are
authorized. Update LANGUAGE_FEATURES, ARCHITECTURE, the call interface and M3
checklist with implemented coverage at each step.

Confidence: high for the first batch's architecture. The scope is supported by
existing source probes, validated leaf MIR and the dependency/effect gate
survey. The principal implementation risk is an incomplete no-result-statement
audit or accidental writer admission through a formerly reader-only proof.
