# Bounded M3: liveness and reference dependencies

Status: approved after M2.13 (`5ddf2de82c`), with `--dump-mir` added as the
first delivery step. This document specifies two analysis increments, not
completion of M3. Implementation status is recorded below.

## Contract and example

For a `Cell` with an `int32` field and a constructor assigning that field:

```python
def retained() -> int32:
    current: Cell | None = Cell(1)
    saved: Cell | None = current
    if current is not None:
        current.value = 7
    current = Cell(2)
    current = None
    if saved is not None:
        return saved.value
    return 0
```

This already returns 7 under TPy and CPython. Generated C++ has two separate
backing slots and nullable pointer holders. Reseating and clearing `current`
leave `saved` referring to the first slot. A tuple holding that reference
must preserve the same dependency. This work describes those relationships
at CFG points; it does not change source acceptance or emitted operations.

**Invariant:** at every reachable point, the analysis conservatively retains
every storage dependency carried by every possibly live holder, including
references copied into aggregate payloads, independently of the source
holder's later value or last use.

Classification: architectural. The new facts are internal MIR analysis
results. No parser, sema, type-system, runtime, stdlib or emitter behavior
changes; existing provenance, alias-storage selection and AST last-use
analysis remain authoritative.

## Existing authorities and evidence

- `mir/nodes.py` supplies body/slot/block IDs, record storage, typed places,
  wrapper layouts and explicit value operations. Reuse these identities.
- `mir/validate.py::operands` and `successors` are the existing operation/CFG
  inventory. Reuse them; centralize any additional use/definition description
  needed by both analysis and validation instead of making divergent lists.
- `mir/presence.py` supplies a finite worklist precedent and validates selected
  payloads and scalar union aliases. It is not a lifetime checker. The first
  dependency pass does not require a new path-sensitive presence solver.
- `tpyc/liveness.py` computes AST last-use candidates for current auto-move.
  It remains in place; its string-based alias maps are not input to MIR.
- `sema/context.py::BorrowTracker` and `sema/alias_rebind.py` currently track
  borrowers and select storage. MIR consumes the resulting explicit storage
  operations, rather than rerunning those policy decisions.
- `thir/emit.py::_own_slot` hoists OWN-site backing into the function body.
  `emit_thir_body` emits those declarations before its statements. A source
  branch does not bound that backing's lifetime. Entry-prefix record locals
  likewise have function-body backing; the MIR coverage gate excludes local
  declarations first encountered in branches and owning operations in loops.
- `THIRNarrowAlias` emission distinguishes a scalar reference into a union
  payload from a copied pointer to an external record. MIR preserves this as
  `PAYLOAD_ALIAS`/`alias_source` versus `BORROWED_RECORD`.

Independent design probes in free functions produced `optional 7` and
`tuple 7` under both CPython and native TPy. The existing
`optional/owned_record_alias_reseat` case also pins shared mutation and
branch reseats in functions, methods and constructor tails. Its C++ hoists
the branch OWN slots outside the branches. Those tests establish emitted
behavior, not the new analysis results.

## M3.1: backward liveness

Add an internal `mir/liveness.py` analysis over a validated `MIRFunction`.
Results use stable program points: before/after each statement and at block
terminators, identified by block ID and statement position. Expose block
entry/exit live sets and statement-point live sets for tests and the next
pass. Unreachable points are absent, not successful empty analyses.

Use ordinary conservative may-liveness:

```text
live_out(block) = union(live_in(successor))
live_before(stmt) = uses(stmt) union (live_after(stmt) - defs(stmt))
```

The transfer contract is:

- A whole-slot assignment defines that slot. Read its RHS before killing the
  old value, including self-assignment and a constructor reading an old field.
- A projected write uses its address-bearing root and all RHS operands. It
  does not define or kill the root holder; writing `x.value` does not rebind x.
- Branches use their condition; scalar returns use their returned slot. Void
  returns have no outgoing uses. Constructor receiver initialization reads
  its parameter operands before entering the ordinary CFG.
- A scalar `PAYLOAD_ALIAS` remains a reference to `alias_source`. A live alias
  pins that source holder and any required alias chain at the same point.
  Ordinary reference copies do not pin their original source *holder*: their
  captured referent is handled by M3.2.
- Reading one aggregate element keeps the whole aggregate holder live in
  this first pass. Element-level last-use precision is deferred explicitly.
- All syntactically reachable CFG edges participate. Do not claim correlated
  branch/path precision from ordinary set joins. Existing validation still
  establishes whether a payload read is valid.

