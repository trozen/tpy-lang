# M3.3/M3.4: backing reuse and retained-object conflicts

Status: design approved. M3.1/M3.2 and `--dump-mir` are merged; M3.3 is
implemented with positive record-write facts, bounded OWN-site reuse and a
debug event inventory. M3.4 implements internal possible retention conflicts.
This is a two-commit, analysis-only batch; it does not
complete M3 or change the authoritative provenance checker.

## Concrete contract

Assume `Cell` has one `int32` field, `value`, and a constructor assigning its
argument to that field:

```python
def reuse(n: int32) -> int32:
    current = Cell(0)
    saved = Cell(0)
    result = 0
    while n < 3:
        current = Cell(n)
        result = saved.value
        saved = current
        n = 2 if n == 1 else 3
    return result
```

Native and CPython probes of `reuse(1)` establish the existing behavior:

| Variant | TPy | CPython | Existing diagnostic |
|---|---|---|---|
| As above | 2 | 1 | Alias-clobber warning at replacement |
| Read `saved.value` before replacement | 1 | 1 | None |
| Retain `copy(current)` instead of `current` | 1 | 1 | None |

The emitted OWN assignment has the shape
`current = &*(__slot_N = Cell(n));`, with one body-hoisted
`std::optional<Cell>` per static replacement site. Reexecuting the site reuses
that backing. For these eligible scalar-field records, assignment can keep
the C++ object alive while changing the value seen through `saved`.
This is a logical-object retention conflict; it is not evidence of a dangling
pointer or an ended C++ lifetime. The existing warning, explicit `copy()`
alternative and documented alias-rebind divergence remain unchanged.

M3.3 should admit and model this bounded repeated-site behavior. M3.4 should
report an internal possible retention conflict for the first variant and none
for its last-use-before-replacement twin. The explicit-copy variant is an
emission/parity witness; cyclic copy operations need not become MIR-covered.

**Invariant:** a repeated admitted backing-write site keeps its physical
storage identity, and a logical replacement checks every other live holder
that may retain its previous object, including borrowed aggregate payloads.

## Precedent and phase boundary

Extend M2.2/M2.13's separate storage and holder identities and M3.2's positive
backing-duration facts. Consume the effective OWN/IN_PLACE verdict on emitted
THIR: readonly handling can change the earlier sema verdict. `THIREmitter`'s
`_own_slot` and record/Optional assignment paths establish the placement;
MIR must not reconstruct alias classification independently.

The changes belong in `tpyc/mir/`: nodes, THIR lowering, validation, analysis,
dumping, interpreter and focused tests. Parser, sema, types, C++ emission,
runtime and stdlib behavior stay unchanged. No normal compilation hook or
new source diagnostics are part of this batch.

## M3.3: positive backing-write facts and bounded reuse

Add an optional positive record-write fact to `MIRAssign`, with three modes:

- `INITIALIZE_ONCE`: initial backing construction, or already supported
  acyclic copy/move into fresh backing.
- `OWN_SITE`: an initialize-or-reuse write into the private body-hoisted
  backing of one effective THIR OWN replacement site.
- `IN_PLACE`: replacement through the existing holder, carrying the identity
  of the holder intentionally rebound by the source assignment.

These are facts on the actual write, not a separately maintained stream of
instructions. Exact Python class names are implementation details; the three
meanings and explicit rebind-owner identity are the contract. Ordinary scalar
field writes have no replacement fact. Constructor receiver initialization
remains a separate entry operation.

Validate fact, operation, target, duration and holder compatibility. One
private OWN site has one static constructor write and one storage root.
`BODY` duration alone never authorizes repeated writes. Initial construction
must be outside CFG cycles even when it has only one static writer.
Initial construction and Optional replacement share lowering helpers today;
callers must pass the
write meaning explicitly so initial backing does not accidentally become
reusable. Missing facts may remain usable by earlier acyclic analyses, but
the new event analysis returns an explicit uncovered result for them.
For IN_PLACE, the rebind-owner must equal the target's root holder, with
exactly the admitted plain-record or Optional-record dereference projections.
A compatible type or potentially aliasing holder is not enough: exempting
another holder could hide the conflict being checked.

Relax the cyclic-owning-operation gate only for positively marked OWN_SITE
constructor writes into eligible scalar-field record storage, including
Optional record backing. Keep cyclic IN_PLACE, copy/move, initial declarations
inside loops, custom constructor effects and broader owning shapes excluded.
Existing source coverage and definite-assignment/payload validation still
apply. Do not bypass the presence validator to admit new cases.

The MIR interpreter must reuse the heap identity at a repeated OWN_SITE write
and replace its contents. Different static sites remain distinct. Its current
fresh-object-per-root-write behavior cannot serve as the oracle for this slice.

