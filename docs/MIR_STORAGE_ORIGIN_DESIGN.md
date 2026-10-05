# Storage origins and borrowed-result admission

Status: approved architecture; steps 1-2 (shared backing facts and internal
MIR evidence bound to THIR) are implemented. Production authority is decided
only after measuring its coverage. This is the shared lifetime contract for
[borrowed-argument storage](MIR_BORROWED_ARGUMENT_STORAGE_PLAN.md).

The [targeted coverage audit](MIR_STORAGE_COVERAGE_AUDIT.md) records the
internal API's original and post-operation-proof boundaries. Its sample is
not corpus-wide compatibility evidence. The
[operation-proof extension](MIR_BORROW_OBLIGATION_PLAN.md) adds explicit
supported plain-record sink obligations and parameter-origin discharge.
The [full-expression correspondence extension](MIR_FULL_EXPRESSION_EVIDENCE_PLAN.md)
connects already-modeled constructor storage to the evidence adapter. The
[select-slot extension](MIR_SELECT_STORAGE_PLAN.md) adds shared placement and
bounded mixed plain-record ternary evidence without production authority. The
audit preserves the pre-extension measurement alongside its follow-up.

## Observable contract

```python
def observe(cell: Cell) -> readonly[Cell]:
    return cell

def read(choice: bool) -> int32:
    saved = observe(Cell(1)) if choice else observe(Cell(2))
    return saved.value
```

The selected constructor executes once, at the selected arm. `saved` aliases
that object; it neither copies it nor extends its storage lifetime. C++ keeps
the existing optional backing and reference/pointer bindings.

Every use of a borrowed value must occur while every possible storage origin
it depends on is alive. Copying a holder, returning it from a helper, or
embedding it in a supported payload preserves that dependency. Overwriting
one holder does not erase dependencies retained by another.

For example, copying a block-local result into an outer holder and reading it
after the block must not be newly admitted when its backing dies at block exit.
This holds for direct copies, alias chains and call-mediated transfers alike.
The check follows storage, not the spelling or declaration depth of a holder.

## Representation and phase ownership

THIR owns physical materialization: backing identity, declaration and
initialization anchors, owning region, and the operations that destroy or
replace it. MIR owns propagation through the CFG and uses those physical facts.
The emitter consumes the same placement facts. Sema continues to own types,
selected access and resolved callable identities.

Reuse `THIRTempPlan` / `THIRTempPlacement`, MIR places and emitted regions,
`MIRDependencies`, known call summaries, and existing storage/payload/end
inventories. Do not create another name-depth map or a second THIR dataflow
checker. Do not infer storage from generated C++ text.

The conceptual origin is a body-local storage place, including its payload
projection and physical activation. An external parameter origin remains
distinct from private local backing; external origins need not be disjoint.
Unknown provenance is not an empty origin set.

Static places suffice for the first bounded proof only if each named backing
initializes once per verified activation and every retained dependency is
checked before that activation ends. Region reset may clear dead local
holders, but must not erase an outer holder's dependency on an ended object.
Reinitialization/replacement that violates this condition needs an explicit
generation/freshness proof or remains uncovered. No runtime generation counter
or allocation is introduced.