Solve backward to a fixed point with a predecessor worklist. The domain is
finite subsets of body slots. Loops, zero trips, break/continue and backedges
must converge without bounded unrolling or an iteration-count cutoff.

These are liveness facts, not move eligibility or dead-store elimination.
A write to a scalar global or a field can be observable even when its local
value has no subsequent read. Do not delete it or classify it as harmless.

## M3.2: forward referents and live dependencies

Add an internal `mir/dependencies.py` pass and a result type distinct from
liveness. It accepts validated MIR and its liveness result, with matching
body identity. Neither pass takes an analyzer, source AST, C++ text, source
variable-name maps or compiler-global side state.

Track possible referents per reference-bearing holder leaf. A leaf is the
whole borrowed-record holder, a tuple element, an Optional payload or a union
alternative. Keep the aggregate path; do not flatten it into one record
reference. Scalar leaves carry no referent unless explicitly PAYLOAD_ALIAS.

Referent roots are existing local RECORD_STORAGE identities or symbolic
external inputs (parameter/receiver plus payload path). Append canonical
field paths for inline record fields. A scalar payload alias instead depends
on the wrapper slot and selected payload. External roots name origins, not
proven-disjoint objects: two parameters, or two payloads of one parameter,
can alias each other or have ancestor/descendant relationships.

Track all reference-bearing holder values forward, without using liveness
to discard intermediate state. Join possible referents by union, leaf by
leaf, and strong-update the destination's leaves on a whole-holder write.
Read source facts before replacing destination facts. At a query point,
select dependencies of the holders live there. A copied dependency survives
even when the source holder has become dead or has been rebound.

| MIR operation | Dependency transfer |
|---|---|
| `MIRBorrow` | Resolve the source place to storage and field path; borrowing RECORD_STORAGE starts at that storage ID, while dereferencing a holder follows its current referents. |
| `MIRAlias` | Snapshot source referents into the destination holder; do not retain a link to the source holder's future value. |
| Reference-valued `MIRRead` / `MIRUnionExtract` | Select and copy the source payload's referents, preserving field path and access. |
| Scalar `MIRRead` | Copy a scalar value, with no dependency after the read; reading through an alias uses its source while evaluating the read. |
| Scalar `MIRUnionExtract` into PAYLOAD_ALIAS | Retain a dependency on the selected wrapper storage, as recorded by `alias_source`; do not treat this as a scalar snapshot. |
| Tuple construction/copy | Transfer each borrowed element; scalar elements carry no loans. Empty tuples have a known-empty result. |
| Optional construction/copy | Transfer the reference payload; constructing None clears only the destination's payload dependency. |
| Union construction/copy | Transfer the selected reference alternative; a whole copy preserves possible alternatives and their dependencies. Scalar/None alternatives carry none. |
| Record construct/copy/move into fresh storage | Destination denotes its own storage ID. Eligible owned records have scalar fields, so no interior reference payload needs copying. Follow current MIRMove semantics; do not invent source death or exclusivity. |
| OWN reseat | The existing construction creates a distinct site storage ID; subsequent holder assignment changes only that holder. |
| IN_PLACE replacement / scalar field store | Preserve reached storage identity and all other holders' referents. Evaluate RHS first. Do not infer a general permission to replace aliased objects. |
| Scalar operations, tag/presence tests and scalar globals | No retained referents in the result; operand evaluation still contributes uses to M3.1. |

Readonly is an access restriction, not uniqueness or immutability of the
referent through other paths. Preserve existing access facts and validation;
do not introduce Rust-style shared-versus-exclusive borrowing rules.

The result exposes possible referents and active dependencies by holder and
program point, plus an inverse storage-to-live-holder view. This is a loan
inventory for later consumers, not a `safe` boolean. Known-empty, unreachable,
unsupported and invalid MIR remain different outcomes. Missing support for
an operation or shape cannot silently produce an empty dependency set.

Use a forward worklist with finite root/path sets. Build the inline field
graph from the MIR places' canonical field owners and field types, including
`alias_source` projections. `MIRFunction.records` alone is insufficient: it
does not contain layouts for every borrowed parameter type. Verify compatible
paths and an acyclic field graph before solving; unsupported recursive path
growth makes the body uncovered. Neither loop iterations nor copies mint new
origins. Keep owning creation/replacement in cycles excluded. No widening to
a guessed empty or unknown-safe state is allowed.

