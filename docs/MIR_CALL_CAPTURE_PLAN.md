# M2.11-M2.12: semantic call and capture metadata

Status: implemented on 2026-09-18 after the approved producer survey, with
M2.11 then M2.12 delivered as separate reviewed commits on one branch.
Both steps preserve source acceptance, diagnostics and generated C++.
Validation results are recorded in [MIR_ANALYSIS_PLAN.md](MIR_ANALYSIS_PLAN.md).
Merging and pushing remain separate from this batch.

## Why these steps

The remaining M2 contract needs qualified callees and structured captures.
Today THIRCall retains a source name, rendered C++ target and argument nodes;
THIRLambda and THIRNestedDef retain rendered capture lists. The existing
producers still have the resolved declaration and selected capture decisions.
Preserve those decisions there instead of reconstructing them in MIR.

These are metadata milestones. General calls and closure construction/body
execution remain MIRNotCovered after both steps. A known function signature
does not prove its effects, exceptional edges or termination; a complete
capture inventory does not prove escape safety or lifetime. M3/M4 still own
those obligations, as specified in CALLABLE_PROVENANCE_REQUIREMENTS.md.

## M2.11: resolved free-function identity and signature

```python
# helper.py
from tpy import int32
def choose(flag: bool, x: int32) -> int32:
    return x if flag else 0
# main.py (also imports int32)
from helper import choose as select
def run(x: int32) -> int32:
    return select(True, x)
```

The emitted call remains equivalent to `helper::choose(true, x)`. The new
fact identifies the defining `helper.choose` declaration, not the local
`select` spelling, and preserves its typed signature. A matching descriptor
on the emitted THIRFunction lets tests join the call with its definition.

Invariant: every present descriptor identifies the selected, unique ordinary
free-function declaration and its semantic signature; absence means unknown
or outside coverage, never pure, effect-free or a valid safety proof.

Use small frozen THIR data types for function identity (module and canonical
name), signature (ordered parameter types and return type), and the combined
resolved-callee descriptor. Carry TpyType values unchanged, including their
wrappers. This describes a declaration signature, not argument storage,
evaluation order, borrow provenance, or a finalized effect summary.

Use the structural module registry key for identity, matching M2.10 globals.
For local declarations this is `ctx.cpp_module_name`: despite its name it is
the file-derived `compiled.name`, not a rendered C++ namespace. Normalize the
entry point's semantic `__main__` owner using that explicit context; imported
bindings retain their ultimate defining module. C++ namespace overrides are
separate and must not change identity. Never parse `callee_cpp` or split a
rendered qualified name to recover a declaration.

Read the canonical selected FunctionInfo and existing SymbolBinding/module
registry at the producer. Require evidence of a unique ordinary module-level
declaration. FunctionInfo.root provides canonical resolution while lowering;
neither that mutable object nor its Python address becomes an IR identity.
Registration retains the source declaration only for a unique ordinary name;
the shared producer checks its finalized signature against FunctionInfo.
Overloads, repeated definitions (even identical signatures) and inconsistent
cycle signatures stay absent at calls and definitions. The internal AST
reference supplies lowering evidence only and never enters THIR.
Overloads share qualified_name today, so they cannot use this two-part key.
The caller-supplied MIRBodyId remains unchanged. A future consumer associates
the function descriptor with that body; these steps do not require a shared
whole-program body-identity service.

Initial eligibility:

- Direct calls to synchronous, non-overloaded, monomorphic module functions,
  including same-module calls, imported aliases/reexports and module aliases.
- Concrete typed signatures; retain tuple, Optional, union, reference, Own,
  readonly and view types as signature data without granting MIR support to
  their operations. Open, inferred-template and callback-template signatures
  remain absent until declaration/instantiation identity can be distinguished.
- Exact normalized argument arity. Omitted defaults, variadic expansion and
  special argument packaging remain outside this descriptor's call-site gate.
- No native/builtin/template/linkage replacement, inline body replacement,
  overload/dispatch variant, method/static/class/super/constructor target,
  callback/computed target, generator/async factory or error-return convention.

Attach facts at the actual plain/imported and user-module-qualified source
call producers, not to every THIRCall returned by a general expression walk.
THIRCall also represents synthesized checks, numeric constructors and helpers.
The module-call producer is shared with static/class/super calls, so its
eligibility test must distinguish those paths positively.

Reuse the existing immutable THIR storage-fact convention. Put semantic
callee eligibility in a focused lowering helper shared by call and definition
producers; do not rerun overload resolution or build another symbol registry.
Validate immutable descriptor shape, arity and declaration consistency using
existing type contracts. Do not equate each argument's result_type with its
declared parameter type: Own, readonly and argument adaptations can differ.
Joining calls with emitted definitions belongs in integration tests; the
per-function validator does not require every imported body or a second
general type-compatibility resolver. Missing metadata must not become a
source error.

