# Remaining M2 audit and M2.13

Status: M2.13 implemented and verified on 2026-09-18 after approval of the audit
following M2.11 and M2.12 (`f56f424c7d`). Subsequent M3 work still requires its
own design approval.

The subsequent implemented increments are specified in
[MIR_M3_LIVENESS_PLAN.md](MIR_M3_LIVENESS_PLAN.md): backward liveness, then
forward reference dependencies. They do not yet provide scope-escape or
invalidation safety verdicts; `--dump-mir` is also implemented.
[MIR_M3_REUSE_PLAN.md](MIR_M3_REUSE_PLAN.md) records the approved next batch:
M3.3 backing reuse and M3.4 internal retained-object conflicts are implemented.

## Result of the audit

M2.2 already separates a plain record's backing storage from the local holder
that points to it. It models construction, copy, selected move, aliasing and
OWN versus IN_PLACE replacement. M2.13 must extend that model, not introduce
a second storage-ID system or repeat the alias analysis in MIR.

M2.13 adds **constructor-backed Optional record locals**.
It joins two existing pieces: M2.2 record storage and M2.4 nullable borrowed
payloads. Expression temporaries, owned aggregate backing, views and container
elements have additional placement, reuse or invalidation obligations; they
are separate work, not implicit additions to this slice.

M2 is not complete. However, implementing every remaining type and position
before starting M3 would postpone testing the actual lifetime analysis behind
an open-ended coverage expansion. The recommended sequence is M2.13, then a
design for bounded M3 liveness and holder/loan propagation on the existing
supported CFG. That is an intermediate analysis checkpoint, not a declaration
that the full M2 exit gate or callable admission requirements are satisfied.

## Requirements and remaining work

The six requirements in [CALLABLE_PROVENANCE_REQUIREMENTS.md](CALLABLE_PROVENANCE_REQUIREMENTS.md)
remain the acceptance gate. The following is an audit of implemented coverage,
not a renumbering of those requirements or a promise of one commit per row.

| Requirement | Present after M2.12 | Remaining obligation and dependency |
|---|---|---|
| Stable places | Body-scoped slots; separate plain-record storage; selected field/tuple/Optional/union projections; qualified scalar globals | Constructor-backed Optional locals are M2.13. Owned tuple/union backing, materialized expression temporaries, views, container structure/elements and broader globals need further M2 facts before M3 can analyze those bodies. Capture occurrence/source facts exist in THIR but are not executable MIR places. |
| Explicit operations | Scalar operations, alias/borrow, plain-record construct/copy/move/replacement, selected wrapper construction/copy/extraction | General calls, closure construction/body access, reference/aggregate returns, stores escaping into fields/containers and destruction need their own contracts. M2.11 callee identity and M2.12 capture inventories do not imply effects or lifetimes. |
| CFG and evaluation order | Existing ordinary free/method bodies and bounded constructor tails; branches, scalar loops, short circuits and scalar returns | M3 owns new binding scopes, cleanup, exceptional exits, suspension/resume, comprehension and match regions. Owning operations in loops remain excluded because an emitted site reuses storage. Eager effectful operands also retain the existing order gate. |
| Liveness and all-holder loans | Structural, definite-assignment and payload-selection validation only | M3 must introduce liveness and dependency propagation through every holder in its admitted subset, including aggregate copies and joins. Current presence facts are not loan/lifetime proofs. |
| Summaries and effects | Selected declarations have semantic identities/signatures | M4 needs finalized summaries, pending/opaque/known-empty distinction, call-site substitution and environment/global effects. Summary fixed-point orchestration stays in sema/workspace analysis. |
| Per-instantiation obligations | Open/generic execution remains uncovered | M4 must resolve forms and effects per instantiation and compare with monomorphic twins. No metadata-only descriptor grants generic safety. |

### What must precede M3

For any body M3 accepts, its MIR must describe all operations and storage
identities in that body, with explicit control flow and no guessed effects.
The existing all-or-nothing MIR coverage boundary supplies that prerequisite
for its bounded subset now; M2.13 extends it to the Optional examples below.
There is no technical dependency requiring all view/container/call/closure
families before starting liveness on plain records and supported aggregates.

Before implementing that first M3 slice, write and approve its transfer rules,
join behavior, loop convergence, scope-end model and independent safe/unsafe
witnesses. Include alias and aggregate holder copies and owner reseats in the
witnesses, so the exercise tests propagation rather than just scalar liveness.
Continue to exclude cyclic storage creation/replacement until repeated-site
identity and invalidation are modeled. No checker authority changes then.

The broader M2 rows above remain prerequisites for their corresponding M3/M4
coverage. Before M5, all required callable acceptance/rejection witnesses must
be supported soundly; an unsupported result cannot discharge an obligation.
This is a finite next checkpoint, not a claim of a known total iteration count.

## M2.13 example and invariant

Assume the ordinary `Cell` reference type has one `int32` field, `value`, and
an explicit constructor assigning its parameter to that field:

```python
def retained() -> int32:
    current: Cell | None = Cell(1)
    saved: Cell | None = current
    if current is not None:
        current.value = 7
    current = Cell(2)
    if saved is not None:
        return saved.value
    return 0
```