The first pass may retain dependencies from infeasible combinations after a
join. This is acceptable for internal conservative inventories; it is not a
promise of precise diagnostics. No correlation-sensitive ownership decision
may consume them without a later design and tests.

## Storage duration and limits of the result

The bounded storage contract is explicit:

- External parameter/receiver storage is supplied by the caller for the body;
  this pass does not prove caller obligations or independence of inputs.
- An admitted local backing site becomes initialized when its construction
  executes and remains available until body exit. A hoisted optional slot's
  existence at entry is not proof that its record payload is initialized.
- A holder's last use, reseat or None assignment never destroys its referent.
  Synthetic MIR temporaries are holders, not inferred C++ allocations.
- Returning ends the body; currently admitted scalar returns cannot export
  these references. No implicit scope end is inferred from a CFG join.

Make that contract positive MIR data in M3.2: propose an optional
`MIRSlot.storage_duration` fact using `MIRStorageDuration.BODY` or `CALLER`.
It describes the slot's actual backing when used as a dependency root, not
the lifetime of whatever a pointer holder currently refers to. `None` means
unavailable, never an inferred BODY duration.

The existing THIR-to-MIR producer arms stamp BODY on admitted entry backing
and hoisted OWN-site record storage, and on an admitted local union wrapper
whose scalar payload can be borrowed. A borrowed parameter wrapper uses
CALLER; symbolic external record referents retain the caller obligation above.
Stamp only where the selected THIR storage operation proves the placement;
do not consult emitter names or an analyzer from the analysis pass. Synthetic
reference temporaries do not acquire ownership by receiving a slot ID.

Validate fact/category compatibility. M3.2 requires a recognized duration for
every backing root it tracks; missing or unsupported duration produces an
explicit uncovered result, while malformed facts fail validation. Existing
MIR construction and M3.1 can still operate without those facts. Pair the
producer tests with emitted-C++ placement witnesses, including branch OWN
sites, to establish what structural MIR validation alone cannot verify.
Future branch-local or expression-temporary producers must supply their own
duration model before this analysis admits them.

There are no general region/end/cleanup facts in MIR today. Before checking
scope escapes, source `del`, real expression temporaries, repeated owning
sites, destruction, suspension or borrowed returns, carry their actual
placement/end events from THIR into MIR. C++ RAII is not a substitute for
those analysis facts. Designing that extension is the next M3 checkpoint,
after this proposal's two increments.

Accordingly, safe/unsafe witnesses here test a narrower claim: whether a
storage dependency is still required at a point. A live alias makes a claim
of "no remaining dependency" wrong; after its last use that specific claim
may be true. Even an empty set does not authorize release, movement or early
destruction. End-to-end dangling-reference diagnostics require the later
region/invalidation model and must not be advertised as delivered here.

## Scope matrix and deferred work

The axes intersect: every axis must be covered, and both existing lowering
and validation must accept the entire body. No source acceptance expands.

| Axis | Covered by M3.1/M3.2 | Deferred and tracked here / in the remaining-M2 plan |
|---|---|---|
| Position | Existing ordinary monomorphic free functions, instance methods, complete scalar constructor initialization and supported tails | Static/class/generated variants, module initialization, closure execution, generators, async, comprehensions, context managers, try/finally, error-return bodies and match arms need their existing M2/M3/M4 prerequisites. |
| Shape | bool/int32; borrowed plain records and finite inline field paths; current flat tuple/Optional/union payloads; supported scalar-field owned records; mutable/readonly access | str/bytes/views, nested or owned aggregate backing, Own parameters, Ptr/Span, Box/Rc, containers, native/value/inherited/protocol/generic shapes remain outside coverage. No generic approval from a monomorphic result. |
| Slot | Existing parameters/receiver, entry locals, synthetic temporaries, backing sites, scalar returns, field projections, qualified scalar globals | Reference/aggregate returns; reference-valued field/container/global/capture sinks; frame-held slots; real materialized expression temporaries need escape and placement contracts. |
| Control flow | Existing if/while/short-circuit, break/continue/early return, alias reseats in loops, acyclic owning sites | Branch/loop-first source bindings, cyclic owning sites, cleanup/exceptional/suspension edges and arbitrary effectful operands need explicit storage/region facts. |
| Precision | Whole-holder liveness, per-payload referent sets, conservative joins, stable storage/field paths | Element-level liveness, correlated paths, external-input disjointness, conflict policy and lifetime safety verdicts require separate justification. |

M2.11 call identities and M2.12 capture inventories are declarations only;
they do not make calls or closures executable in this analysis. Broader M2
coverage, M4 summaries and M5 authority/admission gates remain open.

