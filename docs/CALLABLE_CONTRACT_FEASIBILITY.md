# Callable contract: checkpoint 3 findings

Measured on 2026-09-17 at `d9c5173358`, after the two representation-reader
refactors. This is a design investigation, not an implementation of A1-A12.
No compiler, runtime, library, acceptance rule or expected snapshot changed.

**Recommendation, awaiting a sequencing decision:** bring the shared
analysis-only MIR work forward before the coupled callable contract. The
representation/form machinery is feasible, but the proposed admission layer
needs substantial new provenance/effect propagation and rejects ordinary safe
callback code. Those are the exit conditions in the contract document's
compatibility gate. Do not weaken the safety obligations to preserve the old
schedule, and do not treat these projected rejections as approved language rules.

## What was measured

Three independent investigations covered descriptor metadata, generic-form
propagation, and existing admission/loan facts. Focused programs were then run
through CPython, TPy emission, and native execution where safe. Generated C++
was inspected at the alias/capture boundaries. The generic-form prototype is a
standalone finite-state model, not a patched compiler or a lifetime checker.

The baseline contains **13 selected programs**: 12 pass semantic analysis,
10 emit C++, and 9 safe programs build, run and match CPython. The tenth emitted
program contains a dangling receiver capture and was not executed natively.
The remaining three programs already reject: two during lowering, one during
semantic analysis. CPython runs all 13.

Of the nine parity-matching programs, **eight would reject under the literal
proposed admission rules** and one is an acceptance control. This is a
source-rule projection backed by inspected source, current compiler facts and
emission, not an observed verdict from a new admission implementation. In
particular A4 treats invocation through a callback as unknown-effect; propagating
the particular callback's harmless effects would require additional analysis.
The sample deliberately targets the restrictions and is not a population rate.

Static discovery found 134 of 6,144 `tests/cases/*/*/src/main.py` files spelling
`Fn[` or `Callable[`. That excludes implicit callbacks, aliases and other source
files. It is a discovery count, not a corpus rejection census or release-impact
estimate. A complete compatibility measurement remains open.

## Baseline and projected admission

`ok` in the emit column includes successful semantic analysis. Output is the
same under TPy and CPython except for the explicitly unexecuted rows.

| Probe | Emit / native | Output | Projection and reason |
|---|---|---|---|
| Scalar callback while `q = xs[0]` remains live | ok / ok | `10 10` | Safe false rejection, A4: callback is `lambda: 3`; mutation through `q` reaches `xs[0]`. |
| Scalar callback while a whole-list readonly Span remains live | ok / ok | `11 8` | Safe false rejection, A4: non-structural source mutation remains visible through the view. |
| Scalar callback while `take_ptr(x)` remains live | ok / ok | `10 10` | Safe false rejection, A4: pointer mutation reaches the still-live `x`. |
| Named helper forwarding an erased `Callable` under an element loan | ok / ok | `10 10` | Safe false rejection, A4: the harmless callback is reached through `relay`. |
| Scalar callback inside the generic vararg loop in `calls/star_unpack_generic_fn` | ok / ok | `10` | Safe false rejection, A4/A9: iteration has a live iterator loan; callback only reads the nocopy element. |
| Returned self-capturing closure, `nested_def/in_method_escaping` | ok / ok | `101`, `111` | Safe false rejection, A2: receiver remains alive and unmoved; mutation after closure creation is visible. |
| `Own[Packet]` result with a Span field over a caller-owned list | ok / ok | `99` | Safe false rejection, A7: caller keeps backing storage alive and only replaces an element. Outer ownership does not remove the view dependency. |
| Callback returning `Ptr[Node]` into a global | ok / ok | `99` | Safe false rejection, A1/A5: this program does not replace or destroy the global; the proposed layer refuses global roots. |
| Scalar value-capture closure, `calls/callable_return` | ok / ok | `15`, `5`, `21` | Acceptance control for A2: copied scalar captures add no external lifetime dependency. |
| Named helper forwarding an `Fn` parameter | lowering reject / not run | CPython `10 10` | Pre-existing `call.arg_shape.other_callabletype`; not a newly rejected program. |
| Child widget storing `lambda: self.bump()` in its constructor | lowering reject / not run | CPython `1` | Pre-existing `lambda.self_capture` gate; not a newly rejected A12 program. |
| Direct return of a local `Node` under a bare `-> Node` contract | sema reject / not run | CPython `7` | Existing unsafe-borrow rejection under TPy's reference-return convention; must remain rejected. |
| `f = Src().reader()` where `reader` returns a `[this]` closure | ok / deliberately not run | CPython `6` | Demonstrated lifetime violation in the emitted C++; A2/A6 must reject or prove an ownership/lifetime-safe representation. |