## M2.12: complete structured capture inventories

```python
from tpy import int32
def run(x: int32) -> int32:
    def read() -> int32:
        return x
    x = 7
    return read()
```

The emitted nested function remains `[&x]() { return x; }`: it reads the
scalar binding after the assignment. An Fn-context lambda has the same
reference-capture mode; Fn is a parameter-only spelling, so its test passes
the lambda to an Fn parameter. A Callable-context lambda instead emits `[x]`
and snapshots the scalar. Preserve these current decisions and diagnostics;
do not make their capture behavior uniform in this increment.

Invariant: a present inventory describes every selected capture, including
its source binding, type, storage relation and access. None means incomplete
or unsupported; an empty tuple means proven capture-free. Capture-free does
not mean global-effect-free. One unsupported capture makes the entire
inventory unavailable; never publish a misleading supported subset.

Use body-local identities, qualified by the enclosing body when consumed:

- Give each closure a deterministic lexical occurrence index within its
  immediate enclosing body, plus its lambda/nested-def kind. Count all
  lexical closure sites, including unavailable ones, independently of
  eligibility; sites inside another closure belong to that inner body.
  Expanding eligibility must not renumber existing sites. Names, source
  lines and Python object addresses are not unique closure identities.
- Identify capture slots by closure index and capture ordinal. Keep source
  names for display and resolution within the supported enclosing scope.
- Identify the source binding by its parameter/local/receiver category and
  unique name within that scope. Only admit direct parameters, receiver and
  unconditional entry locals whose identity is unambiguous. When MIR later
  consumes these facts, resolve them to its body-scoped slots; do not invent
  a second whole-program lexical binding service in this step.
- Keep scalar binding reference, scalar value snapshot, borrowed-record
  referent and receiver alias as distinct storage relations. Access means
  access through the capture: a reference preserves the source's applicable
  access, while a scalar snapshot is readonly inside today's non-mutable
  C++ lambda even when the source binding is mutable. Reuse finalized source
  facts and the selected closure mode; lambda readonly_params does not
  describe capture access.

Stamp beside the existing capture-list construction, using finalized sema
modes and lowering's selected binding representation. Both modes and source
representation matter: `[&p]` for a C++ record reference parameter aliases
its referent, but `[&p]` for a pointer-backed local aliases the holder and can
follow its later reseats. The latter is excluded from this first inventory.
Existing THIRAliasBinding is not a universal capture operation: it denotes
capturing the current referent, not following an independently mutable holder.

Initial eligibility:

- Ordinary lambdas and synchronous nested defs, including zero captures,
  directly inside ordinary free functions, eligible instance methods and
  supported constructor body tails.
- bool/int32 parameters and entry locals, captured by reference or value.
  Direct borrowed plain-record parameters captured by reference, and `self`
  as receiver alias, with existing explicit/inferred readonly preserved.
- Escaping nested defs may have reference captures; do not assume that an
  escaping closure copies everything. Conversely, Fn does not prove that
  a callee cannot store a closure. Neither fact grants escape permission.
- Record copy/move captures, record locals, narrowed aliases, frame slots,
  transitively nested captures, callable captures and all other shapes stay
  unavailable. Exclude both an inner closure and an outer closure containing
  another closure, even if the outer captured_names is empty: the tracked
  transitive-capture gap prevents treating that list as complete. Apply
  nesting/position eligibility before the empty-inventory fast path. A
  copied handle or wrapper must never be assumed borrow-free.

Keep the C++ capture/parameter/return strings unchanged. This step records
capture construction facts only; typed closure-body lowering, invocation,
propagation through copies/containers/returns and loans remain later work.

## Producer survey and implementation sites

M2.11: parse/nodes.py already carries resolved_function_info; typesys.py has
FunctionInfo.root, originating_module and qualified_name; symbol_binding.py
owns canonical import bindings. The two relevant source-call producer
families are in thir/lower/expressions.py. The matching ordinary definition
is built in thir/lower/functions.py. thir/nodes.py and thir/validate.py own
the immutable contract and validation. No parser, runtime or stdlib change
is planned; sema continues to select declarations and signatures.

M2.12: lambda capture discovery/mode is in sema/expressions.py; nested-def
discovery is in sema/statements.py and escape/ref/move finalization is in
sema/analyzer.py. Capture-list producers are _lower_lambda_impl and
_lower_nested_def. Their shared fact builder belongs in thir/lower/ beside
the storage helpers, with scoped bookkeeping in the lowering context.
FrameType/frame_captures are Send/Sync summaries, not complete provenance,
and cannot substitute for the finalized storage relation.

## Scope matrix

The axes below are factored: a capture inventory is covered only when every
axis is covered. Signature carriage in M2.11 is distinct from executing
those shapes in MIR. Every deferred cell remains in the parent plan's M2-M4
backlog; this proposal does not declare M2 finished.