## Tests and delivery

M3.1 tests use independently authored MIR CFGs with explicit expected live
sets: overwrite versus projected store; self-assignment; RHS-before-write;
diamond joins; early returns; zero-trip/backedge/break/continue paths;
constructor initialization; scalar globals; scalar payload aliases versus
scalar copies. Include loops needing more than one propagation round and
unreachable blocks. Exercise every MIR rvalue/terminator kind.

M3.2 tests assert referents and live-holder dependencies, not only execution
output. Include scalar/tuple/Optional/union twins, whole-aggregate copies,
two holders of the same storage, owner reseat/clear with a retained alias,
different OWN sites on branch edges, inline nested fields, readonly aliases,
parameter aliases, in-place RHS reads, copy/move into independent storage,
scalar union aliases versus extracted record pointers, and loop-carried
holder reseats. A dead source holder must not erase a copied dependency.
Use a bounded path-enumeration oracle for small CFGs to check that computed
sets contain every concrete dependency, without reimplementing the worklist.
Seed concrete parameter aliases; distinct parameter names are not enough.

Negative tests pin missing operation support, missing/unsupported storage duration,
invalid payload access and mismatched body results. Pair witnesses where a
dependency is still needed with ones where the last holder use is over.
Retain guards against unsupported calls/closures/owning loops and body kinds.
Source-to-THIR-to-MIR unit fixtures are independent of `tests/cases` files.

Audit existing native cases for deliberate mutation and placement witnesses;
reuse them when they already pin the intended behavior. Add a condensed case
only for an actual missing emitted-behavior witness. A new snippet cannot
substitute for tests of the MIR analysis. No existing snapshot changes are
expected; unexpected changes require investigation and approval.

Delivery order:

0. Debug CLI: `uv run tpyc --dump-mir program.py` prints supported CFGs and
   explicit uncovered/unavailable-body reasons, using actual emitted THIR.
   User modules only, including imported constructor definitions; no C++
   files or binary are produced. This does not enable MIR in normal builds.
1. M3.1: liveness implementation, focused tests, review/readiness and full
   verification; one squashed commit.
2. M3.2: positive backing-duration facts and referent/dependency implementation
   consuming M3.1, independent tests,
   review/readiness and full verification; one squashed commit stacked after
   M3.1. Document actual coverage and any narrowed prerequisite explicitly.

Keep the commits on a branch for user merge. No auto-move, checker authority,
codegen consumer, new warning or source rejection is part of either increment.
Update LANGUAGE_FEATURES and the analysis roadmap as each increment lands.

## Pitfalls, risk and design gate

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`: mutation witnesses and
  explicit operation transfers distinguish aliases from owned copies; no
  generated copy/move or diagnostic policy changes.
- `tuple-equals-scalar`: scalar, singleton/mixed tuple, Optional and union
  dependency twins, with every borrowed payload retained across copies.
- `same-construct-every-position`: shared analysis for the three admitted
  body kinds; the matrix lists all excluded positions and sinks.
- `conditional-operand-evaluates-in-place`: follow existing CFG sequencing;
  do not hoist computations or invent storage at an unexecuted OWN site.
- `generic-equals-monomorphic-twin`: open/generic bodies remain unsupported;
  no inferred obligation is discharged for them.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: emission
  stays unchanged; no view family is admitted by analogy with record pointers.
- `const-source-const-loop-var`: preserve readonly facts; no iterator/frame
  coverage or const-to-mutable conversion is added.
- `no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics`,
  `reject-valid-python-only-as-documented-divergence`,
  `no-warning-on-valid-code`: results are internal; no new source diagnostics
  or acceptance rule. Unsupported analysis is not a Python compile error.

Main risks are forgetting wrapper-held references, confusing live holders
with live storage, overclaiming external-input disjointness or path precision,
and treating present MIRMove as a general invalidation instruction. Explicit
transfer tables and independent identity/CFG witnesses address those risks.
Worklists must use changed-successor/predecessor scheduling; avoid repeated
whole-CFG scans and exponential path state in the production analysis.

Confidence: high for this bounded analysis-only contract, based on existing
operations, storage/emission inspection and native/CPython probes. General
lifetime safety is deliberately not claimed. No new adjacent defect was
established by the design survey.

The two increments and conservative precision boundary are approved. The
source language's ownership/borrowing rules and existing checker authority
stay unchanged. M3.1/M3.2 implementation is pending the debug CLI delivery.
