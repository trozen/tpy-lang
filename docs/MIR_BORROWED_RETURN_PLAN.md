# MIR borrowed-return summaries

M4.5 leaf extraction and M4.6 caller propagation extend the M4.3/M4.4
call-effect interface. This architectural extension supplies
another M3 W5 prerequisite; it does not complete escape analysis or change
production checking, source acceptance, diagnostics or C++ emission.

The subsequent [borrowed-argument storage batch](MIR_BORROWED_ARGUMENT_STORAGE_PLAN.md)
extends the original stable-actual boundary below with verified named
constructor backing. Source acceptance and the M4.5/M4.6 summary contract
itself are unchanged.

## Contract

For the existing hook-free record with bool/int32 fields:

```python
def choose(flag: bool, a: Cell, b: Cell) -> Cell:
    return a if flag else b

saved = choose(flag, first, second)
```

Existing C++ returns `Cell&` and binds `saved` by reference. The summary must
record possible return origins `{1, 2}`. In the caller, `saved` depends on the
current referents of `first` and `second`, not on the callee's parameter slots.
Reseating either intermediate holder later must not change `saved`'s origins.
Readonly results keep the same origins while withholding mutable access.

Invariant: every covered borrowed result preserves all possible storage origins
and the selected return access; missing, local or unsupported origins never
become a known empty dependency set.

## Evidence and precedent

The design probe compiled and ran direct conditional selection, forwarding,
mutable result writes and explicit readonly observation using `@nocopy Cell`.
Both selection arms matched CPython after changing the original records and
after writing through the result. Emission uses `Cell&` and `const Cell&`.

- `thir/lower/statements.py` already selects record-return ternaries,
  forwarded borrow calls, borrow-call declarations and pointer reseats.
- `THIRResolvedCallee` supplies exact qualified identity and signature; its
  producer excludes method/generic/native/callback/frame callees.