| Axis | M2.11 descriptor | M2.12 capture inventory | Deferred |
|---|---|---|---|
| Position | Eligible free-function definitions; calls wherever the shared source-call producers run | Direct children of ordinary free/method/constructor bodies | Capture inventories in module init, closures, generator/async, comprehension, with, try/finally, error-return and match: M2/M3 |
| Target | Unique ordinary module function, aliases/reexports included | Lambda or synchronous nested def | Overloads, generics, callback dispatch, methods/constructors and resumable factories as call targets: later M2/M4 |
| Shape | Concrete declaration types preserved opaquely | bool/int32; borrowed plain-record parameters; receiver | Tuple/singleton, Optional, union, str/bytes/views, Own, readonly wrappers beyond proven access, Ptr/Span, Box/Rc, callable/container captures: later M2/M4 |
| Source slot | No argument-place/effect claim | Parameter, unconditional entry scalar local, receiver | Field/element/global-backed captures, branch/loop bindings, aliases and frame storage: later M2/M3 |
| Destination slot | Call/definition metadata only | Closure capture slots only | Executing closure local/parameter/return/field/container/global transfers and escapes: later M2-M4 |

Direct globals are not closure captures today. Their absence from an
inventory says nothing about global reads/writes in the closure body.

## Validation, pitfalls and delivery gates

Add independent emitted-THIR fixtures; compiler tests must not read snippet
case sources. M2.11 checks alias/reexport/body identity agreement, distinct
same-named modules, local shadowing, entry-point naming, namespace overrides,
signature wrappers and each excluded target family. M2.12 checks complete,
empty and unavailable inventories, occurrence identity (including same-line
lambdas), scalar binding versus snapshot, record/receiver aliasing, access,
shadowed names and every excluded source/mode. Malformed facts must fail IR
validation. Existing MIR call/closure rejection tests must remain rejecting.

Reuse deliberate native witnesses: imports/cross_module_qualified_func_import_shadow,
imports/from_import_shadow, calls/record_arg_pass_through,
operators/short_circuit_arg_temp, nested_def/escaping_ref_param,
nested_def/in_method_escaping, nested_def/nonlocal_basic and
nested_def/in_method_const. Add a condensed native case only for a semantic
gap those cases do not deliberately pin. Reference tests must mutate shared
storage after capture and observe the change.

Pitfalls: silent-copy-vs-alias and copy-warning-at-wrong-site require that
metadata describe the selected operation without authorizing a new copy;
tuple-equals-scalar requires whole-inventory exclusion for unsupported
wrappers; same-construct-every-position uses the scope matrix and shared
producers; conditional-operand-evaluates-in-place forbids inferred sequencing
or hoisted capture evaluation. Generic-equals-monomorphic-twin is deferred
explicitly, never approximated by a concrete descriptor. view-not-copy and
hidden-allocation are protected by unchanged emission and excluded view
captures. const-source-const-loop-var requires finalized access and excludes
loop/frame aliases. generated-cpp-readability, no-cpp-in-diagnostics,
no-internal-names-in-diagnostics, reject-valid-python-only-as-documented-divergence
and no-warning-on-valid-code require byte-identical existing C++/diagnostics:
metadata absence cannot become a source diagnostic.

Known boundaries already tracked: BUGS.md#escaping-capture-mutation-snapshot,
BUGS.md#nesteddef-copy-capture-uncheckable,
BUGS.md#escaping-capture-of-narrowed-subject,
BUGS.md#nested-lambda-no-transitive-capture,
BUGS.md#fn-slot-closure-stored-by-callee and
BUGS.md#callable-value-borrow-return-copies-unwarned. Preserve them as separate
work; this survey established no new defect. Function import identity is
distinct from M2.10's imported scalar binding-snapshot defect.

Design-time parity assessment: the direct-call and nonescaping scalar-reference
examples match CPython. Existing Callable snapshot divergences are recorded
as current emitted behavior, not endorsed as new language rules or safety
proofs. Compiler-only probes confirmed imported alias spelling, nested-def
reference capture, Fn-argument lambda reference capture and Callable scalar
snapshot capture. Native execution was not repeated during this doc-only
survey; the deliberate cases above provide the existing runtime witnesses.

For each implementation step: update LANGUAGE_FEATURES/ARCHITECTURE and the
parent plan, run focused tests, applicable specialist reviews and readiness,
then the full forced remote suite. No existing snapshot updates are expected.
Keep one squashed commit per step on a single branch; no merge or push.

Confidence: high in the metadata-only phase boundary and producer locations;
medium in the complete bounded gates until emitted-THIR tests verify module
normalization, every closure source relation and same-body occurrence IDs.
Those are implementation gate tests, not permission to expand scope. A need
for new capture behavior, a universal binding rewrite or call-effect policy
returns to design review before compiler changes.