The loop case retains its existing generic field-copy warning. It does not warn
about callback effects and does not acknowledge the projected A4 rejection.
The other eight executed cases emitted no warnings.

The unsafe capture emits `std::function<int32_t(int32_t)> f = Src().reader();`
and `reader()` captures `[this]`. The temporary receiver dies at that statement's
end, before `f(1)`. This is the already tracked escaping-self-capture defect in
`BUGS.md`; a matching run by accident would not establish safety.

### Small reproducible safe example

```python
from tpy import Fn, int32

class Node:
    def __init__(self, v: int32):
        self.v = v

def use(f: Fn[[], int32], xs: list[Node]) -> int32:
    q = xs[0]
    n = f()
    q.v += n
    return q.v

def main():
    xs = [Node(7)]
    print(use(lambda: 3, xs), xs[0].v)

main()
```

Both runtimes print `10 10`. The relevant emission is `Node& q = ...;`
followed by `int32_t n = f();`. No reference-returning callback is involved.
For the pointer sibling, take `x: Node`, use `p = take_ptr(x)`, and mutate
`p.v`; for the view sibling, take `xs: list[int32]`, bind
`q: Span[readonly[int32]] = xs`, call `f`, then replace `xs[0]` and read `q[0]`.
Using `Callable` at both `use` and a `relay(f)` helper gives the accepted named
forwarding sibling. The same helper written with `Fn` currently hits the
pre-existing lowering rejection above.
The non-storing `Fn` forwarding defect is tracked as
`BUGS.md#fn-parameter-named-forwarding-rejected`. A separate minimal check with
only `relay`, `use` and `print(use(lambda: 3))` reproduces it without any loan.
That confirmation is outside the 13-program compatibility sample.

The view/aggregate probes use implicit list-to-Span bindings so the Python
source also executes with `lib/cpy`. Calling `span()` on a builtin list instead
hits the existing CPython-stub limitation recorded by
`tests/cases/builtins/span_function/no_cpython.txt`; that failure is not a
compiler admission measurement.

## Descriptor design constraints

The full descriptor must retain shape as well as the three intended facts.
A product keeps one child per tuple element, including a singleton; a sum
keeps alternatives. Transfer and permission cannot collapse a mixed
`tuple[Own[Node], readonly[Node], int32]` to one useful scalar answer.

The contained-borrow summary needs to distinguish:

- proven absence of external dependencies;
- known possible dependency paths;
- symbolic dependencies resolved by type/form substitution;
- opaque unknowns due to missing layout or capture information.

A summary can contain both known paths and unresolved obligations. Unknown is
never a synonym for no borrows. This is a proposed analysis interface, not new
user syntax or an approved rejection policy.

### Layout context is necessary

Current compiler methods produce:

| Shape | Return boundary | Stored payload |
|---|---|---|
| `tuple[Node]` | `std::tuple<Node*>` | `std::tuple<Node>` |
| `Own[tuple[Node]]` | `std::tuple<Node>` | `std::tuple<Node>` |
| `tuple[Own[Node], readonly[Node], int32]` | `std::tuple<Node, const Node*, int32_t>` | `std::tuple<Node, val_or_ref<const Node>, int32_t>` |

The walk therefore needs boundary versus stored-layout context. A bare
reference-type field is stored inline; its return convention alone does not
make the field an external loan. Conversely a stored readonly reference, view
or pointer can retain a dependency. `Own` changes the outer transfer, not every
field's lifetime. A `ValueType` bound does not prove absence of borrows:
pointers and views are value types.

Reuse `TupleType`/`ReadonlyType` storage rules, Optional/Union representation
facts, `type_def_registry.is_borrowing_view_type`, and
`_fields_and_parents_under_args` for generic fields and inherited layout.
The latter was exercised with a `Packet[T]` field substituted at `int32`,
`Span[int32]` and unresolved `U`. Recursive layouts need a finite summary graph
and a justified fixed point, rather than treating recursive re-entry as proof
of no borrows.