The result is 7. The shared mutation before replacement detects a silent copy
at `saved`; the later read detects accidental retargeting to the new object.
The annotations deliberately exercise Optional holders. The emitted shape is
an initial `Cell` slot plus nullable pointers, a separate backing slot at the
OWN reseat site, and a pointer update of `current` only. This already compiles;
M2.13 changes internal MIR coverage, not Python acceptance or generated C++.

**Invariant:** a supported constructor-backed Optional local is a nullable
holder of independently identified record storage; construction, selected
replacement and clearing the holder preserve the exact existing storage
operation without inferring safety from the Optional type or C++ spelling.

Classification: architectural, because producer facts, MIR lowering and
validation must agree on a new admitted combination of existing operations.

## Existing authorities and representation

| Authority | Reuse / bounded extension |
|---|---|
| `thir/lower/statements.py::_lower_opt_ptr_slot_decl` | Actual OPT_RVALUE producer distinguishes a constructor-backed local from OPT_NONE and storage-Optional call results. Stamp positive plain-record ownership evidence here. |
| `thir/lower/statements.py::_lower_stmt`, `thir/lower/storage.py::optional_layout` | Already attach the nullable payload/access facts. Preserve them alongside the new ownership evidence. |
| `THIRPtrLocalDecl.owned_storage`, `thir/validate.py` | Extend the existing fact to describe the record payload of the exact OPT_RVALUE constructor case. Validate kind, constructor result, Optional payload type and access together; do not generally unwrap wrappers to make mismatches pass. |
| `THIRAssign.rebind_storage`, `_rebind_storage`, `thir/emit.py::_own_slot` | Consume the effective OWN/IN_PLACE verdict, including any existing readonly fallback. An OWN slot belongs to a syntactic site, not to every execution of that site. |
| `mir/lower.py::_Coverage.record_value`, `MIRDefinitions` | Reuse complete constructor/layout verification: pure bool/int32 fields and parameters, no constructor body effects, bases or custom copy/move/destructor behavior. |
| `MIRSlotId`, RECORD_STORAGE, `MIRBorrow`, `MIROptionalConstruct` | Allocate a storage slot, initialize it, borrow it into a record-reference temporary, and construct the Optional holder from that reference. No parallel storage identity, new C++ counter or universal binding rewrite. |
| `MIROptionalPayload`, `MIRDeref`, `mir/presence.py` | IN_PLACE writes through the present payload's referent. Existing Optional construction establishes presence; validation must still reject absent/unknown payload access. |

At an OWN constructor reseat, construct distinct site storage and set only the
destination Optional holder to its reference. At IN_PLACE, evaluate the whole
constructor source before writing through `OptionalPayload -> Deref`; preserve
the reached storage identity and presence. At `None`, clear the holder without
destroying other holders' storage. Existing supported Optional/name copies and
borrowed payload assignments retain their behavior and access restrictions.

MIR admits OPT_RVALUE declarations only with positive ownership evidence and
a verified constructor. The Optional statement dispatch runs before plain-record
replacement and handles its own carried replacement verdict. The verifier
allows record replacement at an exact Deref or OptionalPayload/Deref destination,
not arbitrary nested projected writes. The test interpreter resolves the latter
destination's payload type and referent rather than treating its root slot as
record-typed. Other constructor sources still fail `optional_source` eligibility.

No parser, runtime, stdlib or C++ emitter changes are proposed. Existing sema
ownership selection stays authoritative. No source warning/error changes and
no existing snapshot refreshes are expected.

## Scope and sibling boundaries

The factors intersect; every factor must be covered for a body to be admitted.
The deferred cells remain in this plan and TODO.md until their own increments.

| Axis | M2.13 coverage | Deferred / reason |
|---|---|---|
| Position | Entry-prefix locals in already-covered ordinary free functions, methods and scalar constructor tails; acyclic reseats of those locals in existing branches | Module init, closures, generators/async, comprehension, with, try/finally, error-return and match regions remain M2/M3 gaps. Branch/loop-first declarations and all owning operations in cycles need M3 scope/reuse analysis. |
| Shape | Pointer-form Optional of a plain record satisfying M2.2 constructor eligibility; mutable owned payload and existing readonly borrow/copy restrictions | Owning Optional values/call results, Optional scalar/tuple nesting, value/native/inherited/protocol/generic records, union and tuple owned backing, str/bytes, Own, Ptr/Span, Box/Rc and containers remain separate M2/M4 work. Existing scalar/value and borrowed-wrapper support is unchanged. |
| Initialization | Constructor or null entry holder; constructor reseats carrying OWN/IN_PLACE, including annotation-only locals lowered with those facts; existing supported borrow/None sources | INLINE_RVALUE writes without a carried storage verdict may reuse per-name backing and remain excluded. No treating that reuse as a fresh OWN site. Calls and effectful constructor arguments remain excluded. |
| Slot | Local Optional holder, hidden record storage, record-reference temporary, copies to existing supported local holders, guarded scalar field access and scalar returns | Optional parameter replacement, reference/aggregate returns, Optional-valued field/container/global/capture stores and frame-backed slots stay excluded. Record parameters/receiver facts retain their existing gates. |