Expose deterministic per-program-point write events in the dump, bound to the
exact immutable MIR function. An OWN_SITE event means initialize-or-reuse;
it need not classify the first dynamic execution separately. A live incoming
dependency on that private root witnesses possible prior execution. No epoch
counter or additional initialized-site fixed point is needed for that question.
The event inventory, liveness and dependencies must all belong to the same
immutable `MIRFunction` instance; reject mixed analysis inputs explicitly.

## M3.4: internal possible retention conflicts

Use the event inventory for both reusable OWN sites and already admitted
acyclic IN_PLACE replacements. This keeps the two existing replacement forms
under one contract without admitting cyclic IN_PLACE operations.

For each replacement:

1. Resolve the written storage from the incoming dependency state after RHS
   evaluation, before the write updates referents. Reuse/extract M3.2's place
   resolver rather than implementing another projection interpreter.
2. Select retained holders using liveness *after* the write. A last use during
   RHS evaluation must not cause a conflict merely because it was live before.
3. Exclude the old value of the intentionally rebound holder. OWN lowering's
   following holder assignment should already kill it in backward liveness;
   test ordinary and Optional sequences. IN_PLACE requires the explicit
   source rebind-owner fact because a projected write does not kill the holder.
4. Report other retained payloads whose possible referents overlap the
   replacement, with event location, holder/payload and affected storage.

Distinct private local roots are disjoint in this admitted no-escape subset.
Within a root, replacement overlaps itself and contained inline fields;
parent/child paths overlap. Do not assume distinct external inputs are
disjoint. External origin identity includes the parameter and payload selector
path, so two payloads of one tuple parameter are also potentially aliasing
inputs. Different origins may overlap; within one exact origin, disjoint
inline-field paths can remain disjoint. Readonly does not imply uniqueness.

Holder reseating, clearing an Optional, changing a pointer-union alternative
and scalar field mutation are not themselves record replacement events.
Existing scalar union payload-alias selection/lifetime restrictions remain
unchanged; wrapper storage must not be confused with a record pointee.

Use finite storage roots and existing monotone may-referent joins. Reports
are **possible** conflicts, not path-feasible counterexamples: referent joins
and whole-holder liveness can combine paths. Do not add generation tracking
or claim later uses are dangling. The API must distinguish uncovered analysis
from an empty conflict list and must not expose a general `safe` verdict.

Read liveness only answers this bounded retention question. A bound holder
that is never read can still matter to destructor timing; custom destruction
and cleanup need a different, later contract. No result here authorizes a
move, release, suppressed warning or new callable admission.

## Factored scope matrix

A cell is covered only when all axes below allow it and the operation has
the positive facts above. Excluded cells remain filed under the MIR roadmap
in TODO.md; lack of coverage never becomes a successful proof.

| Axis | Covered | Excluded / remaining prerequisite |
|---|---|---|
| Position | Existing ordinary free functions, instance methods, bounded constructor tails; supported branches and while CFGs | Module bodies, generators, async, comprehensions, closures, context-manager bodies, try/finally, error-return bodies and match arms require their existing region/effect contracts. No general for-loop or new binding-scope coverage. |
| Replacement shape | Eligible scalar-field plain records and Optional record backing; acyclic IN_PLACE plus bounded cyclic OWN_SITE construction | Owned tuple/union storage, container storage, arbitrary effects and cyclic copy/move/IN_PLACE need separate positive facts. |
| Retained holder shape | Existing scalar reference holder, flat singleton/mixed tuple, Optional and reference-union payloads; readonly facts preserved | Nested aggregates, str/bytes views, Ptr/Span, Box/Rc, general Own boundaries and other uncovered families keep their current gates. Empty/non-borrowing payloads carry no loan. |
| Slot / sink | Existing parameters, entry locals, temporaries and supported inline field paths; scalar returns/globals keep existing behavior | Branch/loop-first locals, reference returns, exported field/container/global stores, captures, frame slots and materialized owning temporaries need placement/escape facts. |
| Forms | Concrete MIR body forms already admitted | Open/generic execution and call effects remain M4 work; metadata alone grants no coverage. |

Branch-first and loop-first bindings cannot be modeled as lexical storage
ends: C++ may hoist their backing. Similarly, `THIRDelVar` is emitted only for
selected move-sinks; many Python deletions have no such operation. Actual
storage ends, scope escape, cleanup edges, suspension and destructor ordering
remain later M3 work. The total number of M3 increments remains open.

## Tests and review gates