Do not memoize the descriptor using plain `TpyType` equality/hash:
`OptionalType(int32)` and its forced-pointer counterpart compare equal and hash
equally but have different representations. `TypeParamRef.bound` is also
excluded from identity. Do not freeze record summaries before registration is
complete.

### Ownership and capture boundaries remain decisions

`Box` and `Rc` own lifetime-managed pointers in their implementation fields.
The same `Ptr` spelling also expresses ordinary borrowed pointers. A recursive
field scan can conservatively mark both as borrow-capable, but then rejects
safe owned handles. Proving the difference requires trusted ownership summaries
or analysis of those implementations. `TypeDef.is_indirecting` only breaks
recursive-size cycles; Send/Sync and their unsafe overrides are not ownership
proofs. Do not silently whitelist type names. The trust policy remains open.

Likewise `Callable[[], int32]` can describe an empty environment, copied scalar
captures, reference captures or copied views. The signature cannot distinguish
them. Concrete capture facts exist at lambda/nested-def construction and in
`FunctionInfo.frame_captures`, but `FrameSlot` retains only name, type, Send and
Sync, losing the capture mode. Precision needs expression/binding facts before
erasure and propagation through aliases and forwarding. The `fi` of a function
returning a closure is not necessarily that returned closure's capture summary.

None of these type facts identifies which caller object supplies a loan or what
later invalidates it. They cannot replace provenance/effect analysis.

## Generic-form prototype

The scratch worklist model evaluated **13 root instantiations**: 6 accepted and
7 intentionally rejected, using 20 canonical states and 26 origin/state visits.
It exercised these independent conditions:

| Input | Expected model result |
|---|---|
| Same `Node` payload, fresh versus borrowed callbacks, forwarded through two helpers into owning storage | fresh accepts; borrow rejects at selecting root with storing-site context |
| Scalar `int32` callback result | accepts |
| Concrete payload plus unresolved callback form | remains a forwarding edge |
| Two callbacks with the same payload, rebind or conditional merge | matching forms accept; mixed forms reject |
| Two identical borrow instantiations at distinct source sites | both sites receive diagnostics |
| Fresh rebind of a non-move-assignable payload | rejects independently of form equality |
| Identity-recursive two-function forwarding cycle | terminates; fresh and borrowed states remain distinct |

Forms must be indexed by callback positions, not just by a shared payload type
parameter `K`: two callbacks returning `K` can have different forms.

`sema/own_copy.py` supplies useful immutable obligations, canonical callee
identity, type substitution, forward edges and workspace discharge. It does
not already supply the needed form channel. Missing pieces include:

1. Form expressions and composition independent of payload substitution.
2. Openness in both types and forms, including an empty type substitution and a
   concrete payload whose callback form is still open.
3. Form-aware edge, root, forward and discharge keys at all deduplication sites.
4. Call-site origins retained separately from canonical analysis states. The
   existing declaration-oriented diagnostics and global seen set would lose the
   second selecting call site. Keep existing own-copy diagnostic policy intact.
5. Real producers at named functions, lambdas, aliases and selected overloads,
   plus facts for lowering local slots and returns. A diagnostic-only table is
   insufficient.
6. A semantic move-assignability fact for rule 26(iv). The prototype supplies it
   as a fixture; it does not solve the native/generic payload boundary.

For `k` independent binary callback positions there can be `2^k` reachable form
assignments for one payload substitution. Encoding forms in a tuple key does
not remove that bound. Explore reachable states rather than eagerly enumerating
the product. With `S` finite canonical states and `R` roots the model visits at
most `R*S` origin/state pairs. It does not prove termination for recursively
growing type substitutions, nor validate runtime slot lowering or lifetimes.

Rough additional form-channel estimate: **8-13 engineer-days**, including
integration, diagnostics, tests and review. This is an engineering estimate,
not measured implementation time. It excludes runtime slots, full descriptor
analysis, provenance/effects and a native move-assignment trait. The old 49-day
subtotal already omitted this infrastructure and must not be read as a complete
schedule.

## Admission facts: what is actually missing