Union constructor backing needs variant storage and active-alternative identity;
its selected reseat behavior differs from Optional IN_PLACE. Owned tuple backing
needs per-element forms and reusable tuple-slot facts. Neither should be
implemented by extending this Optional gate implicitly. Materialized argument
and view temporaries additionally need correct placement and lifetime regions,
especially conditional operands; their identities must not be reconstructed
from rendered `__tmp`/`__slot` names.

## Tests, evidence and delivery

Independent emitted-THIR/MIR fixtures should cover:

- Shared mutation before OWN reseat, old-alias survival afterwards, and new
  object independence. Inspect referent identities and object count as well
  as return values; read-only values alone do not establish aliasing.
- Explicit None -> constructor, constructor -> None, then continued use through
  another holder; unaliased IN_PLACE with an RHS reading the old field.
- Different OWN sites in both branches, constructing only on the selected edge;
  existing Optional copies, readonly access and plain-record behavior unchanged.
- Free function, method and constructor-tail positions using the shared producer.
- Malformed ownership/type/access facts; absent-payload IN_PLACE; unsupported
  inline backing without a storage verdict, loops, custom/effectful constructors, owned
  wrapper calls, sibling wrappers and unsupported body kinds. Negative tests
  must reach the intended boundary, not fail earlier for an unrelated return.

Reuse deliberate native witnesses where sufficient:
`records/alias_rebind_no_warning` includes Optional IN_PLACE followed by alias
capture and None clearing. `optional/opt_ptr_slot_reseat_shapes` pins
annotation-only writes; those with carried OWN/IN_PLACE facts use this model,
while legacy INLINE_RVALUE nodes remain MIR-uncovered. Inspect
existing cases for the pre-reseat shared mutation/OWN distinction; add one
condensed CPython-compatible case only for missing deliberate coverage. Compiler
unit tests keep independent source fixtures, never read these case files.

Design probes compiled to emitted THIR/C++ for OWN replacement with shared
mutation, None clearing, IN_PLACE replacement and explicit None -> constructor.
An independent parity assessment executed the revised source under CPython,
producing `7 7 5 3`, and inspected corresponding C++ storage operations. Native
execution was not run for this doc-only proposal. Implementation gates must run
the native witnesses and compare MIR behavior; code inspection is not that gate.

Pitfalls: silent-copy-vs-alias and copy-warning-at-wrong-site are checked with
mutation/identity witnesses and unchanged diagnostics. Tuple-equals-scalar and
same-construct-every-position use the explicit sibling/position gates above.
Conditional operands stay excluded unless existing MIR handles their placement;
branch construction stays inside its edge. Generic-equals-monomorphic-twin,
view-not-copy and const-source-const-loop-var remain excluded-family guards.
Hidden-allocation and generated-cpp-readability require unchanged emission.
The diagnostic rules (no C++ or internal names, no undocumented source rejection,
no spurious warnings) are protected by an internal coverage-only change.

Implementation order: producer fact and THIR validation; MIR coverage/lowering;
projected-write verification and interpreter; independent fixtures and native
coverage audit; update LANGUAGE_FEATURES/ARCHITECTURE and this plan. Then focused
tests, all applicable specialist reviews and readiness/retrospective, a full
forced remote suite, and one squashed M2.13 commit. Leave it on a branch for the
user to merge. Any new source behavior or different storage policy returns to
design review. Existing snapshot changes require consultation before refresh.

Confidence: high in the bounded implementation after emitted fixtures, native
witnesses, specialist reviews and full-suite verification. Existing
adjacent Optional tuple readonly and owning-wrapper defects remain under their
current exclusions; this audit established no new defect requiring a fix.

Implementation evidence corrected one survey assumption: an annotation-only
declaration followed by constructor writes can lower to an OPT_NONE holder and
THIRAssign OWN/IN_PLACE operations, just like explicit None initialization.
Coverage follows those semantic facts. The distinct INLINE_RVALUE operation
still has no supported storage verdict and remains excluded; no source-spelling
flag or separate storage policy is needed.

## Delivery verification

The final focused IR/native gate passed 942 tests. The full forced remote suite
passed 8,779 tests with 23 skips, including 4,159 native cases built and run.
Existing snapshots were unchanged; the new Optional alias/reseat case has empty
diagnostics and matching native/CPython output. Independent MIR tests establish
the analysis behavior, while the native case checks the modeled source behavior.

All seven specialist lenses and the readiness/retrospective gates closed clean.
The full compatibility gate exposed a missing producer restriction for inherited
upcasts: OPT_RVALUE can initialize a Base slot from a Child constructor. Such a
declaration must remain unstamped, so the producer now requires exact constructor
result/pointee agreement before recording ownership. Independent emitted-THIR
tests pin declaration and reseat exclusions, and the repeated full suite passed.

The delivery is one squashed commit on a branch for user merge. No checker
authority, source admission or C++ emission changed. The next proposed work is
the separate bounded M3 design described above; broader M2 coverage remains open.