M3.3 tests must pin source -> THIR -> MIR facts for ordinary and Optional OWN
sites, static-site separation and backedge reuse. Preserve readonly holder
access at the internal boundary; producers consume final THIR verdicts.
Pin physical identity in the interpreter; retain negative tests for malformed
facts, multiple writers, missing duration, cyclic initial construction,
copy/move, IN_PLACE and unsupported constructor effects.
Include forged IN_PLACE owner/projection facts and mismatched analysis inputs.

M3.4 tests must assert exact events and conflicting holder payloads:

- First use and different private sites versus reuse with a retained alias.
- Last read before replacement, including RHS-only use; ordinary and Optional
  destination-rebind sequences do not retain the destination's old loan.
- Scalar, singleton/mixed tuple, Optional and union holders retain aliases
  across copies. Overwriting or clearing one holder kills only its dependency.
- Acyclic IN_PLACE exempts its rebinding holder but detects another live alias;
  scalar field writes remain shared mutation. Exercise external origins,
  same-parameter payload aliases, parent/child and disjoint field paths.
- Branch/loop joins converge conservatively; unreachable paths and uncovered
  bodies do not produce a fabricated successful result.

Use independently authored internal MIR assertions and a bounded-path oracle
tracking concrete backing writes and retained holders, not a second copy of
the production worklist. Source fixtures belong in compiler unit-test inputs;
compiler tests must not reach into `tests/cases`.

The existing `records/warn_alias_rebind_clobber` native case already pins
warned storage reuse. Add a condensed runtime case only if it closes an
emission/parity gap; internal analysis assertions are mandatory regardless.
Passing reference tests must mutate and observe sharing or use `@nocopy`.
No new source errors or runtime panics are intended. Existing generated-code,
diagnostic and runtime snapshots should not change; unexpected churn requires
investigation and approval before refresh.

Each implementation increment gets targeted tests, independent defect review,
readiness/retrospective and a final forced full suite through `rpytest`.
Keep M3.3 and M3.4 as separate coherent commits on one branch, incorporating
this design checkpoint into M3.3 when preparing the batch. Do not merge or
push master as part of this batch.

## Pitfalls and risk assessment

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`: preserve existing
  copies/warnings and distinguish logical retention from physical lifetime.
- `tuple-equals-scalar`: include each admitted borrowed aggregate payload,
  including copied holders; no scalar-only conflict shortcut.
- `same-construct-every-position`: shared passes cover admitted body kinds;
  the matrix explicitly retains other position gates.
- `conditional-operand-evaluates-in-place`: use the actual CFG and post-RHS
  program point; do not invent an execution of an unvisited backing site.
- `generic-equals-monomorphic-twin`: no open-body obligations are discharged.
- `view-not-copy`, `hidden-allocation`, `generated-cpp-readability`: emission
  is unchanged and views are not admitted by analogy with record references.
- `const-source-const-loop-var`: consume final THIR access/storage facts;
  readonly is not evidence of exclusive access.
- `no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics`,
  `reject-valid-python-only-as-documented-divergence`,
  `no-warning-on-valid-code`: debug-only reports introduce no source rule.

Confidence is high for the bounded proposal after storage, conflict and
native/CPython surveys. Main risks are testing the wrong side of a write,
losing aggregate loans, exempting an unrelated holder, assuming external
disjointness, and calling a retention conflict a lifetime end. Positive write
facts, exact-result tests and the independent path oracle address these.
Keep changed-edge worklist scheduling; do not enumerate paths in production.

The probes also encountered existing THIR coverage rejection for initial
alias/copy bindings in the loop fixture; independent initial constructors
avoid that unrelated coverage gap. This batch does not expand it. Adjacent
already-filed issues include `BUGS.md#loop-if-hoist-alias-early-del`,
`BUGS.md#union-inline-slot-block-scoped-reseat` and
`BUGS.md#resumable-alias-identity`; none supplies permission to generalize this
backing model to cleanup, inline unions or resumable frames.

The implementation probes also found that a narrowed Optional-to-Optional
capture can lack the non-extracting read required by existing MIR coverage.
The source Optional loop test retains its initial alias while checking reuse
of the replacement backing. M3.4 adds repeated-site Optional
alias-retention witnesses using internal MIR; the source coverage gap remains
in TODO.md.
Readonly-parameter-to-reassigned-local probes likewise retain their existing
THIR/MIR gates; they do not justify widening this storage slice.

The implemented retention pass shares M3.2's place resolver and requires the
same immutable function instance for all analysis inputs. Its dump reports
event, affected storage and retained holder payload, or explicit uncovered
analysis. Exact expected conflicts and an independent concrete execution
oracle cover bounded loops, copied payloads and pre-write last use; separate
tests cover branch joins, external aliasing, inline fields and missing facts.