| Rule | Existing machinery | Missing obligation / scope |
|---|---|---|
| A1: argument roots | `return_borrows_from`, root walkers, binding provenance | Parameter indices describe recognized roots, not proof that every return path/root was accounted for. Need explicit unknown/completeness and forwarding summaries. |
| A2: borrowed environment escape | Capture lists, nested-def escape marking, frame traits | A binding-level borrow marker through aliases, branches, named forwarding and sinks; copied views still borrow. New escape/dataflow summaries. |
| A3: preserve argument loans | Call-result registration and current alias/branch joins | Callable synthetic FIs have no root summary; returned-call propagation depends on known callee facts. OPAQUE currently protects moves, not invalidation. Need total summaries and propagation. |
| A4: unknown effects | Parameter/self mutation fixed point and argument conflict checks | Hidden capture/global effects and callback-invoking helper summaries; current checks inspect explicit arguments and miss zero-argument callbacks. New effect analysis. |
| A5: globals | Namespace/root information | Requires complete root classification from A1; an empty parameter-index set is not a global-root proof. |
| A6: temporaries | Dangling-return/view checks and some materialization facts | Every candidate root and full-expression extent, including composed calls and the neutral warned-copy fallback. Depends on A1/A3. |
| A7: aggregates | Type/layout/field/view metadata | New context-sensitive contained-borrow descriptor, including symbolic and opaque outcomes; ownership/capture policies above. |
| A8: during invocation | Central argument evaluation and call sites | Borrow/effect obligations established inside a helper and propagated to its caller before callback invocation. Post-result loan registration is insufficient. |
| A9: lexical extents | Borrow tracker, branch joins, iterator cleanup, separate view tracking | Complete producers, rebinding, joins and region exits for every loan class; current separate trackers do not establish one complete lexical model. |
| A10: errors | Located diagnostics | Bounded policy after sound facts exist; distinguish a missing proof from a demonstrated lifetime violation. |
| A11: mandatory form checks | Callable matching, binding contexts, shared representation reader | Semantic descriptor/stamps and one source-aware checker, including overload selection, erasure and native paths. Bounded contract work, not provenance. |
| A12: child widget callback | Constructor sinks and A2 capture metadata | Conservative rejection follows A2; safe receiver lifetime proof is intentionally absent. Existing lowering rejects cannot count as new losses. |

Code authorities: `sema/statements.py` return-root collection and
`_register_call_result_borrow`; `sema/analyzer.py` body-summary finalization;
`sema/calls.py` callable synthetic FIs and borrow-conflict checks;
`sema/mutation_propagation.py` call-graph propagation; `sema/frame_traits.py`
capture classification; `sema/flow_facts.py` joins and `scope_tracker.py` exits.
Existing tracked gaps include nested-def summary replay, captured-argument
effects, import-cycle borrow facts, nested-call argument borrows, whole-container
Span registration, and escaping-self lifetime/relocation. This investigation
does not fix or reclassify those defects.

## Scope and decision before implementation

The descriptor survey covered scalar/reference, singleton/mixed tuple,
Optional/Union, recursive/generic fields, Own/readonly, str/bytes/views, Ptr/Span,
Box/Rc and closures. Runtime probes covered free functions, methods, a
constructor rejection, local/return/field/global boundaries and iteration.
Async/generator suspension, context managers, exception cleanup, comprehensions
and match-arm joins were not newly prototyped here. Their position-specific
facts remain required by the shared analysis design; a straight-line model
does not prove those cells complete.

Pitfall checks used source mutation to expose aliases, inspected scalar versus
tuple storage/return forms, kept generic forms separate from their payloads,
and checked actual Span/pointer/capture emission. No new warning, syntax
rejection, allocation rule or diagnostic remedy ships in this investigation.
The proposed layer's diagnostic remedies have not been validated by an
implementation of that layer and remain part of its gate.

Suggested next work, subject to the sequencing decision:

1. Design the smallest shared analysis substrate from `IR_DESIGN.md`'s six
   callable-provenance provisions: stable places, explicit operations and CFG,
   loan-holder/liveness propagation, and summaries/effects at calls.
2. Preserve Python-compatible shared mutation: conflict means invalidation of
   borrowed storage, not exclusive access. MIR-backed emission, SSA and precise
   index disjointness are not prerequisites for this analysis-only work.
3. Resolve ownership summaries, concrete capture facts and native assignment
   traits explicitly; then integrate descriptor and generic-form producers.
4. Revisit callable admission and its compatibility corpus on that substrate.

Checkpoint 3 has produced evidence and a recommendation; the compatibility gate
has **not passed**. Proceeding with the current contract-first restrictions or
changing the sequence to analysis-first both require the user's decision.