The contract includes all materialized storage conceptually, not just
`THIRArgTemp`. `THIRSlotEmplace` for mixed reference ternaries is a sibling
producer. Its inventory entry lacks planned placement, so a body containing
it cannot receive this certificate. Mixed reference ternaries compile
without this certificate.
The select backing has an escaping-alias defect
([select-slot-alias-escape-unchecked](../BUGS.md#select-slot-alias-escape-unchecked)):
`saved = owner if choice else Cell(2)` inside a branch, followed by
`outer = saved`, leaves `outer` pointing into destroyed storage. Therefore
compatibility is not a safety endorsement; extending enforcement to this
existing producer needs an explicit compatibility decision too.

## Admission obligations and evidence

At every newly admitted borrowed-expression route, record a typed source
admission obligation on the body/operation independently of temporary planning.
It must survive `prepare_temporaries` returning `None`; no plan then means
Not covered, never no obligation. Link it to physical backing placements when
available. Include inline full-expression temporaries: absence of a named
`THIRArgTemp` is not evidence that no lifetime proof is needed. Lowering may
discharge a storage-free operation only with positive stable-origin evidence.
The marker must survive lowering and be validated against the exact body and
placement plan; losing it is an invariant failure, not permission to emit.
This is a production compatibility boundary, not a runtime feature flag.

The implemented inventory is narrower than this future admission contract:
it records named argument, select-slot and inline full-expression constructor
backing, plus non-stable borrowed local declarations and `PTR_ADDR` sinks.
The sink inventory omits `THIRAssign` reseats and borrowed returns; the backing
inventory also omits storage producers. Its entries are not exhaustive
source-admission markers. The internal adapter proves all recorded backing
roots through whole-body MIR;
it does not discharge each obligation individually. The production gate must
first complete and consume those obligations and admission-route markers.

A reusable MIR evidence API returns one of:

- **Certified:** complete evidence for every required backing origin.
- **Conflict:** an origin can be used or escape after its backing ends.
- **Not covered:** some required operation, origin, lifetime or effect is unknown.

A certificate is keyed by the actual emitted THIR object and generation-request
identity, with its placement plan and callee summary inputs. Display names,
source locations and counter-based debug body IDs are not cache identities:
specialized clones and constructor wrappers can share those descriptions.
A modified body or different instantiation cannot reuse a certificate.
Malformed IR remains an internal validation failure, not an uncovered result.

Certification requires all of the following:

1. Complete supported caller CFG and holder/payload propagation, including
   joins, aliases, calls, reseats and every reachable exit.
2. Complete backing placement, initialization and selection facts.
3. No live dependent holder crossing a backing's scope end, including loop
   back edges, `break`, `continue` and early return paths.
4. Applicable replacement, payload-end and freshness checks.
5. Explicit escape checks: at each reachable return, resolve every returned
   borrowed leaf. Any non-external origin conflicts; a missing origin is
   uncovered. Function exit has no CFG successor, so empty
   successor liveness is not evidence that returning a borrow is safe.
6. Known callee effects, including no unmodelled retention or invalidation.
   Opaque/recursive/native calls and unsupported stores/captures make the body
   uncovered; omitting an operation from a selected slice is not a proof.

The first version requires whole-body coverage. It does not analyze a
hand-selected subgraph around the candidate. Existing summary extraction
already proves its bounded `retains == empty` contract from known bodies;
absence of a summary must never be interpreted the same way.

Completeness is checked before interpreting empty conflict lists. Every live,
engaged borrowed leaf must have known non-empty origins; an absent payload is
established by explicit selection facts, not a missing map entry. Every ending
optional backing and every replacement requiring engagement must have an
explicit engagement fact. Missing engagement cannot silently suppress an end
or replacement check. Any applicable freshness issue prevents certification.
These requirements strengthen the current advisory APIs, which sometimes
omit events when evidence is missing.

Repeated initialization must be excluded by source coverage before strict MIR
validation, or reported through a dedicated located Not-covered result when
the supported lifetime model cannot represent it. Do not catch all validation
errors or reinterpret malformed MIR as a source limitation.

The bounded normal-flow proof may allow unwinding directly out of the function
only when every retained holder has trivial cleanup, backing is hook-free,
calls cannot retain dependencies, and no handler/finally/context-manager can
observe them while unwinding. If any prerequisite is missing, exceptional
flow is uncovered. Merely having a normal-return summary is insufficient.

## Production boundary and compatibility

Enforce obligations in shared compiler orchestration before returning or
publishing generated C++ or invoking a build. CLI-only enforcement would leave
library calls, the REPL and extension generation unprotected.

Current THIR caches are populated during code generation. Refactor the shared
orchestration to collect/buffer the actual emitted THIR and output, finalize
required workspace summaries, certify obligated bodies, then release output.
Use the same artifact for debug dumping and production checking. Avoid an
independent second lowering walk; existing cache side effects and module
dependencies must remain coherent. Collect required callee modules regardless
of the debug CLI's current user-module display filter.

Provide one generation-request API for the required module set, including
cycle peers. Its immutable artifact bundles C++ text, actual THIR and completed
certificates. Per-module file/string APIs delegate to that request; verbose
printing and subsequent writing consume its same output instead of generating
twice. REPL and extension paths use it too. Artifacts live for that request,
not across recompilation or mutable compiler-state changes. An opaque stdlib
callee is an explicit coverage limitation, not a missing-module success.

Only new admission obligations acquire this hard gate. Existing accepted
programs without obligations retain their current policy, including separately
tracked defects. A certificate is not a claim that the entire legacy program
is lifetime-safe. Existing diagnostics are not suppressed as a side effect.

For an obligated body, `Conflict` rejects the unsafe transfer/use and identifies
its source backing. `Not covered` rejects the new form with a located coverage
diagnostic, not a claim that the source is unsafe. Neither may fall through to
unchecked emission. Diagnostics use source names and TPy types, never MIR slot
IDs, C++ temporary names or internal rejection codes.

This is a bounded M5 authority decision ahead of full M3/M4 completion. It does
not replace the existing checker or establish a general exclusive-borrow rule.

## Initial coverage and its cost

The internal proof uses bounded MIR coverage. A combination is
certifiable only when every axis below is covered; no omitted axis grants
permission.

| Axis | First certifiable slice | Uncovered obligations |
|---|---|---|
| Caller positions | Ordinary free functions, instance methods, fully initialized constructor tails | Module, generic/overload bodies, comprehensions, closures, match, context managers, try/finally, error-return, generator and async bodies |
| Control flow | Existing supported if/while/range/native-loop CFG with verified storage regions | Missing cleanup, exceptional or suspension edges; unsupported loop/operand forms |
| New argument backing | Verified named hook-free bool/int32-field record constructors, readonly argument access, ordered stable scalar operands | Mutable argument access, hooks/nonmovable records, nested effects, inline full-expression arguments, richer constructor/argument shapes |
| Callees | Known ordinary nongeneric synchronous free functions with bounded summaries | Method/static/constructor calls as callees, callbacks, native/protocol calls, recursive or opaque summaries |
| Holders | Local borrowed records and existing completely modelled tuple/Optional/union payload leaves | Arbitrary nesting, container-held references, str/bytes views, Ptr/Span, Box/Rc, escaping environments |
| Destinations | Local aliases/reseats; verified parameter-derived return leaves | Candidate-local borrowed returns conflict; field/container/global/capture stores need explicit escape modelling |
| Owning/value siblings | Existing scalar/value operations with complete effects | Owning aggregate transfer or copy semantics are not inferred from borrowed-record rules |

A simple safe scalar-returning function is covered; adding
`print(saved.value)` currently makes temporary planning incomplete.
Container/Optional/Own actuals and custom nonmovable records also exceed this
slice. Those forms cannot certify until coverage is added through this same
contract. Existing accepted programs need a separate compatibility measurement
before any production gate applies to them.

Ordinary MIR coverage means the body has a complete representable IR, not
that its lifetimes are safe. Workspace analysis and `--dump-mir` can expose a
covered body with a storage conflict; only the evidence API evaluates that
proof. A caller's local backing is private storage to its own summary
(`docs/MIR_ANALYSIS_PLAN.md` "Owned record results"): the summary publishes
nothing for it, so this coverage extension publishes no unchecked facts.

## CPython semantics and explicit alternatives

CPython keeps the referent alive through `outer` after an `if` ends. Rejecting
that form under bounded inline storage is a documented lifetime limitation.
Accepted forms must preserve identity, mutation visibility and lazy evaluation;
no implicit copies, heap promotion or blanket scope hoisting are introduced.

An explicit owner in an enclosing scope can provide sufficient lifetime when
earlier construction is intended. Alternatively, consume the borrowed value
inside its backing's scope. These are not universal equivalence-preserving
rewrites: moving a side-effecting conditional constructor earlier changes
behavior. Diagnostic remedies must be individually compile-tested; do not
recommend `copy()` for `@nocopy` values or claim a universal workaround.
Coverage rejections of safe Python forms must be documented as incomplete
support, separately from proven lifetime conflicts.
The tempting rewrite that merely names the chosen constructor inside the
branch (`cell = Cell(1) if choice else Cell(2); outer = observe(cell)`) currently
rejects at local binding; it is not a working remedy.

Conditional promotion of only the backing declaration, retaining construction
inside its selected arm, is a legitimate alternative to rejecting some branch
escapes. It is deferred as a storage-placement transformation, not dismissed
as impossible: its cleanup timing and repeated-activation behavior need a
separate proof. A shared slot cannot preserve two simultaneously retained loop
activations. The proof certifies existing placement before changing it.

Conflict uses the existing source-language wording "reference ... may outlive
its storage", with locations for the originating constructor and escape/use.
Document that bounded inline-storage rule in LANGUAGE_FEATURES when the gate
lands. Not covered uses the established "not yet supported" category, with a
located explanation of the missing lifetime support and an `error_` tripwire;
it is not labelled unsafe. Add the coverage gaps to the M3/M4 backlog.
Both classes therefore satisfy `reject-valid-python-only-as-documented-divergence`.

## Delivery and validation

Steps 1-2 are approved and implemented. Step 3 remains a separate approval
decision: source admission cannot expand before its production checker exists.

1. **Shared backing facts and obligations.** Preserve existing source admission
   and publish validated backing facts and borrowed-sink obligations.
   Implemented as `THIRStorageFacts` (`thir/storage_facts.py`), published on
   every lowered function, constructor and module-init body beside its
   optional temporary plan (resumable frames publish none yet):
   - A `THIRBacking` per `THIRArgTemp` (linked to its plan placement, whose
     scope and declaration/initialization anchors are the physical region),
     `THIRSlotEmplace` and full-expression constructor, keyed by THIR
     identity, with its holder statement or initializer cell, full
     expression and location.
   - An explicit uncovered reason where the physical facts are incomplete:
     no or partial plan, an unplanned select slot, storage inside a nested
     body. A missing plan is never an empty inventory; `None` facts mean
     unpublished.
   - A `THIRBorrowObligation` per supported plain-record binding/reseat,
     including stable-looking names and fields, plus eligible free-function
     borrowed returns selected by the resolved callable contract. Previously
     recorded unsupported sinks remain visible. Each obligation lists the
     backings evaluated in its value; an empty list is not lifetime evidence.

   This bounded inventory includes existing accepted forms. Production
   admission still needs complete coverage and route markers.
   Consumers validate facts by recomputing them against the exact body, plan
   and initializer cells, including the exact selected return contract.
2. **MIR lifetime evidence.** Include the existing returned-argument dependency
   work; add the reusable result contract, explicit return escape proof and
   complete-root checks. Keep this internal until production integration exists.
   Implemented by `mir/storage_evidence.py` and `mir/storage_adapter.py`:
   - `MIRStorageRequest` captures the exact function or constructor, storage
     facts, placement plan, verified definitions and an immutable snapshot of
     the callee summary map. The ordinary MIR builder publishes its actual
     identity-keyed backing places and final borrowed writes/returns in the
     same lowering pass.
   - `certify_thir_storage` validates that inventory and binds the evidence
     to this request, exact THIR object and exact MIR function. Reusing equal
     names, counter IDs, copied functions or a new plan cannot reuse a proof.
   - Named argument storage, bounded plain-record ternary slots and
     already-modeled full-expression constructor storage can certify for
     covered free functions, methods and constructor tails. Full-expression
     backing maps directly to its expression-region root without a temporary
     plan. Select slots require shared placement, hook-free movable
     bool/int32-field constructors and stable scalar operands, with one
     emplacement per declaration activation. Repeated while-head emplacement,
     record and/or and source wrapper sinks remain uncovered. A missing
     required temporary plan or unpublished facts never clears obligations.
   - The known plain branch-local select alias escape is an internal Conflict
     with no gaps. Retained tuple/Optional/union witnesses connect to the same
     actual select root internally; they do not add aggregate source coverage
     or fix the production defect.
   - No backing and no obligation is reported separately from unpublished
     facts and from a borrowed-expression obligation with no modeled origin.
     Empty inventory receives no vacuous certificate. Parameter-only
     operations can certify without new backing or a temporary plan.
   - `MIRBorrowEvidence` demands a nonempty set of exact operation points and
     retains their origins, explicit backing roots and reached local roots.
     Its exact-demand query cannot be used as a roots-only certificate.
     Writes resolve the complete post-write dependency state, including dead
     destinations; returns resolve their operand at the terminator. Missing
     or pruned operations stay uncovered. `MIRStorageEvidence` retains its
     separate nonempty-root API, with one shared whole-body computation.
   - Repeated initialization within one region activation has a dedicated
     validation exception carrying its initialization location. Source
     lowering reports that bounded proof failure as Not covered; malformed
     hand-built MIR still fails validation.
   - Scope, replacement, payload and explicit return escapes compose the
     existing analyses. Missing engagement at a feasible write (including an
     entirely missing point) blocks certification. Freshness issues also
     block certification; they can currently hide a more specific conflict
     behind Not covered because strict replacement/effect passes cannot run.
3. **Production gate and covered source forms.** Buffer and certify through
   all compiler entry points, then enable only the approved slice. Add broader
   coverage before expanding the advertised source surface.

Steps 1-2 establish the reusable internal proof without making
the current narrow coverage a source-language requirement. Step 3 requires a
compatibility inventory for both new forms and existing materialized
borrow/select forms. Its explicit decision is whether to ship the narrow new
admission gate first (leaving known legacy defects open), or expand coverage
before enforcing the shared contract on existing producers. Coverage must be
measured before enabling production authority; blanket rejection of current
safe programs is not authorized.

Tests pin direct/call/chain escapes, aliases surviving holder reseats, joins,
dead aliases, aliases of durable parameters, returns, loop activation and
replacement. Use internal IR tests for facts and uncovered channels; do not
execute dangling C++. One condensed positive source case must mutate through
shared identity or use `@nocopy`; representative negative cases carry located
diagnostic annotations. Include a safe but uncovered body and an inverse
temporary-free body to verify the compatibility boundary. Verify output APIs,
file generation, REPL and extension paths cannot publish an uncertified body.
Production-entry-point verification belongs to step 3, not the internal API.
Pin byte-identical output for already-accepted single-arm named-argument
bindings. Use snapshots and THIR initialization anchors to verify selected-arm
construction: adding a printing constructor/callee would itself exceed the
initial effect coverage. Compile-test a hook-free `@nocopy` witness so aliasing
tests cannot pass through silent copies.

Pitfalls: `silent-copy-vs-alias`, `view-not-copy`, `hidden-allocation` and
`copy-warning-at-wrong-site` forbid changing storage semantics to appease the
checker; `conditional-operand-evaluates-in-place` pins selected-arm execution;
`tuple-equals-scalar`, `same-construct-every-position` and
`generic-equals-monomorphic-twin` require the explicit coverage matrix above.
`const-source-const-loop-var` preserves access independently of provenance.
`runtime-template-kind-matrix` is unchanged because no runtime templates are
added. Generated-code readability and all diagnostic pitfalls are checked by
source snapshots and compile-tested remedies.

Existing escape and spurious-warning defects remain open until their full
advertised scope is fixed. Internal certification does not change their
production behavior.