- `MIRDefinitions` proves the existing supported record layout and hook rules.
- `MIRCallSummary.returns` carries `MIRReturnOrigin(parameter, path)`: a path
  into a parameter on the alphabet of `MIRParameterWrite.path` (an empty path
  is the whole parameter); see
  [projected return origins](MIR_ANALYSIS_PLAN.md#projected-return-origins).
- `analyze_dependencies` and `resolve_referents` already propagate aliases
  and conservative branch joins. Return operands already participate in liveness.
- Workspace scheduling already handles ordinary forward/imported definitions
  and leaves recursion or incomplete definitions Opaque.

Sema's `return_borrows_from` remains authoritative in its existing production
domain. It is not the proof source for these MIR summaries: the local extractor
must account for every reachable MIR return. A legacy missing-to-empty adapter
must not supply evidence here.

## Representation and phase ownership

One new THIR fact is necessary: optional `borrowed_result: THIRBorrowedRecord`
on `THIRCallableSignature`. Produce it at resolved-callee selection, shared by
the definition and call. A supported borrowed return combines its explicit
readonly type with the declaration's `is_readonly`, matching the emitted free
function signature. Declared type alone misses `@readonly` free functions.
Do not substitute `FunctionInfo.is_readonly`: registration also sets that for
`@pure`, while the emitter uses the declaration flag. Pin both spellings.
Own/value/unsupported results do not receive a borrowed-result certificate.

No independent THIR function/call result fields or C++ spelling inspection are
needed. Existing declaration form, `is_const`, pointer-slot kind and registered
destination access describe local bindings. Keep `THIRAliasBinding` for its
existing name-to-name purpose; do not encode call expressions as source names.

MIR lowering copies the selected result fact onto `MIRFunction` so standalone
validation retains emitted return access without a THIR/workspace lookup.
This transports one decision across IR phases rather than re-deriving it.
MIR calls read the same fact from their summary's selected signature. Existing
THIR free-call nodes use `Form.VALUE` even when the selected signature returns
a reference; result shape comes from that signature certificate. Borrowed
names and conditional expressions still require their existing borrow form.
Normalize the declared
reference wrapper when matching holder types while preserving exact signature
identity and access checks. Extend the shared borrowed-expression path for
names, same-type conditional selection and known calls; selection assigns
aliases on CFG edges, never scalar copies. Use that path at returns and local
bindings/reseats. Scalar and void behavior stays unchanged.

For a borrowed result, summary validation requires a nonempty set of in-range,
same-record borrowed parameter indices. A mutable result cannot name a readonly
origin. Scalar/void results require empty return dependencies. A valid shape
alone is not evidence that a summary is complete.

The producer inspects dependencies immediately before every reachable return,
unions external whole-parameter origins and preserves existing scalar-field
write extraction. Unknown, projected, callee-local or fresh origins make the
summary Opaque. Acyclic normal-return restrictions remain. A read of `b.value`
before returning `a` must not add `b` to the returned roots.

The caller's dependency transfer substitutes each returned parameter index
through its actual holder at the call point. Resolve before overwriting the
destination, including `saved = identity(saved)`. Repeated actuals deduplicate;
different external roots retain the existing conservative overlap semantics.
Keep substitution with dependency resolution (or a shared lower-level helper),
so `dependencies.py` does not import its `call_effects.py` consumer. Forwarding
summaries inspect the resulting states rather than implementing another solver.

Audit all MIR consumers that assumed scalar call/return results: verifier,
builder, definite assignment/presence, liveness, dependencies, storage,
retention, scope lifetime, dump and test interpreter. Existing storage events
must see the new holders; a content write does not invalidate their backing.
This batch adds no escape safety verdict at function exit.

## Scope matrix

The following axes are factored: a combination is covered only if every axis is
covered. All excluded cells stay explicit coverage failures, not source errors.
The deferred groups are tracked by the M3 completion and general M4 effect plans.

| Axis | Covered | Deferred owner |
|---|---|---|
| Callee | Resolved ordinary nongeneric synchronous free definition, acyclic normal flow; direct/forward/imported spelling | Methods/static methods/constructors, generics, native/protocol/callback contracts and recursion: M4; captures: W5; frames: W4 |
| Caller position | Existing ordinary free/method/constructor-tail bodies; supported if/while/for control flow | Module, comprehension, match: W2; closure: W5; with/try/finally/error-return: W3; generator/async: W4 |
| Result shape | Whole same-type plain borrowed record, mutable or readonly; existing scalar/void forms | Tuple/singleton, Optional/union, str/bytes views, Own, Ptr/Span, Box/Rc, containers, nested records: M2/M4/W5 |
| Parameter shape | Existing bool/int32 and borrowed plain record parameters | Richer shapes: M2/M4/W5 |
| Result holder | Local declaration/reseat, expression result, return; ordinary existing storage may back caller actuals | Field/container/global/capture stores and return projections: M2/M4/W5 |
| Return source | Parameter, alias/reseated alias, conditional choice, known forwarded result | Owned/fresh/callee-local roots and environment/global roots: M4/W5 |
| Actual/evaluation | Stable named borrowed record actuals and existing stable scalar inputs; sequenced calls and conditional arms | Temporary record actuals for borrow-returning calls, nested effectful arguments, direct call-result projection: later storage/sequencing work |
| Effects | Existing scalar-field writes compose with returned dependencies | Storage replacement/structural invalidation and retention: M4/W5; exceptions/nonreturn/cleanup: W3 |

Existing tuple/Optional/union holders may coexist under prior coverage, but
this batch does not add their call signatures or result construction. Tuple
language completeness remains a separate workstream. Constructor callers use
their existing supported argument/storage forms, not a new constructor ABI.

## Tests and pitfalls

MIR unit tests compile their own source and assert exact return sets, caller
origin externality and projection shape, access and holder lifetime; they do not
read snippet fixtures.
Cover direct/conditional/multiple returns, alias reseats, transitive and
imported forwarding with permuted parameters, repeated actuals, read-vs-return
root independence, writes plus returned aliases, readonly and decorated
signatures, caller loops and method/constructor-tail positions.

Prove that an alias returned through an intermediate holder survives that
holder's reseat and stays visible to existing retention/scope analysis. Include
internal MIR storage-end/replacement witnesses where source coverage prevents
isolating the analysis. Malformed signatures/indices/access are validation
errors; unsupported roots, results, temporaries, unknown callees and recursion
are Opaque/not covered. Keep those categories distinct.

One condensed CPython-compatible source case is justified: existing ordinary
borrow-return cases mostly read, so they do not pin the conditional plain-record
boundary against silent copies. Use `@nocopy`, distinct owner mutations and
result writes, both flags, readonly observation, reseats and caller positions.
Section names identify each output witness. Existing snapshots remain unchanged.

Pitfall disposition:

- `silent-copy-vs-alias`, `copy-warning-at-wrong-site`: mutation witnesses and
  reference emission checks; no owning conversion or new warning.
- `tuple-equals-scalar`, `same-construct-every-position`,
  `generic-equals-monomorphic-twin`: explicit matrix exclusions and coverage
  boundary tests; no new source restrictions for excluded siblings.
- `conditional-operand-evaluates-in-place`: existing CFG guards, both-arm
  witnesses and unchanged eager-effect fences; may-origins do not execute arms.
- `view-not-copy`, `hidden-allocation`: no view/owning result admission or emitter
  changes; inspect new C++ for references and no unexpected materialization.
- `const-source-const-loop-var`: returned access is checked separately from
  provenance; existing iteration access rules remain unchanged.
- `runtime-template-kind-matrix`: not applicable; no runtime templates change.
- `generated-cpp-readability`: inspect the new case's snapshot.
- `no-cpp-in-diagnostics`, `no-internal-names-in-diagnostics`,
  `reject-valid-python-only-as-documented-divergence`, `no-warning-on-valid-code`:
  no source diagnostic/acceptance changes; uncovered is an analysis status.

Known adjacent issues remain separate: `BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling`,
`BUGS.md#augassign-call-receiver-double-eval`,
`BUGS.md#getter-source-const-not-tracked`,
`BUGS.md#nested-def-return-validated-against-enclosing` and
`BUGS.md#finally-mutate-then-rebind-return`. The initial batch avoids those
boundaries.

An additional probe found that a free `@readonly def keep(cell: Cell) -> Cell`
emits `const Cell&`, but `saved = keep(cell)` binds `Cell&` and fails the C++
build. Tracked as
`BUGS.md#readonly-free-return-binding-drops-const`. The result-access fact must
reflect the emitted const return; a mismatched caller stays uncovered.
Explicit `readonly[Cell]` return
annotations work in the probe and supply the positive readonly witness.
The decorated definition's parameter signature/facts also fail the existing
exact summary-identity check, so its summary remains Opaque; this batch does not
relax that check to admit the inconsistent source boundary.

Mixed mutable/readonly record ternaries are rejected before MIR
(`BUGS.md#readonly-record-ternary-join-rejected`). Separate typed return
branches supply the mixed-access origin-join witness without changing that
frontend boundary.

## Dependency split

1. **M4.5: borrowed result contract and leaf summaries.** Shared THIR signature
   fact, borrowed return lowering/validation, leaf extraction and boundary tests.
   Return consumers, including dump and interpreter, preserve reference identity.
   These producer facts are the prerequisite for call consumption.
2. **M4.6: caller holders and forwarding.** Result substitution, local binding
   and reseat support, forwarded writes/return roots, call-result dump and
   analysis-consumer integration, condensed source witness and negative coverage.

Risk is concentrated in missed scalar-only consumers, access projection and
loss of origins during a join or destination overwrite. Reuse finite referent
sets and the existing workspace schedule; no new interprocedural fixed point.
