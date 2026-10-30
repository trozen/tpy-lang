# Analysis-only MIR: implementation plan

## Decision and boundary

On 2026-09-17 the user approved bringing analysis-only MIR forward before the
coupled callable contract, following `CALLABLE_CONTRACT_FEASIBILITY.md`.
That approves the sequence, not the proposed admission restrictions or every
implementation detail below. **M1's implementation scope was then approved and
is implemented in `tpyc/mir/`.** Source acceptance and generated C++ are
unchanged; normal compilation runs MIR for advisory warnings: since
2026-10-10 the argument-alias warning
([argument-alias obligations](#argument-alias-obligations-the-first-mir-diagnostic)),
since 2026-10-27 the lifetime hazards MIR proves (B6, evidence vs warning).

The first increment builds and verifies a real control-flow graph from a small
THIR subset. It does not check callable lifetimes yet. Later increments must
supply all six provisions in `CALLABLE_PROVENANCE_REQUIREMENTS.md` before that
consumer can become authoritative. Shared mutation stays legal; the eventual
conflict rule concerns invalidation of borrowed storage, not exclusive access.

**Active sequence (approved 2026-09-29): breadth-first.** MIR now widens the
representations it models rather than deepening the bounded bool/int32 subset;
the steps are B1-B6 in [Breadth-first order](#breadth-first-order). Lifetime
and loan defects are routed to MIR instead of patched in sema (`BUGS.md`
entries tagged `deferred: MIR`). The numbered increments below are the record
of what landed, and the M1-M5 stage table is the capability map the B-steps
deliver.

## M1: internal scalar CFG foundation

This existing program illustrates the first supported body:

```python
def choose(flag: bool, x: int32) -> int32:
    result = x
    while flag:
        if x > 0:
            result = 1
            break
        flag = False
    return result
```

`choose(True, 3)`, `choose(True, -2)` and `choose(False, 7)` return `1`, `-2`
and `7`. Generated C++ remains the existing local variable, `while`, `if`,
assignment and return. MIR records entry, loop condition, conditional branches,
back edge and exit; `break` reaches the loop exit, not its normal `else` path.

**Invariant:** every successful lowering describes the complete admitted body
with typed binding/temporary identities, explicit evaluation order and valid
control-flow edges; a body outside that subset produces no usable MIR graph.

### Input and integration

Use the exact THIR returned by `Compiler.generate_code_and_thir()` in integration
tests (`tpyc/compiler.py`). This returns sources and their THIR context from one
emission. Do not re-lower a second approximation of the same function.
Lowering takes a `THIRFunction` and an explicit body identity/kind; it does
not take an analyzer or codegen context.

Only the lowering reads that `THIRFunction` ([IR_DESIGN](IR_DESIGN.md),
"Lowering: one pass building fragments"): `_Lowering` (`tpyc/mir/lower.py`)
walks it once and either refuses the body or builds it, each expression and
statement a `Fragment` (`tpyc/mir/emit.py`) spliced bottom-up into the body,
which `emit.assemble` closes and numbers; assembly reads no THIR. Every fact
it needs has one source: a THIR fact read as is (a binding's shape built once
from its declaring node's facts, a read node's own facts, the temporary plan),
a THIR fact translated at one site (`value.loan_access` for a binding's access,
`owned_leaf_role` for whether a call result or a literal is fresh), or a MIR rule
at one site (storage durations, call summaries, the class model, evaluation
order, record copy raise flags). The facts MIR still derives because THIR
does not carry them are listed in TODO.md under "One conversion boundary for
every value sink". A refusal names the lowering's reason; what needs the
built body is refused after the walk: a declaration THIR placed no storage
for, only once the body reaches it, then a non-void fallthrough, then the
validator's.

There is no complete standalone THIR-module pass today. Ordinary functions
lower in `codegen_cpp/generator.py`; resumables lower during frame emission.
`collect_thir()` deliberately drives codegen for that reason. M1 introduces no
normal compilation hook or `--dump-mir` option. Its internal dump and integration
tests exercise the future analysis boundary without adding default compiler
cost or making partial analysis a language restriction.

New pattern: general MIR blocks and places do not exist. Reuse THIR's immutable
typed-node, explicit validator and deterministic dump conventions, not a parallel
parser, type inference pass or C++ emitter. The existing resumable CFG models
suspension/frame mechanics; it is an integration input for a later increment,
not a general semantic CFG to clone.

### Identity and operations

- A body identity includes its module and declaration identity; a short function
  name alone is insufficient. Within that body, allocate deterministic binding,
  temporary and block IDs. Names and source locations are display metadata.
  IDs are stable within the artifact and reproducible for identical input, not
  promised stable across source edits. Python object addresses are not MIR IDs.
- Parameter/local slots are mutable. Reassignment writes the same slot;
  expression temporaries have separate IDs. The admitted subset has one binding
  per source name, so a lowering-local name map can resolve reads to slot IDs.
  A read that cannot resolve locally is unsupported, not an implicit global.
- Store `TpyType` and the carried THIR form. In M1 admitted values have VALUE
  form. Do not infer ownership from VALUE, STORAGE, last-use flags or a C++ type
  string; there are no alias, ownership-transfer or loan verdicts in this slice.
- Operations are typed literal/read/comparison/not evaluation and scalar slot
  writes. A read produces a captured value, not a deferred slot read; a walrus
  yields its assigned value. Eager operand order is admitted only when
  unobservable, as specified below. Branching expressions
  write a common result temporary on each reaching arm; this is mutable-place
  IR, not SSA. Terminators are branch, goto and return.
- Preserve locations on operations and terminators for later explanations.
  Blocks and instructions contain no analyzer, parse-node or emitter references.

### Exact admitted subset

Only ordinary monomorphic synchronous free functions with `bool` and `int32`
parameters, local slots and results; a bare void return is also allowed.
Local declarations form an unconditional entry prefix. Their initializers may
contain admitted branching expressions. All later writes target those slots.

Expressions: literals and plain local/parameter reads; in-range integer literal
coercion to `int32`; builtin comparisons on the admitted scalars; boolean `not`;
boolean `and`/`or`; conditional expressions; and a plain walrus assigning an
already declared scalar slot. Support both boolean `THIRBinOp` short-circuit
nodes and same-boolean `THIRValueSelect` nodes. Reject representation-changing
flags, non-boolean selects, checked casts and user conversions from this slice.

Statements: scalar declarations and assignment, expression statements, return,
`if`/`else`, `while`/`else`, `break`, `continue` and no-op. Loop back edges
re-evaluate the condition. A `break` bypasses that loop's `else`; normal condition
failure enters it. `continue` targets the innermost loop's condition.

Not covered: arithmetic, including fixed-width operations that can trap;
dedicated chained-comparison nodes; branch/loop-created or hoisted declarations;
narrowing; calls (including otherwise scalar calls); fields, indexing, globals
and captures; any ownership/form conversion. Some THIR hoists carry only
`(name, cpp_type)`, so deriving a typed slot from them would parse C++ spelling.
The admitted operations cannot raise or require destructor cleanup. M1 makes
no claim about exceptional or suspension paths in other bodies.

The existing evaluation-order policy is still open (`TODO.md`,
`BUGS.md#subexpression-right-to-left-eval`). M1 must not model Python's preferred
order as a fact about C++ that does not enforce it. For an eager comparison with
a walrus anywhere in one operand, require the other operand to be a literal
(possibly its admitted literal coercion); otherwise return `MIRNotCovered`.
Thus `(n := 1) > 0` is covered, but `x < (x := 0)` and
`(x := 1) == (x := 2)` are not. Pure operands can be normalized left-to-right
because their order is unobservable. Lazy boolean/conditional edges retain
their guaranteed sequencing. This bounded syntactic gate avoids designing a
general effect analysis or settling the open language policy in M1.

### Coverage and verification

Lowering returns either a complete `MIRFunction` or `MIRNotCovered` with a source
location when available, node kind and reason. An unsupported node anywhere,
including an unreachable branch, makes the entire body not covered. There is
no partial-success graph, opaque no-op instruction or broad exception catch.
This is internal analysis coverage, not a compiler diagnostic or rejection of
the source. A future consumer must not interpret `MIRNotCovered` as no effects,
no loans, an empty summary or a passing safety check.

Invalid IR is a separate programmer error. The verifier checks unique IDs,
declared and type-compatible operands/destinations, valid block targets, one
terminator per block, boolean branch conditions, return type agreement, and
definite assignment on reachable paths (including merged expression temporaries
and loop back edges). Unsupported input is not a verifier failure.

### Files and tests

Package: `tpyc/mir/{nodes,lower,validate,dump}.py` plus `__init__.py`
and focused unit/integration tests in that directory. Parser, sema, typesys,
runtime, stdlib and C++ emission need no behavior changes. If inspection during
implementation finds that even this subset requires new semantic THIR metadata,
revisit the boundary rather than reading C++ strings.

1. Compile source fixtures owned by the MIR unit tests through the normal
   emission path and test graph structure from the collected THIR: sequential
   writes, nested branches, early returns, both loop exits, nested break/continue
   targets, and short-circuit/conditional expressions. Dumps pin stable IDs and
   source correspondence, not generated C++ details.
2. Test lazy evaluation observably with an existing-local walrus:
   `n = 0; selected = flag and ((n := 1) > 0); return n if selected else 2`.
   Its CFG must place the write only on the right-operand edge. Cover `or` and
   conditional arms too. No calls are needed to expose evaluation order.
   Cover a walrus in a repeatedly evaluated loop condition. Pin the eager
   competing-operand examples above as `MIRNotCovered`, without asserting their
   toolchain-dependent native output.
3. Negative coverage pins: put unsupported calls, arithmetic, globals, hoists,
   shapes and body kinds inside otherwise supported functions. Assert the
   specific located reason and absence of a graph. These are analysis tests,
   not `error_` cases for valid source.
4. Malformed-IR tests exercise dangling IDs, wrong types, uninitialized merge
   results and malformed edges. Test distinct bodies with identical short names
   and repeated lowering for deterministic, non-colliding identities.
5. Keep compiler unit tests independent of `tests/cases/`. Ordinary cases run
   through the standard compile/exec/CPython harness, which does not exercise
   MIR in M1. Existing expected output must remain byte-identical; unexpected
   churn requires investigation. Future corpus-wide MIR validation belongs in
   a general harness integration, not case-specific compiler tests.
6. Run targeted tests during implementation, then the full forced-exec suite
   once via `rpytest`. Finish defect review, readiness and a single squashed M1
   commit on a branch. Do not merge master or push.

Design probe at `4976142820`: both CPython and native TPy produced `1 -2 7`
for `choose` and `1 2` for the lazy-write example. The real THIR dump contains
`THIRWhile`, `THIRIf`, assignment, short-circuit binop, walrus, literal coercion
and conditional return as expected. This validates the input seam and baseline,
not the unimplemented MIR builder.

The implementation's unit tests compile their own source fixtures and lower
the exact THIR used for emission. They check CFG paths with a bounded test
interpreter, inspect the lazy-write edge, and pin deterministic IDs/dumps.
These assertions test MIR semantics directly; they do not execute generated
C++ or compare MIR execution against CPython. Normal case-harness runs remain
regression checks for the existing compiler, not MIR coverage.
Separate negative tests cover unsupported input and malformed/undefined MIR,
including branch intersections and loop back edges. `MIRBodyKind` is mandatory
input: a `THIRFunction` alone cannot distinguish a free function from all its
sibling body kinds, so the caller must supply the declaration classification.

## M2.1: borrowed record holders and scalar fields

Status: approved and implemented, 2026-09-17.
This is the first bounded part of M2, not completion of storage/provenance
analysis. M1 is merged. Current provenance checks remain authoritative.

### Observable example and invariant

Given a plain reference record `Cell` with an `int32` field `value`:

```python
def inspect(a: Cell, b: Cell, flag: bool) -> int32:
    current = a
    saved = current
    if flag:
        current = b
    current.value = 7
    return saved.value
```

With distinct incoming objects containing 1 and 2, `inspect(a, b, True)`
returns 1 and changes only `b.value` to 7. With the same object passed twice,
it returns 7. The current C++ already expresses this correctly:

```cpp
Cell* current = &(a);
Cell& saved = (*current);
if (flag) current = &(b);
current->value = 7;
return saved.value;
```

M2.1 makes those facts explicit in MIR; emitted C++ stays unchanged.
**Invariant:** every admitted reference binding identifies the current
referent transferred between holders, and every field operation identifies
the logical field of that referent. No admitted operation creates, copies,
moves, destroys or replaces a record's owned storage.

A holder is a variable containing a reference, not the referenced object.
Distinct parameter IDs do not establish disjoint objects. Reseating `current`
does not redirect `saved`. Readonly limits access through a holder, not writes
through other aliases; a readonly field read must still observe those writes.
A scalar field load captures a value immediately, as M1 scalar reads do.

### Evidence and reusable authorities

The source probes for local alias mutation, conditional reseating, shared and
distinct arguments, scalar snapshots and explicit readonly aliases all matched
between native TPy and CPython. The selected functions' real emitted THIR uses
`THIRVarDecl`, `THIRPtrLocalDecl`, `THIRPtrLocalRebind`, `THIRAssign` and
`THIRFieldAccess`. This establishes the existing semantics and producer paths,
not completion of the broader M2 work package.

| Existing authority | Reuse / missing fact |
|---|---|
| `MIRBodyId`, `MIRSlotId`; lowering's local binding map | Keep deterministic body-scoped holder IDs and the one-binding-per-name restriction; no new symbol resolver |
| `NominalType` qualified identity and structured type arguments (`typesys.py`) | Reuse canonical type identity, never short record names or rendered C++ types |
| `_lower_borrow_local` and name-reseat arms in `thir/lower/statements.py` | These already choose reference alias / pointer bind / pointer reseat; attach the semantic operation at these construction sites |
| `THIRParam` construction in `thir/lower/functions.py` (the param record's `const`, from `_param_is_const_at`) and finalized `FunctionInfo.const_borrow_params` | Project the admitted parameter's borrowed-reference category and access capability; do not parse its emitted signature |
| Direct field lowering and `_field_cpp` in `thir/lower/expressions.py` | Carry declaring nominal owner, source field name and field type before the source name becomes presentation spelling |
| `BindKind` / `ctx.func.bind_kinds` in `sema/alias_rebind.py` | Not an input to MIR: the per-function table is reset before lowering; do not resurrect a stale analyzer lookup |
| `RebindStorage`, `THIRCopy`, `THIRMove`, `THIRFormConvert` | Existing facts for later owning/storage slices, not permission to admit their semantics in M2.1 |

`THIRVarDecl.cpp_local_representation` is expressly compatibility metadata.
`Form.BORROW` also does not establish ownership, alias roots or access policy.
The new semantic facts must be stamped beside the existing lowering decision,
not calculated afterward by inspecting either tag or a C++ string. Scalar
writes, alias binds and reference reseats remain distinct facts.

### Representation and boundary

1. **A small semantic THIR bridge.** Attach immutable facts to the existing
   nodes: borrowed-record parameter/access facts; local alias/reseat facts
   naming the source binding; and direct-field identity/type facts. Stamp
   only the supported, positively identified producer paths. A missing fact
   means unsupported analysis, never an implicit copy or an empty effect.
   Existing emission metadata remains available to the C++ renderer. The
   bridge carries no analyzer, AST node, registry object or mutable binding
   cell. Keep the bridge operation-centric; it is not a second THIR body.
2. **Places extend M1 slots.** Introduce `MIRPlace` rooted at a `MIRSlotId`,
   with typed dereference and field projections. An unprojected place is the
   existing local slot. A borrowed record holder's scalar field is
   `Field(Deref(holder), field_id)`. A field ID uses canonical nominal owner
   plus source field identity, not `field_cpp`. Only this direct projection
   path is admitted now; globals, indices and backing-store regions follow
   in later M2 slices.
3. **Explicit held values and operations.** Keep parameter/local/temporary
   roles separate from whether a slot holds a scalar or a borrowed record
   reference. Carry access capability explicitly. Generalize `MIRAssign`
   targets and scalar `MIRRead` operands to places. Add an explicit alias
   operation which reads a source holder's current reference into another
   holder; use it for both initial binding and reseating. Do not copy the
   record, defer the source read, or use a scalar load as a reference copy.
   Record copy, move, owning construction and borrow-from-owned-storage
   operations remain later work, rather than unused enum cases now.
4. **Verifier and dump.** Extend existing structural/type/definite-assignment
   checks to holder categories, initialized reference bases, field owner/type
   compatibility, and access capability. A scalar store through a readonly
   reference is malformed MIR; alias transfer may reduce access but may not
   increase it. Reseating a readonly-reference holder is distinct from
   writing its referent. A projected store reads its initialized holder;
   writing a field never establishes assignment of that holder. Verify
   scalar returns only. External record field
   initialization, referent lifetime, disjointness and loan legality are not
   proved by this verifier. Dump aliases and field loads/stores explicitly.
5. **Whole-body coverage stays mandatory.** Extend the current coverage walk
   and builder together, preserving checks for unsupported metadata and
   unreachable unsupported nodes. No producer stamp alone makes a body
   covered. M1's eager-effect ordering restriction remains in force; this
   slice adds no effectful receiver expressions or reference-valued walrus.

There is no new parser rule, type inference rule, sema provenance authority,
runtime support, stdlib implementation, language diagnostic or default
compilation hook. THIR construction/validation, MIR lowering/validation/dump
and their unit tests change. No C++ output or existing snapshot should change.

### Exact scope and sibling matrix

Admit ordinary monomorphic synchronous free functions. Parameters are the M1
scalars or borrowed plain, non-generic reference records; scalar/void returns
remain as in M1. Reference locals are initialized in the entry declaration
prefix from an existing parameter/local of the same nominal record type.
Subsequent name-to-name reseats may occur on M1 branches and loops. Parameters
themselves are not reseated. Fields are direct, non-native instance fields of
these holders, with `bool` or `int32` payloads. Preserve explicit readonly and
inferred const access; record selection/coercion is not added.

Do not admit inherited/subobject field selection, properties, class variables,
user dereference, native records, reference-valued fields or field receivers
with calls/side effects. These need their own semantic facts; a field's C++
spelling is never evidence for treating it as direct storage.

The following factors intersect; every factor must be covered for a cell to
be admitted. Deferred cells remain filed by the broader M2-M5 matrix below
and the MIR entry in `TODO.md`.

| Axis | Covered by M2.1 tests | Not covered / filed stage |
|---|---|---|
| Position | ordinary free function; M1 if/while/else/break/continue/early return within it | M2 remainder: methods/static methods, constructors, module bodies, closures; M3: generator, async, comprehension, context-manager body, try/finally, error-return body, match arm |
| Shape | bool/int32; plain borrowed record; readonly access to that record | M2 remainder: other scalars, tuple/singleton, Optional, union, str/bytes/views, Own, Ptr/Span, Box/Rc, concrete generic records; M4: open generics and per-instantiation obligations |
| Slot | parameters, entry-declared local holders, scalar temporaries, scalar returns, direct scalar fields | M2 remainder: record/aggregate returns, globals, captures, nested/reference fields, container elements and backing storage; M3: branch/loop-created bindings and frame slots |
| Operation | scalar read/write, reference alias bind/reseat, scalar field load/store, M1 control flow | M2 remainder: record copy/move/creation, calls and additional projections; M3: drop/del, cleanup, suspension and liveness; M4: call summary/effect application |

### Tests, implementation order and exit gate

Use unit-owned source fixtures compiled with `generate_code_and_thir()`, plus
small hand-built THIR/MIR inputs for malformed or absent metadata. Do not add
a special executable case or read source from `tests/cases/`. The eventual
general harness integration remains separate.

Extend the bounded MIR test interpreter with explicit reference values and
an object store. Test callers must be able to bind two parameters to the same
object identity. Check final object fields as well as function results:

- Write through an original holder and read through its alias, and vice versa.
- Reseat one alias while another retains the old referent; exercise distinct
  and identical parameter referents, branches and a terminating loop.
- Read through inferred-const and explicit-readonly aliases after another
  holder mutates the same object; readonly must not freeze the object.
- Save a scalar field value, mutate the field, then return the saved value.
- Distinguish two fields of one record and same-named fields of distinct
  nominal records. Include a Python field name needing C++ escaping; changing
  presentation metadata must not change the MIR field identity.
- Inspect real THIR semantic stamps and exact MIR operations. Missing stamps,
  unknown sources and unsupported sibling shapes produce whole-body
  `MIRNotCovered`; inconsistent MIR types/access/IDs fail verification.
  Include a field store through an uninitialized holder in verifier negatives.
- Keep M1 tests green; preserve deterministic dumps and source locations.
  Ordinary case tests remain byte-for-byte codegen/diagnostic regression
  checks, not claims that the case harness executes MIR.

Implementation and exit gates for this work unit:

1. Add and test semantic THIR facts at the existing producers; verify the
   renderer remains unchanged. Reuse the current const decision reader.
2. Extend MIR places/held values, lowering, verifier and dump together, with
   the semantic and malformed-input tests above.
3. Review scope boundaries and run the final full suite. Update this plan,
   `ARCHITECTURE.md` and `LANGUAGE_FEATURES.md` to mark actual coverage, then
   prepare one squashed implementation commit; do not merge or push.

The implementation lives in `thir/lower/storage.py`, the existing THIR
producer arms and `mir/`. `mir/test_storage.py` compiles its own source and
checks shared/distinct referents, all alias producer node kinds, branches,
loops, explicit/inferred readonly access, scalar snapshots and field identity.
It also guards against stamping method/constructor `self` aliases as ordinary
name bindings, and rejects properties, class constants, native fields and
user-dereference accesses as direct MIR fields.
Readonly name binds that use a `THIRFormConvert` carry the alias fact on the
binding itself; MIR admits only its non-moving borrowed conversion.
`mir/test_storage_validate.py` checks malformed places, access upgrades,
uninitialized field bases and join/loop definite assignment. Existing scalar
tests share the bounded interpreter in `mir/testutil.py`.

Pitfall checks: shared mutation tests expose alias-versus-copy mistakes;
scalar loads pin evaluation time; readonly alias tests prevent an immutability
assumption. Tuple/Optional/union and generic twins are explicit negative
coverage, not silently treated as records. Reference returns, view/owned
materialization, iteration and destruction are deferred, so this slice adds
no copy warnings, hidden allocations or lifetime verdicts. Generated C++ and
diagnostics must be unchanged. Internal MIR coverage is never a new rejection
or warning for valid Python. Existing documented language gaps stay open.

Confidence: high for this bounded slice after inspecting its actual producers
and native/CPython probes. The architectural risk is drift between the new
semantic stamps and emitted operations; direct producer tests and unchanged
codegen are the gate. M2.1 does not solve the later owning-storage, cleanup,
capture, container-region, summary or authority-transition problems.

## M2.2: owned scalar-record storage

Status: scope approved and implemented in `tpyc/mir/`.
This is an architectural extension of the internal analysis representation,
with no change to source acceptance, generated C++ or provenance authority.

### Observable example

For a plain `Cell` whose constructor assigns its int32 argument to `value`:

```python
def inspect() -> int32:
    current = Cell(1)
    saved = current
    current = Cell(2)
    saved.value = 9
    return current.value
```

The result is 2. `saved` still refers to the original object, now containing 9.
The existing C++ materializes initial storage, binds two holders to it, then
constructs the replacement in separate storage and redirects only `current`:

```cpp
std::optional<Cell> replacement;
Cell initial = Cell(1);
Cell* current = &initial;
Cell& saved = *current;
current = &*(replacement = Cell(2));
saved.value = 9;
return current->value;
```

Without the alias, the current compiler instead selects `(*current) = Cell(2)`.
MIR must preserve that existing distinction; it must not choose a storage
strategy or reinterpret all constructor assignments as fresh allocation.

**Invariant:** every admitted materialization names its owned storage separately
from the holder that borrows it; copy/move initialization and replacement act on
explicit storage, while alias reseating changes only the holder.

### Investigation and existing authorities

Native TPy and CPython probes agreed on preserved aliases, unaliased replacement
and explicit copy independence (`2 2 1`), and on compiler-selected move-through
plus a local that switches from owned storage to a parameter (`9 2 7 7`).
These are design probes, not an implemented MIR slice.

| Existing authority | What the extension must preserve |
|---|---|
| `thir/lower/statements.py`: plain owned-record and copy declaration arms | Owning `THIRVarDecl` with STORAGE form; actual initialization is a constructor or `THIRCopy` |
| `_lower_borrow_local`, REBIND_SLOT arm | Owning initial storage despite BORROW form; the local is also a reseatable holder |
| `_lower_record_ptr_slot_decl`, RECORD_RVALUE arm | Initial owned storage for a local later reseated to an existing object |
| Existing `THIRAliasBinding` producer sites | A name alias can borrow owned local storage as well as an incoming borrowed object |
| Move-through declaration arm; `THIRMove` | Record the selected move; do not re-decide it from names or last-use hints |
| `_rebind_storage`; `THIRAssign.rebind_storage` | Carry the effective OWN / IN_PLACE decision, including const fallback; do not rerun alias analysis |
| `emit._own_slot` | OWN is one slot per syntactic reseat site, reused if that site executes again |
| `THIRConstructor`, `THIRMilInit` | Existing constructor body and member initializers, not an assumed positional field recipe |
| `RecordInfo.has_copy/has_move/has_del` and copy/move eligibility | Copies, moves and destruction may execute user code; scalar fields alone do not establish trivial behavior |

Two constraints determine the proposed boundary:

- A constructor call can run arbitrary code. `THIRCtorCall.type_cpp` is
  presentation, and its argument list is not a list of fields. A covered
  constructor needs its actual emitted THIR definition and logical field IDs.
- A reseat site's OWN slot can be overwritten on a later loop iteration.
  Treating every execution as a fresh object would disagree with emission.
  The existing alias-rebind pass handles this and may warn; M2.2 excludes
  owning operations in cycles instead of introducing an iteration model.

### Representation

1. **Extend the existing semantic THIR bridge.** Stamp owning materialization
   at the actual declaration producers, alongside the M2.1 alias fact. Retain
   constructor/copy/move as distinct operations and reuse the effective reseat
   verdict. Facts identify logical storage and access, not emitted slot names.
   In particular, BORROW-form REBIND_SLOT declarations still create storage.
   Stamps must agree with the actual lowered source, including in unsupported
   methods/constructors; M2.1's `self` regression remains a required guard.
2. **Provide immutable record/constructor facts.** Extend the emitted
   `THIRConstructor` artifact with canonical nominal identity and the record's
   logical field layout and special-member eligibility; attach logical field
   identity to its `THIRMilInit` entries at their existing producer. Feed these
   emitted definitions to MIR through an optional immutable definition input.
   A caller collecting `generate_code_and_thir()` results can supply them once;
   existing M2.1 callers need no extra input. MIR gets no analyzer/AST/registry
   reference, and does not re-lower constructor source or parse C++ names.
   Eligibility includes constructor uniqueness, custom copy/move/destructor
   presence and existing copyable/movable verdicts; respect `nocopy`/`nomove`.
   Index definitions by canonical identity and verify each once per input set,
   rather than rescanning all definitions at each materialization.
3. **Verify the complete constructor before deriving a recipe.** Initially
   require one explicit, non-overloaded constructor, no base initializers, an
   empty body (apart from no-ops), and exactly one member initializer for every
   stored field. Each initializer is a scalar parameter or scalar literal,
   allowing the already-supported literal coercion. Parameters and all fields
   are bool/int32. No default-only fields, hidden initialization or calls may
   remain outside this accounting. Check the emitted call has a complete,
   type-correct positional argument list for that definition. Fully normalized
   keyword/default arguments can use that list; omitted/default-dependent or
   unresolved argument binding remains uncovered. Arguments must be pure M1
   scalar expressions, without walrus writes, calls or hoisted temps. This
   makes argument order and member-initialization order independently harmless.
   This is MIR coverage over existing THIR, not a second AST constructor
   classifier, a general call summary or a separately maintained body recipe.
4. **Reuse body-scoped slots and places.** Add a record-storage category to
   `MIRSlot`, retaining `MIRSlotId`/`MIRPlace`; do not create a parallel identity
   system. A source local can have a hidden owned-storage slot and a named
   reference holder. Add explicit borrow-from-storage and record
   construct/copy/move initialization operations. Existing `MIRAlias` continues
   to transfer a holder's current referent. Record operands and destinations
   are separate from scalar loads/stores and cannot use `MIRRead` as a copy.
5. **Preserve the effective replacement operation.** OWN initializes the
   reseat site's distinct storage and redirects the destination holder;
   previous storage remains for its other aliases. IN_PLACE writes the new
   record into the storage reached through the current holder, keeping that
   storage identity. Do not rerun sema's ownership/alias proof in the verifier.
   Every storage site in the admitted subset executes at most once; M1 loops
   may remain only when their bodies/conditions contain no owning operations.
6. **Keep move distinct from destruction.** A selected `THIRMove` initializes
   distinct destination storage. With the admitted memberwise scalar move,
   source storage remains alive and retains its scalar contents until scope
   exit; `std::move` alone does not emit destruction or `StorageDead`. Record
   the move event for later ownership analysis, without claiming it proves
   source uniqueness, invalidates every alias or establishes a loan lifetime.
   Initially admit only the existing move-through declaration from a fixed,
   non-reassigned owned local, not consuming call/return sinks.
7. **Extend validation and the test interpreter together.** Check storage and
   holder categories, nominal/layout agreement, complete initialization,
   source/destination access and definite assignment. Borrowing uninitialized
   owned storage fails; field stores do not initialize their base holder or
   record. Model record payload snapshots before an IN_PLACE write. The test
   interpreter gives each owned slot its own identity and preserves identity
   when replacing its contents. Copy and move create distinct destinations.
   This remains structural verification, not lifetime or alias-safety proof.

### Exact admitted scope

The factors below intersect, preserving all existing M2.1 coverage. Stricter
record eligibility applies to the new owning operations, not to borrowed-only
records already admitted by M2.1.

| Axis | M2.2 coverage | Deferred / existing work package |
|---|---|---|
| Position | Ordinary synchronous free functions; prefix declarations; acyclic replacements under M1 branches; loops with only previously covered operations | Other callable/module bodies remain M2; branch/loop-created bindings, cleanup, resumables, comprehension, match and error-return bodies remain M3 |
| Shape | Plain non-generic reference records, all stored fields bool/int32, no native/inheritance/protocol/value-record behavior, no custom copy/move/destructor | Aggregate/wrapper/view/container forms and generic twins remain broader M2/M4; custom effects need M3/M4 |
| Slot | New local owned storage, borrowed holders, scalar fields; existing scalar/borrowed parameters and scalar returns | Own parameters, record returns, owning arguments, globals, captures, container elements and nested/reference fields remain M2 |
| Operation | Verified scalar construction; explicit copy to a prefix local; selected move-through to a prefix local; borrow/alias; name reseat; constructor-rvalue OWN/IN_PLACE replacement outside cycles | General calls/factories, arbitrary constructors, record copy/move assignments, consuming boundaries, del/drop and repeated materialization remain later M2-M4 |

No record identity/equality/truthiness operator is added. No heap allocation,
move optimization, new warning, source restriction or public MIR CLI is added.
Missing definitions, unsupported metadata and unreachable unsupported nodes
must yield whole-body `MIRNotCovered`; they must not weaken normal compilation
or silently turn an unknown constructor effect into an empty effect.

### Tests, docs and implementation order

Use unit-owned source compiled through `generate_code_and_thir()`, following
M2.1. Never read `tests/cases/` from compiler unit tests. Extend the bounded
interpreter and malformed-IR tests; keep ordinary case snapshots unchanged.

1. Add semantic storage/constructor facts and direct producer tests for plain
   owned, REBIND_SLOT, RECORD_RVALUE, explicit copy and actual move-through.
   Include method/constructor/other-body regression guards for globally
   produced metadata, although these bodies remain outside MIR coverage.
2. Extend MIR storage places and operations, then verifier/interpreter. Test
   mutation through a borrow of owned storage, saved aliases after OWN reseats,
   IN_PLACE replacement, switching a local to a borrowed parameter, copy
   independence, distinct move destination, readonly source copying, and both
   branch paths. Verify multiple storage sites and same-named fields/types
   remain distinct; escaped field spellings must not affect identity.
3. Pin constructor rejection for body effects, base/default-only initialization,
   custom special members, missing/duplicate field facts, unresolved/default
   argument binding and effectful arguments. Pin owning operations in loops,
   the deferred shape/position matrix, malformed category/type/access facts,
   uninitialized storage and CFG joins. Include borrowed-only M2.1 compatibility.
4. Review the cumulative branch, run targeted checks and a final full forced
   suite, update actual coverage in this plan, `ARCHITECTURE.md`,
   `LANGUAGE_FEATURES.md` and `TODO.md`, then prepare one
   squashed implementation commit on a branch. Do not merge or push.

Pitfall assessment: shared mutation distinguishes aliases from copies; explicit
copy warnings stay where current codegen puts them. Pure constructor arguments
avoid the existing evaluation-order defect (`BUGS.md#subexpression-right-to-left-eval`).
Other positions get metadata regression checks; tuple/Optional/union/generic
twins are negative coverage, not inferred scalar semantics. Views, allocation,
iteration, cleanup and user diagnostics do not change. No new source
divergence was found in the admitted design probes; existing loop/cleanup
limitations remain outside this subset and tracked by the broader plan.

Confidence: high in the storage distinction and observed producer paths. The
constructor-definition boundary is the main implementation risk; first test
that its facts describe the exact emitted constructor and complete layout.
If implementation needs arbitrary constructor effects, new move decisions or
cyclic storage lifetimes, stop and revise the scope rather than widening it.

The implementation adds `owned_storage` facts to the existing declaration
producers and `THIRRecordLayout`/member identities to the emitted constructor.
Callers build `MIRDefinitions(tuple(ctx.thir_constructors.values()))` from that
emission and pass it as `definitions=` to `lower_function`. The immutable index
verifies each definition once and retains failed coverage as an explicit reason.
Missing definitions affect only bodies needing owning operations.

MIR uses `OWNED` slots (`RECORD_STORAGE` until B2) and `MIRConstruct`, `MIRCopy`, `MIRMove` and
`MIRBorrow` rvalues. A record-place write through `MIRDeref` preserves identity;
an OWN replacement initializes a distinct slot before rebinding its holder.
The verifier checks complete layouts, initialization, access, copy/move
eligibility and single-execution storage sites, including a linear CFG cycle
check. `test_owned.py` uses actual emitted THIR; `test_owned_validate.py` checks
malformed MIR. The shared bounded interpreter observes both the returned scalar
and mutations of distinct/shared storage. No compiler unit test reads case files.

## M2.3: flat tuple payloads

Status: implemented, reviewed and verified after the prerequisite fix landed as
`334c182905`. The approved scope keeps source acceptance unchanged and tests
empty/readonly payloads at the internal IR boundary where source admission is
narrower.
This is architectural work. Split aggregate products (tuples) from tagged sums
(Optional/unions): the latter additionally require presence/alternative facts,
checked payload projections and narrowing identities. Existing provenance
remains authoritative throughout both increments.

### Prerequisite: tuple-held aliases missing from rebind analysis

Investigation found a pre-existing silent divergence in ordinary local code:

```python
from tpy import int32

class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value

def example() -> int32:
    current = Cell(1)
    saved = (current,)
    current.value = 4
    current = Cell(2)
    saved[0].value = 9
    return current.value
```

At M2.2's merged revision `37f49a38bc`, native execution returns **9**;
CPython returns **2**, with no compiler diagnostic. The write of 4 is essential:
it keeps `current` live after capture, selecting a borrowed tuple member instead
of a last-use move into owned tuple storage. Emission then has the shape:

```cpp
auto saved = std::tuple<Cell*>(current);
current->value = 4;
(*current) = Cell(2);
std::get<0>(saved)->value = 9;
```

The violated invariant is that every borrowed tuple member must keep a loan on
the storage it actually captured. Tuple provenance records the relationship,
but tuple-literal bindings do not register it with `BorrowTracker`, whose
statement records feed `alias_rebind`. Consequently the replay wrongly proves
IN_PLACE replacement safe. This is a production semantic defect, not a missing
MIR instruction; teaching MIR to reproduce the overwrite would not fix it.

Recommended prerequisite: restore loan registration at the shared tuple-binding
boundary, after capture modes are final. Reuse `register_binding_borrow`,
`_register_source_borrow` and the existing call-result borrow contracts as
appropriate, feeding `BorrowTracker` rather than adding another provenance
model. Expose each borrowed element source to those helpers: passing the whole
literal to the existing root helper does not register its element loans.
REF/CONST_REF elements carry loans; VALUE captures must not manufacture
loans merely because their source type is a reference type. Owning reseats must
preserve still-borrowed old storage, using the existing storage strategy.

The user approved this prerequisite fix. Implementation uses one tuple-binding
registration helper after capture selection and old-loan removal, including
the existing unannotated walrus borrow form and tuple-call return contracts.
Self-assignment retains existing loans in both the tracker and replay without
creating a self-edge.
It also prevents numeric/character stored-field writes from spuriously warning about
invalidation; numeric property setters retain the check because their bodies
can replace borrowed storage. The prerequisite is merged; M2.3 is approved
with the source-admission limits below.

Audit declaration, reassignment and walrus producers together: they currently
update capture/provenance and remove prior loans at different points. Whole
borrowed-tuple payload/name copies must retain captured element loans, while
reseating the tuple must clear
only that holder's former loans. Audit call/method/property results against
their actual return-borrow contracts, and Optional borrowing tuples against
their selected representation. These are required sibling checks, not claims
that all paths have already been reproduced. Do not patch IN_PLACE selection
with a tuple-specific exception or infer local capture from escape roots alone.

The existing `resumable-alias-identity` entry in BUGS.md concerns a different
ownership direction/producer; it does not track this literal-capture
defect. It is an active prerequisite here, not a
deferred backlog entry.

An independent mixed-tuple probe confirms both lost identities: before mutation
through the saved reference, TPy observes old/new values `2, 2` versus CPython's
`4, 2`; afterward it observes `9, 9` versus `9, 2`. The scalar member remains
unchanged in both. Preserving the old storage matches Python's aliasing and
binding behavior; no new warning or rejection of this program is warranted.

The condensed `tuple/capture_owner_rebind` regression checks both mutation
directions, singleton/mixed/duplicate references, copies, reseats, self-binding,
call results, walrus bindings, branches and inverse VALUE/scalar captures.
It includes method, constructor, instantiated generic, nested-function,
generator and async sections. Unit-owned source additionally pins OWN versus
IN_PLACE decisions, release of replaced holder loans, readonly/nullable and
conditional tuple facts, borrowed call members, and the scalar-field/property
diagnostic distinction. These broader tuple checks are sema-only where current
THIR admission is narrower. No compiler unit test reads the case source.

Scope audit: the change repairs local binding registration, not tuple ownership
at every boundary. Module/global storage, fields and container sinks retain
their existing storage-form rules; return/parameter contracts are inputs to
local binding registration. Nested owning elements, string/bytes view forms,
Own/Ptr/Span/Box/Rc payloads and union storage are not reclassified by this fix.
Shared statement dispatch covers bindings under match, try/finally, context
managers and error-return bodies; this patch adds no cleanup or invalidation
rule for those constructs. A comprehension is not a statement-binding position;
its supported walrus producer shares the expression registration helper.
The unrelated immediate walrus field-access defect is tracked separately as
`BUGS.md#walrus-tuple-immediate-field-access`, with user approval.

### MIR contract

```python
def example(a: Cell, b: Cell) -> int32:
    pair = (a, 1)
    saved = pair
    pair = (b, 2)
    saved[0].value = 9
    return saved[1]
```

The saved scalar is 1; the write reaches `a`, regardless of `pair`'s later
reseat. Existing C++ copies a `std::tuple<Cell*, int32_t>` payload. MIR must
snapshot scalar values and reference identities; it must not alias the tuple
holder itself or deep-copy the referenced record.

Reuse MIR slots, places, record field identities and access capabilities. Add
a typed tuple payload/layout and element projection, plus explicit aggregate
construction/copy operations. A record field reached through a tuple composes
element selection, dereference and field selection. Extend place verification
to walk this typed chain; do not assume dereference is always the first step.

THIR producers must record the selected per-element scalar/reference capture
and readonly capability before reducing it to C++ spelling. Neither
`TupleType.is_value_type()` nor `THIRBorrowTupleLiteral` proves borrowing:
the latter can also contain owned VALUE record captures. Record normalized
constant-index projections explicitly and extend the existing direct-field
fact boundary for verified tuple projections, preserving its current filters.
Do not recover semantic facts from emitter strings or rerun sema inside MIR.

The following intersecting axes define the first implementation:

| Axis | Coverage | Deferred scope |
|---|---|---|
| Position | Existing ordinary free-function bodies and prefix bindings; M1 branches/loops subject to M2.2 owning-operation limits | Other bodies remain later M2/M3; no new binding lifetime model |
| Shape | Flat empty/singleton/mixed tuples of bool/int32 and borrowed plain records, including readonly access | Nested/owned tuple elements, Optional, union, str/bytes, Own, Ptr/Span, Box/Rc, containers and generic forms remain later M2/M4 |
| Slot | Local tuple payloads and expression temporaries; record members reached by constant tuple index | Tuple parameters, tuple returns, unpack targets, globals, captures, fields storing tuples and container elements remain later M2 |
| Operation | Literal assembly, name copy, whole-tuple reseat, constant-index scalar read, selected record-field read/write | Standalone record extraction bindings, unpacking, tuple calls/returns, owning conversions and backing temporaries remain later M2 |

Element expressions initially use side-effect-free existing scalar operations
or record names; no walrus/call/property evaluation is newly admitted inside
tuple literals. Once the prerequisite is fixed, include borrowed tuple members
pointing at M2.2 owned storage and verify preservation across owning reseats.
Missing or contradictory facts and unsupported nodes anywhere in a body still
produce whole-body `MIRNotCovered`, without rejecting the source program.

Pitfall checks: mutate references after tuple construction/copy; compare scalar,
singleton and mixed forms; preserve readonly access and exact copy/move facts.
New producer facts need method/constructor regression guards despite limited
MIR body admission. Unsupported generic twins remain explicit negative coverage.
Pure operands avoid evaluation-order expansion. No allocation, destruction,
iteration, exception, warning or source-acceptance rule is introduced by MIR.

### Implementation and subsequent wrapper work

Producer probes before implementation found two existing source-admission
limits within the proposed shape list:

- `pair = ()` fails in the parser with `Empty tuple literal is not supported`.
- Given `a: readonly[Cell]`, `pair = (a, 1)` fails at THIR's
  `decl.tuple_literal_shape` gate. The ordinary `a: Cell` twin compiles.
  A readonly element annotation on a tuple capturing a mutable source also
  compiles, but is not evidence that readonly sources work.

The approved scope preserves these existing rejections and exercises
empty/readonly payload invariants at the internal IR boundary. IR support for
these shapes does not imply complete source-to-MIR support. Frontend acceptance
is separate work; M2.3 introduces no new language rule.

1. Fix and fully review tuple-capture loan registration as a separate commit.
2. Add positive THIR capture/projection facts with producer tests, then MIR
   tuple payloads/places/operations, verifier and bounded interpreter together.
3. Use compiler-unit-owned source for semantic tests; never read case files
   from compiler unit tests. Check copies/reseats, duplicate aliases, snapshots,
   access restrictions, branch joins, malformed facts and deferred shapes.
4. Run targeted tests and one final full forced suite, complete defect review
   and readiness gates, update actual coverage docs, and prepare one squashed
   M2.3 implementation commit on the same branch. Do not merge or push.

Optional/unions follow in a separately approved design: model presence or an
active alternative before exposing payloads. Borrowed record Optional values,
owning Optional storage, nullable tuples, pointer-based union alternatives and
recursive union wrappers are distinct representations. Narrowing aliases must
retain the original wrapper/alternative identity; current C++ spellings are
insufficient. Construction, extraction, reassignment and invalidation must use
the same facts. No flat-tuple rule implicitly covers any of these tagged forms.

Confidence: high in the observed tuple payload behavior. The prerequisite's
cross-producer audit is complete and its registration fix is merged. The source
limits above are explicitly retained. Tests include the owned-local example,
using the corrected production storage decision.

Validation: 598 MIR/THIR unit tests passed. The final full forced suite passed
8,394 tests with 23 skips and rebuilt/ran all 4,138 native cases. No existing
source cases, diagnostics or generated-code snapshots changed. Specialist
review covered architecture, safety, codegen, CPython parity, tests, conventions
and docs; closing review resolved all findings. An independent retrospective
accepted the producer facts, typed projections and bounded coverage. This
increment remains analysis infrastructure, with no production MIR consumer or
lifetime proof.

## M2.4: Optional payloads and presence checks

Status: implemented, reviewed and verified. This is an
architectural increment, following M2.3 at `adce74b35b`. Keep normal compilation,
source acceptance, diagnostics, emission and provenance authority unchanged.
Working branch: `mir-optional-payloads`.

```python
def example(a: Cell | None, other: Cell) -> int32:
    current = a
    saved = current
    current = None
    if saved is not None:
        saved.value = 9
    return other.value
```

With an existing plain `Cell` record, this source emits two pointer copies,
clears only `current`, tests `saved` against null and writes its referent. If
`a` and `other` are the same object, the result is 9; if distinct or `a` is None,
`other` is unchanged. The scalar Optional twin copies the contained value, not
the source binding. Readonly restricts the reference, not other aliases' writes.

Design-time runtime probes matched CPython for absent/shared/distinct records,
scalar None/zero/nonzero, bool None/False/True and mutation through a mutable
alias observed by a readonly nullable alias. The type/identity contract needs
no new diagnostic or escape hatch. Check both int32 bounds in implementation
tests as well; general Optional truthiness is outside this proposal.

**Invariant:** an Optional value carries absence or the selected scalar value /
borrowed record identity and capability; payload access requires a presence
fact valid for that exact value, and holder replacement cannot retarget copies
or preserve a stale presence fact.

### Representation survey and split from unions

The existing producer decisions, not `Form` or C++ spelling, must establish:

| Family | Existing representation | Consequence for analysis |
|---|---|---|
| Optional bool/int32 | `std::optional<T>` | Copy presence and scalar snapshot |
| Optional borrowed record | Nullable `T*` / `const T*` | Copy presence and referent identity/capability |
| Owned Optional record | `std::optional<Record>` and explicit borrow conversions | Needs owned payload/backing-storage identity; deferred |
| Optional tuple | Optional containing a tuple, including borrowed tuple members | Needs nested presence plus tuple layout; deferred |
| All-value union | Inline value alternatives | Typed alternative tests and wrapper payload places are needed |
| Reference or mixed union | Pointers for every non-None alternative, even scalar alternatives | Pointer copies are not scalar snapshots; backing storage may be required |
| Recursive union | A wrapper whose layout overrides ordinary union representation | Needs frozen wrapper identity/layout; deferred |

Precedent: extend `thir/lower/storage.py`'s positive immutable facts and the MIR
tuple payload / typed-place verifier pattern. Reuse plain-record identities and
access capabilities. Do not introduce a generic union representation before its
alternative-specific facts exist; Optional is the first two-state payload.

Producer seams identified in the survey:

- `thir/lower/functions.py`: parameter payload representation and capability.
- `thir/lower/statements.py`: Optional declarations and pointer rebinds; the
  whole-Optional assignment path distinguishes copying a wrapper from extracting
  its narrowed payload. A narrowed target's expression type is not its slot type.
- `thir/lower/expressions.py`: `THIRIsNone`, narrowed names and optional field
  receivers; `deref` alone also serves ordinary indirect names and is not a
  sufficient semantic fact. Unproven accesses retain separate runtime checks.
- For the later union increment, `_make_narrow_alias`, `THIRIsinstance` and
  `THIRNarrowedRead` currently retain C++ subject/member strings. Typed original
  wrapper and selected-alternative facts must be added together, including
  branch-entry, early-return and compound-condition extraction paths.

### Bounded scope and verification

| Axis | Proposed M2.4 coverage | Deferred scope |
|---|---|---|
| Position | Ordinary monomorphic free functions, prefix locals, existing if/while/short-circuit CFG | Methods/constructors/globals/closures: remaining M2; new branch/loop bindings, generators/async, comprehensions, with, try/finally, error-return and match: M3 |
| Shape | Optional bool/int32 or borrowed plain record, mutable/readonly; existing unwrapped M1-M2.3 shapes | Union, nested Optional/tuple payloads, owned Optional records, other scalars, str/bytes/views, Own/Ptr/Span/Box/Rc, containers/protocols/recursive aliases; generic forms: M4 |
| Slot | Parameters, entry-declared local holders, expression temporaries; scalar record fields after guarded extraction | Optional results/calls, fields/globals/containers/captures holding Optional, backing storage, parameter reseats |
| Operation | None/member assembly, whole-value name copy and supported reseats, None tests, guarded scalar extraction or record-field read/write | Ownership conversions, effectful constructors/calls, unchecked/unproven access, general truthiness/equality, standalone extracted-record bindings |

1. Add typed THIR facts for Optional layout, whole-value transfers, presence
   tests and payload-source identity. Stamp them where representation and
   access are selected. Preserve existing emitter fields. Missing or contradictory
   facts and unsupported bodies continue to yield whole-body `MIRNotCovered`.
2. Extend MIR slots and operations for absent/present construction, payload
   copying and presence tests. Add a typed payload projection composing with
   existing dereference/field projections. Scalar extraction reads a value;
   reference extraction preserves referent identity and capability.
3. Verify presence at payload access. Track the relationship between a test's
   boolean result and the tested holder/value; overwriting either must not leave
   a usable stale guard. Writes invalidate holder facts, joins retain only facts
   true on every incoming edge, and loops converge conservatively. A successful
   check establishes valid payload selection, not borrow/lifetime safety.
   Runtime-checked unproven accesses remain uncovered until failure edges exist.
4. Test actual emitted THIR from compiler-unit-owned sources. Required witnesses:
   None and present values (including False/zero), scalar snapshots, shared and
   distinct record identities, copied holder cleared/reseated independently,
   readonly aliases observing mutation, guard/early-return/loop exits, stale
   boolean tests after reseat, branch joins, and malformed layout/capability facts.
   Producer tests include methods/constructors without admitting those MIR bodies.
5. Add explicit negative coverage for the deferred families and slot positions;
   reuse the existing bool/int32/record/tuple witnesses beside their Optional
   twins. Run targeted tests, specialist review and readiness gates, then a full
   forced suite; prepare one squash commit on the branch, without merging/pushing.

The presence work is deliberately a small prerequisite for tagged payloads,
not the M3 liveness/loan analysis. Its dataflow should use finite per-slot facts
and a worklist, never enumerate paths. Preserve test provenance through admitted
boolean operations or conservatively decline coverage when it cannot be verified.

### Source admission, risks and follow-on work

Producer probes confirm the example above, scalar Optional copies/reseats,
readonly nullable-record reads after mutation through another alias, and a loop
that clears a nullable holder already emit. Some other source forms still stop
before MIR: reseating a nullable local from a plain record parameter hits
`decl.reseat_param_source`, and the analogous union example hits
`decl.union_reseat_source`. Keep those rejections and pin them; where necessary,
test the corresponding admitted IR operation directly. Do not claim that an IR
operation makes every source spelling available.

Risk: stale narrowing after holder replacement or a join is more serious than
a missing shape. Require verifier-negative tests that still fail when lowering
facts falsely claim a payload is present. Do not relax a check or reinterpret a
record borrow as a copy to fit existing THIR metadata.

Pitfall audit: mutation witnesses cover alias/copy semantics; singleton/mixed
tuple and Optional-of-tuple twins are explicit exclusions; shared producer facts
cover position symmetry without widening body coverage; tests retain conditional
evaluation; generic twins remain unsupported. No new owned views, allocations,
copy warnings, C++ formatting or user diagnostics are intended. Readonly flows
through every payload operation; iteration slots and cleanup remain M3 work.
Thus no new Python rejection or warning is justified by partial MIR coverage.

Actual coverage is recorded in `LANGUAGE_FEATURES.md` and `ARCHITECTURE.md`;
`TODO.md` retains the excluded cells. Source-owned compiler unit tests use the
emitted THIR directly, including method/constructor producer checks without
admitting those body kinds to MIR. Direct IR tests cover record-member assembly
behind the existing source gate, capability transfer directions and malformed
operations. No compiler test reads the snippet-test corpus. The other direction
holds: the snippet harness runs MIR over every case that reaches codegen
(`tpyc/mir/collect.py`: one `MIRBodyVerdict` per body, the record `--dump-mir`
and the coverage tool also render), so a MIR exception fails the case; every
body's verdict line (`MIRBodyVerdict.describe()`) is snapshotted in the case's
`expected/mir.txt`, so a body that stops being covered, or changes verdict,
is snapshot churn in the regular suite, with no full-corpus `--dump-mir`
compare; and a case pins a
body's verdict with `# tpyc: mir(...)` / `mir_summary(...)` on its `def` line.
A statement line pins exact per-line facts with the line-level fact family
(`mir_owned` / `mir_borrowed` / `mir_copy` / `mir_write` /
`mir_borrows(x, a|b)`, names selectable as `param(x)` / `local(x)`), which
one producer, `collect.line_facts`, derives from the verdict's analyses and
the harness only reads. The verdict family pins OUTCOMES per body; the fact
family pins, per line, slot kinds, a holder's possible origins, copies and
non-initializing writes. Unit tests keep what neither expresses: a copy's
source and `may_raise`, passings, initialization vs replacement modes,
region lifetimes, operation order, and the ABSENCE of a copy.
There is no MIR dump snapshot per case, for the reason THIR has none: a dump
pins the IR's spelling, not a fact about the program.

The implementation records `THIROptionalLayout` on parameters and selected
holder writes, and `THIROptionalRead` on whole-wrapper or extracted name reads.
MIR construction, copy and presence-test operations share the same payload
layout. Payload places compose with record dereference/field projections.
Presence verification uses a finite worklist domain with unconditional holder
facts and sparse boolean implications. Joins reconstruct each predecessor's
full outcome before intersection; holder writes invalidate old implications.
This avoids duplicating unrelated unconditional facts in every boolean result;
it does not claim linear complexity for arbitrary CFGs.

The initial specialist review found a scaling issue in duplicated presence
facts and two coverage gaps: narrowed whole-wrapper reassignment and readonly
construction/copy directions. All three are addressed, with regression tests
for sparse state, predecessor-derived join proofs and both transfer directions.
Targeted verification passed 668 MIR/THIR tests. Independent codegen probes
emitted byte-identical C++ at base and head and matched CPython at runtime.
Closing review is clean across architecture, safety, codegen, CPython parity,
tests, docs and conventions, including the final union source-gate test. An
independent retrospective accepted the design and its explicit coverage limits.
The final forced suite passed 8,465 tests with 23 skips and rebuilt/ran all
4,138 native cases; no existing source, diagnostics or generated-code snapshots
changed. Normal compilation and provenance authority remain unchanged.

## M2.5: nonrecursive union selection and extraction

Status: implemented, reviewed and verified; ready to merge. Architectural scope,
following M2.4 at `f22952127f`. Branch: `mir-union-payloads`. Normal compilation,
source acceptance, diagnostics, C++ emission and provenance authority stay
unchanged. This increment models already-emitted operations in analysis-only MIR.

With existing plain `Cell` and `Other` records holding `value: int32`:

```python
def example(current: Cell | Other, other: Cell) -> int32:
    saved = current
    if isinstance(saved, Cell):
        saved.value = 9
    return other.value
```

The borrowed union copies its discriminator and selected pointer; the narrowed
branch binds `auto&` to the selected object. Shared `current`/`other` returns 9;
distinct objects leave `other` unchanged. A direct readonly union parameter
can observe a write through another mutable alias. An all-value union copy
instead copies its bool/int32 payload independently of later holder replacement.
Design-time TPy/native and CPython probes matched for shared/distinct records,
direct readonly observation, scalar copies/replacement and bool/int32 tests.

**Invariant:** a union carries a typed alternative and its selected value or
borrowed identity/capability; extraction requires a current alternative proof,
and an extraction alias preserves the actual selected referent or storage place
rather than following a subsequently replaced holder.

### Representation and alias audit

| Producer decision | Existing emitted behavior | Required MIR meaning |
|---|---|---|
| All-value bool/int32 union | Inline values; value-union parameters bind by const reference | Local wrapper copies snapshot values; parameters cannot be reseated in this subset |
| Borrowed plain-record union | Tagged pointers, including pointee constness | Wrapper copies preserve referent identity and capability |
| Borrowed `THIRNarrowAlias` | Reference initialized from the selected pointer once | Capture the selected reference in its own binding |
| Value `THIRNarrowAlias` | Reference to the selected subobject inside wrapper storage | Retain a payload-place alias; do not eagerly snapshot a scalar |
| `THIRNarrowedRead` | Fresh extraction in a compound condition | Live wrapper projection, checked at each read |
| None in a genuine union | A separate discriminator with no payload | Excluding None leaves a set of alternatives, not necessarily one member |
| Mixed scalar/reference union | Every non-None alternative is a pointer, including scalars | Deferred: needs borrowed scalar/backing-storage identity |
| Owned/storage or recursive union | Payloads live inside a wrapper; recursive layout has registry identity | Deferred: do not classify from `UnionType` alone |

The deciding sites are `thir/lower/functions.py` for parameters, union
declaration/reseat arms in `thir/lower/statements.py`, `_make_narrow_alias`,
and `_lower_isinstance_cond` / `THIRNarrowedRead` producers in
`thir/lower/expressions.py`. Branch-entry, early-return, while and compound
condition paths must share the same typed facts. Freeze the original wrapper's
full alternative identities before narrowing; never derive them from emitted
`variant_cpp`, `member_cpp`, alias spelling or a narrowed subset's indices.

### Approved implementation

1. Add immutable THIR union layout, transfer, test and extraction facts at the
   existing representation/access decisions. Keep emitter fields intact. Facts
   identify the source binding, full layout, checked alternatives and extraction
   mode. Validate capability transfers, including source versus destination
   decisions; missing or contradictory facts cannot authorize MIR coverage.
2. Add typed MIR union slots, construction/copy, alternative tests and payload
   projections. Borrowed extraction captures a reference into a distinct holder;
   value extraction binds a typed wrapper-payload place. Give extraction
   bindings body-scoped identities and preserve lexical scope, including aliases
   recreated on loop entry. This is internal alias binding, not admission of
   arbitrary source declarations inside branches.
3. Extend the existing Optional finite-worklist selection engine to union
   alternative sets. Guards intersect possible alternatives; joins union them;
   unknown means the full declared set, and an empty set makes the edge
   unreachable. Keep boolean implications sparse and
   invalidate them on holder writes. Optional remains its two-state case with
   all existing regression tests. Do not introduce a second competing analysis.
4. Track value-alias coverage validity separately from discriminator facts.
   Conservatively invalidate its existing payload aliases on wrapper replacement;
   C++ can preserve the subobject on some same-alternative assignments, but this
   increment does not prove that case. A later successful test,
   even for the same alternative, must not revive them. A subsequent use of
   such an alias makes the whole body uncovered in this increment. Re-extraction
   creates a valid binding; joins retain validity only when it holds on every
   reachable predecessor. A captured borrowed-record identity survives wrapper
   replacement. This verifies selection/storage binding, not general lifetime
   or loan safety.
5. Test emitted THIR from compiler-unit-owned sources, then malformed internal
   IR and excluded shapes. Complete specialist review, readiness and a final
   full forced suite. Prepare one squashed commit; merge/push remain separate.

### Scope matrix

The axes below factor the Cartesian product. A cell is covered only when every
axis is covered; all other cells remain whole-body `MIRNotCovered`, assigned to
the listed later stage. Existing M1-M2.4 coverage remains available unchanged.

| Axis | Covered cells | Filed later scope |
|---|---|---|
| Position | Ordinary monomorphic free functions; existing if/while/short-circuit/early-return CFG | Methods, constructors, module statements, closures: remaining M2; generators, async, comprehensions, context managers, try/finally, error-return, match: M3 |
| Shape | Nonrecursive bool/int32-only value unions or flat borrowed plain-record unions; either family may include None; selected mutable/readonly capabilities | Mixed scalar/reference, other scalars, tuple/Optional payload nesting, str/bytes/views, Own, Ptr/Span, Box/Rc, containers, protocols, recursive wrappers: remaining M2; generics: M4 |
| Slot | Read-only union parameters, entry-declared local wrappers, expression temporaries, internal extraction aliases; scalar fields of extracted records | Parameter reseats; union returns/call boundaries, fields, container elements, globals, captures, owned backing storage: remaining M2; general branch/loop-created source bindings and frame slots: M3 |
| Operation | Whole-wrapper copies and supported local reseats; member/None construction without effects or new backing storage; typed isinstance/None tests; guarded scalar reads and record-field reads/writes | General equality/truthiness, effects/calls, runtime-checked extraction, widening/conversion across different layouts, writes through scalar payload aliases: later M2/M3 |

Readonly refers to the selected payload capability, including inferred constness.
Direct readonly parameter extraction is included, and so is copying a
readonly record union into a local, which binds a const pointer union
(`::tpy::Union<const Cell*, const Other*>`). Readonly
construction/copy directions are covered by internal IR tests. Scalar-union parameter
reseats currently emit writes through a const reference
(`BUGS.md#scalar-union-parameter-reassign-const`) and remain excluded,
recorded for a separate fix with user approval.
No frontend gate is relaxed to manufacture a source witness.
Direct record-member construction and reseating are likewise tested at the
internal IR boundary: their source forms still hit `decl.ptr_union_source` and
`decl.union_reseat_source`. Negative source tests pin both existing gates.

### Tests, pitfalls and risks

Use existing bool/int32/record/tuple/Optional tests as neighboring regression
coverage. New compiler-owned sources cover shared/distinct record mutation,
readonly observation, scalar copy independence, local reseats, every alternative,
None, bool False/True and int32 bounds, single/multiple-member tests and their
complements, short-circuit reads, early-return aliases, branch joins and loops.
Use explicit `int32(...)` runtime witnesses when testing that discriminator:
the CPython stub's distinct subclass behavior is already tracked in
`BUGS.md#isinstance-int32-cpy-stub-subclass`.

Internal tests must distinguish a captured reference from a live projection,
and a scalar payload alias from an eager scalar copy. Include holder replacement
after extraction, stale boolean tests, same-alternative replacement, alias
recreation on loops, conflicting joins, readonly access escalation, missing
facts, mismatched alternative identities and invalid layouts. Keep unsupported
source gates pinned; use internal IR where the frontend cannot express a cell.
No production compiler code reaches into snippet-test directories.

| Pitfall | Design obligation |
|---|---|
| silent-copy-vs-alias | Mutate shared records after union copy/extraction and observe through each alias; inspect pointer/reference emission |
| copy-warning-at-wrong-site | No warning/emitter changes; reference extraction adds no copy |
| tuple-equals-scalar | Keep flat-tuple regressions; union-in-tuple and tuple-in-union stay explicitly uncovered |
| same-construct-every-position | Stamp facts at shared producer decisions; matrix excludes positions whose complete bodies are not modeled |
| conditional-operand-evaluates-in-place | Extract only on the guarded CFG edge; probe compound conditions and loop reevaluation |
| generic-equals-monomorphic-twin | Open/instantiated generic obligations remain M4, not inferred from concrete coverage |
| view-not-copy; hidden-allocation | Views/backing-storage families excluded; verify byte-identical emission, no new allocations |
| const-source-const-loop-var | No foreach admission; direct and inferred readonly capabilities cannot increase |
| generated-cpp-readability | C++ emission unchanged; new internal MIR dumps remain deterministic and readable |
| no-cpp-in-diagnostics; no-internal-names-in-diagnostics | No new source diagnostics; internal coverage reasons do not become language errors |
| reject-valid-python-only-as-documented-divergence; no-warning-on-valid-code | Coverage failure preserves source behavior; adjacent build defects need separate fixes |

The main risks are accidentally re-deriving representation from union types,
confusing alias capture with projection, reviving invalid scalar aliases, and
duplicating the selection engine. Use a finite per-holder/per-alias domain and
sparse conditional facts, not path enumeration. Check widening loop joins and
state size; make no blanket linear-time claim for arbitrary CFGs.
Scalar-union parameters retain their const-reference binding distinction even
though excluded effects/reseats make it unobservable in this subset. Future
effect or parameter-write support must preserve that storage identity.

An independent design review found no blocking contradiction after clarifying
conservative value-alias validity, its joins/re-extraction, and unreachable
alternative sets. Confidence in the bounded design is high; mixed/owned storage
and broader effect/lifetime behavior are explicitly outside that assessment.

Documentation at implementation: update this section, `ARCHITECTURE.md`,
`LANGUAGE_FEATURES.md`'s internal MIR status and the existing MIR TODO entry.
No parser, language type rules, runtime, stdlib or C++ emitter change is proposed;
existing snapshots should remain byte-identical. The Optional tuple-unpack
metadata defect remains separate and must be fixed before admitting that shape.

### Implementation checkpoint

THIR carries original union layouts, whole-name reads, literal-construction
facts, alternative-test sets and extraction-source identities. The facts are
recorded by existing declaration/parameter and narrowing producers; inline
condition reads carry the same source/alternative facts as branch aliases.
MIR has tagged construction/copy, set-membership tests, live payload projections,
captured borrowed references and scalar payload-place alias slots. Scoped alias
bindings receive body-local IDs even when separate C++ scopes reuse a spelling.

The Optional selection worklist now tracks finite alternative sets and sparse
boolean implications. Scalar-alias coverage validity is separate and intersects
at joins; holder replacement kills it, and explicit re-extraction restores it.
Readonly-to-mutable transfers and malformed layouts, tests, literal facts or
extraction bindings are rejected. Existing source gates, including narrowed
union reseats and the two separately filed codegen defects, remain unchanged.

Compiler-owned tests cover source producers, mutation through shared identities,
copies surviving replacement, bool/int32/None alternatives, guarded field reads,
multiple-member tests, early return, compound conditions and loops. Internal IR
tests distinguish frozen record identities from scalar storage aliases, check
stale guards and aliases, and exercise access directions and malformed inputs.
The first combined MIR/THIR and union snippet run passed 1,064 tests without
snapshot updates. Specialist review found and prompted a fix for direct scalar
alias operands bypassing record-construction validation, a single-pass reverse
alias index, and additional internal record-member construction, scalar compound-read
and complement-alternative witnesses. Independent native/CPython probes matched
and produced byte-identical C++ against the base.

Closing cumulative review from `f22952127f` through `ff605cd114` was clean across
architecture, safety, codegen, CPython parity, test coverage, conventions and
docs. Meta-review and the readiness retrospective accepted the bounded design;
no code changes followed that review. The final `rpytest --force-exec` passed
8,568 tests with 23 skips in 292.55 seconds: all 4,152 executable cases built and
ran, with zero execution-cache skips. Existing snapshots remained unchanged.
Only this factual completion record followed verification. Prepare the reviewed
tree as one squashed branch commit; merging into master remains separate.

## M2.6: borrowed nested record places

Status: implemented and verified for the autonomous M2.6-M2.8 batch.
M2.5 landed on master as `2cb3c41d3f`. This is an architectural extension of
the analysis-only THIR/MIR contract, not a new source feature or a lifetime
checker. Existing acceptance, diagnostics and C++ emission stay unchanged.
The whole-field replacement and nested owning operations this increment
excludes are the [nested records](#nested-records) rules of B3.

### Observable contract and evidence

Given `Outer.inner: Cell` and `Cell.value: int32`, both ordinary reference
records, this already compiles:

```python
def inspect(outer: Outer) -> int32:
    saved = outer.inner
    outer.inner.value = 7
    return saved.value
```

The emitted core is `Cell& saved = outer.inner; outer.inner.value = 7;`.
MIR should model the same shared storage and return 7. If a local holder
`current` is reseated from one Outer to another after `saved = current.inner`,
`saved` must continue referring to the first Outer's inner storage.

**Invariant:** an admitted field borrow captures the selected inline record
subobject at that operation; later holder reseats cannot retarget it, scalar
leaf writes remain observable through aliases, and access cannot become more
mutable along a field path.

Design probes on `2cb3c41d3f` established:

- Native TPy and CPython agree for the direct example (7), a method twin (8),
  staged nested aliases (17), parent-holder reseating (8, 8, 11), and readonly
  reads after writes through another alias (13). Optional and union narrowed
  record roots also reach nested scalar fields without emission changes.
- Longer scalar paths such as `root.outer.inner.value` already emit chained
  member access. Rebinding a field-derived holder emits `Cell* current =
  &(a.inner)` followed by `current = &(b.inner)`. A tuple built from an already
  bound field alias stores a pointer to that same field.
- Some source spellings still reject before MIR: `saved = root.outer.inner`
  and `saved = pair[0].inner` hit the THIR local-declaration gate. The staged
  `parent = root.outer; saved = parent.inner` form compiles. Preserve these
  source limits; do not widen the frontend to fill the MIR scope matrix.
- Borrowed tuple locals containing a non-copyable record retain the existing
  sema gate. The tuple mutation witnesses use copyable records; a separate
  direct-field witness verifies borrowing a non-copyable inline record.
- Whole-field replacement is a distinct boundary. With a saved alias to a
  field containing 13, `outer.inner = Cell(19)` leaves the alias reading 19
  in TPy and 13 in CPython. TPy emits the existing located field-borrow
  mutation warning. This warned behavior is part of the documented inline
  storage model, not a new defect to file; whole-field replacement is excluded.
- Replacing a local owning Outer after taking a field alias currently chooses
  another backing slot and preserves the old value in the probe. That is
  evidence about existing emission, not permission to admit nested ownership.

### Existing authorities and proposed extension

1. **Extend the existing semantic field facts.** `THIRFieldIdentity` already
   carries qualified declaring owner, source field name and declared type.
   Extend `storage.direct_field()` to positively classify bool/int32 leaves
   and inline plain-record fields using the existing `borrowed_record()`
   eligibility rules. Preserve its exclusions for properties/hidden calls,
   user dereference, native and interior-mutable fields. Both the ordinary
   expression field producer and `_lower_field_source()` must attach these
   facts; the latter currently emits STORAGE-form fields without them.
   Receiver recursion consumes the actual THIR expression, never C++ text.

2. **Record storage borrowing explicitly at the selected binding operation.**
   Add `storage_borrow: THIRBorrowedRecord | None` to the relevant existing
   declaration/rebind statement families. It records destination type/access;
   the source remains exclusively in the statement's existing `init`/`value`.
   It is mutually exclusive with `alias_binding` and `owned_storage`.
   Name-to-name aliases retain their existing fact and `MIRAlias` operation.
   This avoids duplicating the source as a second path representation or
   embedding a second expression tree inside metadata. Stamp the new fact
   only where lowering already selected a borrow, including the pointer-form
   declaration and reseat siblings; do not infer it from `Form` alone.

3. **Borrow a MIR place.** Generalize `MIRBorrow.source` from a storage slot
   to `MIRPlace`, adapting existing owned-scalar-record borrows to empty-path
   places. Resolve the source at execution of the borrow. A path rooted in
   an Outer holder is `Deref(holder), Field(inner), Field(value)`; inline
   record fields do not add another dereference. A borrowed field endpoint
   produces a holder of that selected storage identity. Reuse existing
   `MIRPlace`, `MIRField`, operand enumeration and deterministic dump patterns.

4. **Validate type, storage category and access throughout the path.** A
   scalar field ends traversal; an eligible inline record field continues as
   record storage. Effective readonly is inherited from the receiver or the
   declared field type; the accessed result type may consequently differ from
   the declaration by readonly qualification. Preserve explicit malformed-IR
   checks for foreign owners, inconsistent field types, invalid dereferences,
   incompatible borrow destinations and increased access. A shared place
   classification should serve reads, writes and borrows. Admit scalar leaf
   writes only; a record endpoint is not permission for record replacement.
   Presence/alternative validation must inspect place-valued borrow sources
   as well as scalar reads: borrowing a field through a wrapper needs a current
   selection proof. Once captured, the record reference survives reseating
   that borrowed wrapper just as an existing record extraction does.

5. **Keep borrowed paths independent of constructor eligibility.** Existing
   `MIRDefinitions` verifies owning scalar-record constructors. Borrowed nested
   access must not require such a constructor, and must not relax that verifier
   to admit nested construction/copy/move. Field facts supply the path's schema
   just as direct borrowed scalar-field facts do today. No new full-record
   registry or runtime representation is needed for this increment.

6. **Represent subobject identity in the test interpreter.** Extend the flat
   scalar heap model with nested inline storage and stable subobject references.
   Borrowing a field captures its containing storage identity and selected
   subobject, not a source-holder expression to re-evaluate later. Distinct
   owners' same-named fields remain distinct; aliases of one owner share them.
   Do not model inline fields as independently replaceable pointer slots, and
   do not let the interpreter's existing shallow copy stand in for nested
   owning copy semantics. Those operations remain outside coverage.

Parser, sema rules, type-system rules, C++ rendering, runtime and stdlib need
no behavior changes. Expected implementation sites are THIR nodes, storage
fact helpers, the field and binding producers, THIR validation, MIR nodes,
lowering, place/presence validation, operand/dump consumers and the test interpreter.
Definitions need only regression protection against accidentally widened
constructor eligibility. Construction/validation should remain proportional
to the nodes and field-path lengths actually traversed.

### Factored scope matrix

These axes compose; a cell is covered only when every axis is admitted. The
existing M1-M2.5 coverage remains available. All other cells stay explicit
whole-body `MIRNotCovered`, or retain their existing earlier frontend gate.

| Axis | Proposed M2.6 coverage | Deferred, tracked under the remaining increments |
|---|---|---|
| Position | Ordinary monomorphic synchronous free functions; existing prefix bindings and if/while/short-circuit CFG | Methods, constructors as bodies, module statements and closures: remaining M2; generator, async, comprehension, context-manager body, try/finally, error-return and match arms: M3; generic twins: M4 |
| Shape | Plain reference records with nested inline plain-record paths and bool/int32 leaves; mutable/readonly access | Value/native/inherited/protocol records; str/bytes, Own, Ptr/Span, Box/Rc, containers; tuple/Optional/union stored inside fields: later M2/M3; recursive owning layouts are not introduced |
| Root | Borrowed parameters and name aliases; already-covered local tuple-element, Optional and union borrowed-record roots compose with field traversal | Tuple parameters, unproven wrapper extraction, global/capture/container-element roots and calls remain excluded; no new wrapper-source admission |
| Destination | Entry-declared field-derived holders; reseats of existing holders; scalar locals/temporaries and scalar field leaves | New branch/loop bindings and frames: M3; reference returns, field/container/global/capture stores: later M2/M4 |
| Operation | Borrow an inline field; copy a reference holder; reseat a holder; scalar leaf reads/writes; existing wrapper captures of already-bound record names | Whole-field or containing-record replacement; nested owning construction/copy/move/destruction; direct field-to-wrapper capture beyond current producers; reference equality and calls/effects |

Producer facts should be checked in method and constructor twins even though
MIR continues to decline those body kinds. Non-admitted record shapes and
body positions need explicit negative coverage, not fabricated scalar facts.

### Tests, pitfalls and implementation gate

- Use compiler-owned source fixtures, lower the exact emitted THIR and compare
  MIR execution with expected shared mutations: direct/staged nested paths,
  distinct/shared owners, holder reseats before and after capture, conditional
  reseats, scalar snapshots, and scalar leaf writes in existing loops.
- Cover readonly receiver/field paths, reading through a readonly alias after
  another alias writes, and rejecting any mutable borrow from readonly storage.
  Include a non-copyable record witness and scoped wrapper-root tests with
  current presence/alternative proofs. Local singleton/mixed tuples holding an
  already-bound field alias must retain that alias and observe later mutation.
- Test invalid field owners/types, scalar traversal, double dereferences,
  mismatched binding facts and access escalation directly at THIR/MIR boundaries.
  Pin frontend rejection of the observed unsupported alias spellings separately
  from MIR coverage failure. Whole-record writes and nested owned operations
  must fail coverage even when a related borrowed path is supported.
- `silent-copy-vs-alias`, `tuple-equals-scalar` and `copy-warning-at-wrong-site`:
  mutation witnesses and unchanged C++/diagnostics protect each boundary.
  `same-construct-every-position` and `generic-equals-monomorphic-twin`: check
  shared producer facts at sibling positions; unadmitted bodies stay explicit.
  `conditional-operand-evaluates-in-place`: no borrow or field read is hoisted
  above its existing guard. Calls/properties remain excluded.
- `view-not-copy`, `hidden-allocation` and `const-source-const-loop-var`:
  view/container/frame operations remain excluded; inline subobjects add no
  allocation. `generated-cpp-readability`: existing emission is unchanged.
  Both diagnostic-name pitfalls and both valid-Python diagnostic pitfalls:
  no new source errors, warnings or internal-name leaks; missing MIR coverage
  cannot reject a program or authorize a safety proof.
- Update the internal MIR status in `LANGUAGE_FEATURES.md`, `ARCHITECTURE.md`
  and the existing TODO entry when implemented. Add one condensed native/CPython
  mutation case if existing case coverage cannot pin the emitted source forms;
  keep graph and malformed-IR tests in `tpyc/mir/`. No existing snapshot changes
  are expected. Finish targeted tests, specialist review/readiness, and one
  full forced suite before preparing a single squashed implementation commit.

CPython-parity design assessment is clean for the proposed boundary. The
warned whole-field replacement difference stays excluded. Related tracked
gaps include `BUGS.md#iter-borrow-place-needs-hops` and
`BUGS.md#optional-tuple-unpack-readonly-fact`; this increment does not fix them.
Confidence is high for the borrowed-only contract, supported by emitted THIR,
native/CPython probes and the sibling survey. The autonomous batch authorizes
the new storage-borrow fact and place-valued MIRBorrow; nested owning/replacement
operations remain a separate design decision.

Completion gates: all seven review lenses are clean, including a closing review
after strengthening the actual nested-constructor exclusion and wrapper-clear
execution tests. The full forced suite passed (8603 passed, 23 skipped; 4154 C++
cases built and ran). Existing case sources and snapshots are unchanged. The
readiness retrospective accepts the fact/place design and bounded ownership
scope; this remains internal analysis, with no production provenance authority.

### Autonomous M2.6-M2.8 batch

The requested deliverable is one branch with three sequential, individually
reviewed and squashed commits. Do not merge to master or push. Each iteration
includes its design record, implementation, tests, docs, defect review and
readiness gate. Preserve intermediate working branches when constructing the
final stack; no amend, rebase or destructive cleanup is needed.

- M2.6: the borrowed nested-record-place contract above.
- M2.7: investigate and implement a bounded tuple-borrow extension, reusing
  place-valued borrows for standalone record element captures and selected
  borrowed tuple parameters. Preserve existing source admission and document
  exact supported forms before implementation.
- M2.8: investigate and implement ordinary synchronous method-body coverage,
  carrying receiver identity/access explicitly and reusing the established
  operations. Constructors, effects/call summaries and resumables are excluded.

The latter two boundaries remain subject to their source/THIR investigations;
routine design choices within the existing analysis-only architecture are
delegated. Stop for blockers or changes to language behavior, source acceptance,
diagnostics or the agreed architecture. Existing snapshot changes require
approval. This batch does not claim completion of M2.

## M2.7: flat tuple parameters and selected element borrows

Status: implemented, verified and reviewed under the autonomous batch
authorization. This extends the internal
analysis contract only. Source admission, C++ and diagnostics stay unchanged.

The contract is an already accepted capture from a borrowed tuple parameter:

```python
def inspect(pair: tuple[Cell, int32]) -> int32:
    saved = pair[0]
    pair[0].value = 7
    return saved.value
```

The signature borrows a tuple containing `Cell*`, and `saved` binds the
dereferenced element by reference. MIR must return 7. A scalar tuple member
is a value snapshot. Capturing the record selects its current referent, not a
tuple slot to follow later. An immutable tuple wrapper does not make its
record pointees readonly; explicit payload readonly and the existing semantic
deep-const decision determine access.

Native/CPython probes agree for mixed/singleton parameter captures and readonly
captures observing another alias's mutation. A mixed-access parameter emits
`const std::tuple<const Cell*, Cell*>&`, confirming the two constness axes.
Standalone capture from a tuple local, and a parameter-element capture whose
destination is later reseated, retain their existing `decl.slot_type` gate.
Copying a borrowed record-tuple parameter into a later-reseated tuple local also
retains that gate; non-reseated copies and scalar tuple copies compile. These source forms remain
excluded rather than changing the frontend for MIR completeness.

Auto copies from inferred or whole-tuple readonly record parameters, alias
chains and method and constructor siblings included, carry the parameter's
readonly payload in their local fact, as their C++ auto copies keep its
constness: the copy's record is a slot of the binding planner's const fixpoint,
const when every source bound into it is. MIR covers them in free functions and
methods, beside mutable and correctly annotated mixed payload copies; a
constructor body's tuple local stays uncovered whatever its access
(`unsupported metadata: tuple_layout`).

Implementation follows M2.3 tuple layouts and M2.6 place-valued borrows:

- Add an optional tuple-layout fact to THIR parameters at the shared function
  and constructor producers. Record only flat bool/int32 and borrowed eligible
  plain-record elements. Exclude whole/per-element Own before stripping type
  wrappers; use semantic element/deep constness, never rendered C++.
- Extend the existing storage-borrow fact only for the positively selected
  tuple-parameter alias source: normalized constant index, borrowed record
  element and the producer's existing dereference decision. Its source remains
  the actual initializer; name aliases and field borrows retain their operations.
- MIR consumes the parameter layout and lowers capture as tuple index followed
  by dereference. Storage reached through a borrowed holder needs no owning
  constructor layout; actual owning operations still do. Forbid tuple parameter
  replacement separately from tuple-element replacement.
- Reuse tuple compatibility and typed projection validation, normalizing the
  semantic Ref wrappers carried by parameter types. Validate conflicting facts,
  element access, declaration type and capture representation. No new runtime,
  parser, sema, emitter or effect model is required.

| Axis | Covered | Excluded / later work |
|---|---|---|
| Position | Ordinary monomorphic synchronous free functions; shared producer facts checked in methods/constructors | Method bodies M2.8; constructor/module/closure bodies later M2; resumables/comprehension/context-manager/try-finally/error-return/match M3; generic bodies M4 |
| Shape | Flat bool/int32 and borrowed plain-record tuple parameters; singleton/mixed tuples; mutable/readonly elements | Nested/owning/wrapper elements, str/bytes, native/value/protocol/generic records, Ptr/Span/Box/Rc: remaining M2/M3/M4 |
| Slot | Parameter reads, existing local tuple copies/reseats, entry record captures and scalar locals | Tuple parameter replacement, returns/unpack/field/container/global/capture slots: remaining M2/M4 |
| Operation | Constant/negative index normalization, scalar snapshots, record capture and shared field mutations, existing CFG | Effectful elements, calls, dynamic indexing, frontend-rejected local/reseated captures: later increments |

Tests use compiler-owned source fixtures and one condensed native/CPython case.
They must observe mutation after capture, distinguish scalar snapshots, cover
singleton/mixed/duplicate referents, explicit/inferred readonly, negative indices,
and parameter-to-local tuple copies. Malformed IR tests reject parameter writes,
access escalation, incompatible facts and wrong capture forms. Pin existing
frontend gates and non-admitted ownership/body shapes. Existing native snapshots
must remain byte-identical; no refresh is planned.

Pitfalls: mutation witnesses cover silent-copy-vs-alias and tuple-equals-scalar;
shared producers cover same-construct-every-position while generic admission is
explicitly deferred. Readonly comes from semantic facts (const-source-const-loop-var).
Effectful sources remain excluded (conditional-operand-evaluates-in-place).
No view/container/allocation operations or new warnings/rejections are added,
so view-not-copy, hidden-allocation, copy-warning-at-wrong-site, diagnostic-name
and valid-Python diagnostic obligations reduce to unchanged emission/admission.
The sibling survey and independent parity design assessment found no blocker
within this boundary. Finish review/readiness and a full forced suite before
adding M2.7 as its own squashed commit on the final stack.

Completion: 792 focused MIR/THIR and tracking checks passed. The full forced
suite passed with 8624 tests, 23 skips and 4155 C++ cases built and run; the new
case also matched CPython. Seven specialist lenses found no code defect; the
docs review clarified the older readonly local-tuple construction exclusion.
The independent retrospective accepted the producer facts and bounded coverage.
Existing snapshots remain unchanged. The only deferred defect is the tracked
readonly auto-copy fact mismatch above, with a safe-rejection regression guard.

## M2.8: ordinary instance-method receivers

Status: implemented, verified and reviewed under the autonomous batch
authorization. This is an architectural metadata extension within the existing
analysis-only boundary.

The contract uses an already accepted method body:

```python
def update(self) -> int32:
    saved = self
    saved.value = 7
    return self.value
```

The generated C++ binds `saved` to `*this` by reference. MIR receives the same
record identity as an explicit borrowed receiver parameter and returns 7.
The receiver's access comes from finalized `func.is_readonly`, the verdict
used by the method signature. Inferred readonly receivers can have an
unqualified THIRSelf result type; neither that type nor body-read inspection
is a substitute for the finalized verdict.

Extend THIRFunction with an optional borrowed-record receiver fact at the
ordinary callable producer. Reuse `borrowed_record` eligibility and the
existing alias-binding operation, including its shared constructor producer.
No synthetic THIRParam is needed: the emitter already owns implicit receiver
emission. MIR requires the fact for METHOD and its absence for FREE_FUNCTION,
then seeds a non-reseatable borrowed parameter named self. THIRSelf maps to
that identity explicitly, never by parsing C++ text. Tuple captures use the
same reference-source mapping as scalar aliases. Existing place/access and
CFG rules apply unchanged.

Eligibility is deliberately positive: ordinary synchronous monomorphic
non-consuming instance methods on eligible plain records. Properties,
dunders, static/class methods, overload specializations, auto-own and
auto-readonly twins, generic/native/value/inherited/protocol records,
constructors and resumables remain excluded. Missing receiver metadata does
not grant free-function eligibility. Unsupported statements still reject
the whole body. No call effects or interprocedural checking are introduced.

| Axis | Covered | Excluded / later work |
|---|---|---|
| Position | Ordinary instance methods, prior free functions; shared self-alias facts checked in constructors | Constructor/module/closure bodies later M2; generator/async/comprehension/context-manager/try-finally/error-return/match M3; generic bodies M4 |
| Shape | Eligible plain-record self, mutable/inferred/explicit readonly; scalar/singleton/mixed tuple aliases and existing payload operations | Consuming and generated receiver twins, native/value/inherited/protocol/generic records; other type families retain prior M2/M3/M4 gaps |
| Slot | Receiver parameter, entry locals, existing scalar field projections and scalar returns | Receiver reseat; record/tuple returns, globals/captures/container elements: remaining M2/M4 |
| Operation | Shared mutation, local alias reseat, nested fields, prior CFG | Calls/effects, constructor body initialization and all prior uncovered operations |

The sibling survey traced ordinary/module/constructor/resumable producers and
generated method twins. Independent native/CPython probes agreed on scalar,
singleton and mixed tuple captures, shared/distinct readonly observers and
local alias reseating. Explicit readonly also constrains ordinary parameters;
the mutable-writer witness therefore uses inferred readonly self. Existing
snapshot output must remain unchanged.

Implementation probes pinned two inherited boundaries: `saved: Cell | Other =
self` fails the frontend's `decl.ptr_union_source` gate, whereas `saved: Cell |
None = self` compiles but selects a pointer initializer outside M2.4's existing
Optional declaration subset. Both remain outside M2.8. The former rejects valid
source and is tracked as `BUGS.md#self-record-union-initializer`; fixing the
shared union initializer family would change source admission, outside this
analysis-only batch. Methods receiving Optional/union parameters still reuse
the previously supported wrapper operations.
Assigning self into an existing Optional/union local also retains the frontend
reseat-source gate. Direct receiver payload construction is therefore excluded
at the internal boundary too; aliases and tuples do not grant wrapper coverage.

Tests will use compiler-owned source fixtures plus one condensed native case.
Pin receiver identity/access, scalar and tuple aliases through mutation,
shared/distinct parameters, nested projections, copies/reseats where already
covered, and exclusion facts. Malformed IR must reject missing/wrong receiver,
body-kind mismatch, readonly writes, duplicate self and receiver reseating.
THIR validation checks fact shape, source identity and nominal consistency.

The native receiver case pins tuple/Optional captures, a readonly receiver
observing another writer, and explicit copy independence. Direct self aliases,
explicit readonly aliases and local reseats are already deliberate subjects of
`tests/cases/records/receiver_alias`; nested field aliases are pinned by
`mir_nested_record_fields`. Those sections are not duplicated in the new native
case. The independent MIR unit checks still cover every admitted operation.
The tuple-parameter case uses distinct starting values for its shared/distinct
mixed-access calls, so the two outputs distinguish referent identity.

Pitfalls: mutation witnesses cover silent-copy-vs-alias and tuple-equals-scalar;
the finalized semantic access verdict covers const-source-const-loop-var.
Shared alias producers plus explicit excluded positions cover
same-construct-every-position. Generic-equals-monomorphic-twin is deferred to
M4. Calls/effectful expressions remain under prior coverage gates, preserving
conditional-operand-evaluates-in-place. No emission, allocations, views or
diagnostics change, so the remaining copy/view/allocation/diagnostic and valid
Python obligations are byte-identical existing output. Finish all specialist
reviews, readiness and a forced full suite before the third squash commit.

Completion: 817 focused MIR/THIR and tracking checks passed before the final
source-gate tests were added. The full forced run, including those tests, passed
with 8652 tests, 23 skips and 4156 C++ cases built and run. The new native case
matched CPython. All seven specialist lenses found no code defect; the docs
review refreshed the stale landing-status overview in `IR_DESIGN.md`. Independent
retrospective and meta-review accepted the approach and bounded exclusions.
The compiler implementation is unchanged by the native coverage cleanup.
Snapshots of cases predating the batch are unchanged.
M2.6, M2.7 and M2.8 each form one commit on the unmerged batch branch; broader
M2 coverage and the M3-M5 analysis/authority work remain as listed below.


## Approved batch: M2.9 constructors and M2.10 scalar globals

The user approved these two increments on 2026-09-18. Develop them in order,
review and verify each, and retain one squashed commit per increment on a
single branch. Do not merge to master or push. Both remain analysis-only:
source acceptance, diagnostics, C++ emission and provenance authority stay
unchanged. Resolve routine implementation details within this boundary;
stop for blockers or a material design change.

### M2.9: complete scalar constructor initialization and body

Completed: 841 focused checks passed, including the new native/CPython
witness. The full forced suite passed with 8679 tests, 23 skips and 4158 C++
cases built and run. All applicable specialist lenses and the independent
retrospective were clean. No existing snapshots changed.

Admit unique constructors of eligible flat plain records with bool/int32
fields and parameters. Every field must have exactly one explicit, pure
parameter-or-literal member initializer. Then admit the existing body subset,
including receiver aliases, singleton/mixed tuples, field writes and CFG.
For example, initializing value=3, capturing (self, self.value), writing 7
through the captured receiver and reading the captured scalar must retain
the original receiver identity and the old scalar value 3.

Invariant: complete initialization of caller-supplied receiver storage occurs
before any body operation can observe or alias self. Represent this as an
explicit MIR entry initialization with field values from scalar parameters
or typed constants, followed by the ordinary CFG. This is not record
replacement and does not require move assignment. The bounded initializers
have no effects or self reads, so field declaration order versus source order
is unobservable. No partially initialized receiver enters the CFG.

Reuse the eligibility and complete-initializer verification in
mir/definitions.py, factoring it from the additional empty-body restriction
on caller-side constructor summaries. Constructor-body coverage must NOT
relax MIRDefinitions: current call expansion reproduces only initializers,
so calls to constructors with effects remain uncovered. Reuse the method
receiver, place, alias, tuple and CFG machinery for the body.

| Axis | Covered | Deferred / existing gap |
|---|---|---|
| Position | Constructor entry and supported body tail; existing function/method coverage unchanged | Module bodies and closures: M2; resumables, comprehension, with, try/finally, error-return and match: M3; generic instantiations: M4 |
| Shape | bool/int32 receiver fields and parameters; existing supported local shapes and borrowed self tuples | Nested/reference/owning/Optional/union fields; non-scalar parameters; defaults, bases and special members: later M2; generics: M4 |
| Slot | Receiver storage, parameters, entry locals, scalar fields, void return | Globals: M2.10; containers/captures/reference returns: later M2/M4 |
| Operation | Complete pure entry initialization, then existing alias/mutation/CFG operations | Partial/demoted/default initialization, effectful initializers, calls and arbitrary construction effects: later M2/M3/M4 |

Tests consume the actual emitted THIRConstructor, check entry initialization
and execute both mutation branches over supplied empty receiver storage.
Malformed-IR tests cover absent/duplicate/mistyped fields, non-parameter
sources and wrong receiver identity/access. Rejection tests cover all
excluded initializer families and confirm body-bearing constructors remain
excluded from call summaries. Audit deliberate native constructor coverage;
add one semantic witness only where the alias/scalar-snapshot interaction
is not already pinned. Existing snapshots must remain identical.

Pitfalls: mutation distinguishes alias from copy; singleton/mixed tuple
captures preserve element semantics; the shared body lowering preserves
conditional evaluation. Other source positions keep their explicit gates.
No new view, allocation, numeric, warning or source-rejection behavior is
introduced. Generic twins and partial initialization remain excluded rather
than receiving fabricated facts. Independent design/parity surveys support
this boundary; initialization and call-summary separation are review gates.

### M2.10: qualified scalar global places

Completed: 864 focused checks passed, including 10 native executions. The
full forced suite passed with 8689 tests, 23 skips and 4158 C++ cases built
and run. All seven specialist lenses were clean after the scope-isolation
and explicit-load verification fixes; the independent meta-review and
retrospective accepted the bounded design. No existing snapshots changed.

Admit bool/int32 global reads and writes inside supported function, method
and constructor bodies. Carry semantic module/binding identity from existing
resolved bindings into THIR and then MIR; never recover identity from C++
spelling or conflate a global with a same-named local. Imported aliases and
module-qualified reads must identify the actual selected binding. Writes
follow the binding selected by the existing frontend, including Python's
distinction between module attributes and locally rebound imported names.

The producer survey confirmed that imported scalar names currently read the
defining module's live storage, unlike CPython's imported binding snapshot
(`BUGS.md#imported-scalar-binding-tracks-foreign-rebind`). Keep those names
and reexported module attributes uncovered. Admit same-module declarations
and direct module attributes (including module aliases) only. This narrows
internal coverage without changing source admission or C++ emission.

Use THIRGlobalBinding on scalar names, module-variable leaves and walrus
targets. Existing scope seeding decides whether a name is global; the module
registry identifies direct module attributes. Preserve qualified module/name,
type and lexical write permission. MIR global slots carry MIRGlobalId and
are separate from parameters/locals. Their storage is supplied by the caller,
shared across bodies and read afresh at each operation. Deduplicate by logical
identity, checking type consistency and each source write's permission.
Global slots are places only: scalar operands and terminators must consume
an explicit load into a local/temporary. Bare-name facts are not stamped in
nested functions, lambdas, comprehension scopes, module initialization or
resumable bodies; their inherited outer scope classifications cannot prove
the selected inner binding. Direct module attributes retain their resolved
module identity without consulting local names.

Model globals as shared external storage, not copied parameters. Require
explicit initial state when interpreting tests, and preserve mutations
across separately executed bodies. Module initialization, native globals,
reference/aggregate globals, closure captures and interprocedural effects
remain outside this increment. The detailed producer survey precedes code.
Test same-name globals in distinct modules, local shadowing, repeated reads,
conditional writes and existing-body-position symmetry. Preserve the existing
rejection of order-sensitive eager operands; global walrus writes do not
authorize a source-order assumption for an unsequenced emitted expression.
MIR tests prove identity; native tests only pin deliberate source-semantic
gaps in coverage.

| Axis | Covered | Deferred / existing gap |
|---|---|---|
| Position | Existing ordinary free/method bodies and complete scalar constructor tails | Module init and closures: later M2; generator/async/comprehension/with/try-finally/error-return/match: M3; generic instantiations: M4 |
| Shape | bool/int32 external storage, including bool walrus and ordinary scalar stores | Other scalars, tuple/Optional/union, reference types, str/bytes/views, Own/readonly wrappers, Ptr/Span/Box/Rc globals: later M2/M4 |
| Slot | Qualified global storage; reads into existing locals, scalar fields and return positions | Aggregate/field-held global aliases, containers and captures: later M2/M4 |
| Identity | Same-module declarations and direct module attributes, module aliases and distinct module names | From-import/reexports: tracked import-binding defect; native linkage: later M2 |

Native coverage is already deliberate in global_keyword_write,
global_unannotated_decl (including constructor writes), global_walrus_write,
global_walrus_shapes and global_shadow_func_local. Reuse those cases without
duplicating them or reading their sources from compiler tests. New MIR tests
use their own emitted-THIR fixtures to prove shared storage, lazy writes,
qualified identity, shadowing, readonly access and safe exclusions.

For each increment update this plan and the architecture/language status,
run focused checks, the applicable specialist reviews and readiness gate,
then one full forced suite. Fix and re-review findings before squashing;
do not refresh existing snapshots without the user's approval.

## Approved batch: M2.11-M2.12

The producer survey and bounded proposal are in
[MIR_CALL_CAPTURE_PLAN.md](MIR_CALL_CAPTURE_PLAN.md). Approved on 2026-09-18.
M2.11 preserves resolved ordinary free-function identities and signatures;
M2.12 preserves complete inventories for selected closure captures. Both
are descriptive THIR metadata steps: general calls and closure execution
remain MIRNotCovered until their control flow and effects can be modeled.
The proposal records the sibling survey, capture-storage distinction,
position/shape/slot exclusions, tests and review gates.

M2.11 implementation carries THIRResolvedCallee on eligible calls and
definitions, with structural module/name identity and ordered semantic
signature types. Registration retains the unique ordinary source declaration;
overloads and repeated definitions remain absent even when the registry has
collapsed them to one binding. Both producers require agreement with the
finalized declaration signature; stale cycle enum signatures remain absent
(`BUGS.md#cycle-enum-stale-reference-signature`). Declaration matching does not consult C++ names; the
existing MIRBodyId and call exclusion remain unchanged. The synthetic tuple
unit fixture that adds a parameter clears its original declaration fact,
since it no longer represents that emitted function's signature.
The final M2.11 forced gate passed 8,721 tests with 23 skips and 4,158
native executions after incorporating master `edfe216564`; existing snapshots
were unchanged. All seven review lenses and the retrospective closed clean.

M2.12 adds THIRClosureIdentity and THIRCaptureSlot plus a complete optional
inventory on THIRLambda/THIRNestedDef. Source bindings carry their category
and name; relations distinguish scalar binding references, scalar snapshots,
record referents and receiver aliases. Source facts and selected capture modes
determine access, including readonly snapshots and inferred readonly receivers.
The lexical prepass numbers unavailable sites too, with a separate occurrence
space for each nested body. Both levels of nested closure construction remain
unavailable before checking the empty-capture case. Only direct ordinary-body
positions and unambiguous parameters/entry scalar locals/receivers are covered;
special regions, wrappers, record local/copy/move and frame captures retain
the detailed exclusions in the call/capture plan. Existing MIR call and closure
rejections remain in force; no renderer consumes the new inventory.

The final M2.12 forced gate passed 8,747 tests with 23 skips and 4,158
native executions; the focused THIR/MIR and existing closure-case gate passed
907 tests. All seven review lenses and the retrospective closed clean.
Existing snapshots were unchanged. Both milestones are prepared as separate
commits on one branch; this batch does not merge or push them.

## Remaining M2 audit and M2.13

M2.11 and M2.12 are merged. The post-merge audit and M2.13 plan are in
[MIR_M2_REMAINING_PLAN.md](MIR_M2_REMAINING_PLAN.md). M2.2 already models separate
plain-record backing storage; M2.13 extends that model to constructor-backed
Optional record locals, consuming OWN/IN_PLACE facts and preserving nullable
holder copies and None clears. Inline-slot reuse without those facts stays
excluded. The implementation was approved on 2026-09-18.
The audit maps all six analysis requirements and recommends designing bounded
M3 liveness/holder propagation next, while retaining explicit M2 coverage gaps.
The implemented M3.1/M3.2 rules and tests are in
[MIR_M3_LIVENESS_PLAN.md](MIR_M3_LIVENESS_PLAN.md). They provide analysis
inventories, not lifetime safety verdicts or authority. The approved next
batch, M3.3/M3.4, is in [MIR_M3_REUSE_PLAN.md](MIR_M3_REUSE_PLAN.md): bounded
backing reuse and internal possible retention conflicts. M3.3 implements the
bounded backing reuse and positive write inventory; M3.4 adds internal
possible retained-object conflicts using that inventory.
General storage ends and cleanup remain later work.
This does not declare M2 complete or authorize callable admission.

The approved M3.5/M3.6 continuation is in
[MIR_M3_PAYLOAD_LIFETIME_PLAN.md](MIR_M3_PAYLOAD_LIFETIME_PLAN.md): implemented
inline scalar Optional/union payload-end events (M3.5), followed
by implemented internal retained-alias inspection (M3.6) that preserves
strict validation.
M3.3-M3.6 are merged. The approved next batch is in
[MIR_M3_REGIONS_PLAN.md](MIR_M3_REGIONS_PLAN.md): implemented M3.7 emitted
storage regions and normal end events, and implemented M3.8 internal retained-reference
inspection at those ends. It does not change source behavior or checker authority.

M3.7/M3.8 are now merged. The approved next batch is
[MIR_M3_DECLARATIONS_PLAN.md](MIR_M3_DECLARATIONS_PLAN.md): implemented M3.9
late direct declarations with conditional initialization, and implemented M3.10 positive
facts for bounded ordinary hoisted bindings and record backing.
Default-constructed wrapper hoists and general cleanup stay outside that batch.
Definite-assignment proof failures remain strict at the MIR validation boundary;
lowering reports them as uncovered rather than accepting an uninitialized read.
M3.11 removes impossible literal/not boolean edges during construction, including
the zero-trip path of `while True`, and prunes unreachable slot/region metadata
before strict validation ([constant CFG plan](MIR_M3_CONSTANT_CFG_PLAN.md)).
Mutable-variable propagation, comparison folding and arbitrary truthiness stay
deferred; every analysis consumes the same resulting structural CFG.

## Breadth-first order

This is the active sequence. The landed increments (M1, M2.x, M3.1-M3.27,
M4.1-M4.6) stay as records; the open items of the
[M3 completion checklist](MIR_M3_COMPLETION_PLAN.md) and of the M2/M4 matrix
below fold into the B-step that needs them.

**Baseline** (front end, the tree before B1, measured with
`scripts/mir_coverage/`; its name-hash sample of 1720 of 4151 compiling test
cases, the whole stdlib and 22 tpy-examples programs). MIR lowers 7.1% of test
bodies, 2.1% of stdlib bodies and 1.8% of example bodies. Of the 2525
loan-active bodies -- sema registered a loan, view, provenance,
parameter-return, loop or `with` hold, or emitted a lifetime diagnostic -- it
lowers 25 (1.0%). First blockers over those bodies, from the first
measurement's draw (the blocker mix, not the counts, is what matters): type vocabulary (anything
but bool/int32) 1077, resumable frames 452, module init 186, method receiver
not modelled 170, generic 147, unsupported statement (`print`, protocol `for`,
tuple unpack, `try`, nested `def`) 134, record layout 133. As of 0.6.1 the
type gate was `in (BOOL, INT32)` at about 20 sites in `tpyc/mir/lower.py`,
repeated in `validate.py`, `dependencies.py` and `storage_evidence.py`.

**Principle.** Model exactly what can create, hold or invalidate a loan;
everything else is opaque -- opaque contents, never opaque lifetime or
effects. Anything that can borrow fails closed. Admission keys on type and
form facts, never on lists of accepted kinds.

- **B1: loan classification.** Landed as the [B1 contract](#b1-contract): a
  tri-state loan classifier over representations (`typesys.loan_class`:
  holds a borrow, lendable), a parameter-passing fact carried on
  `THIRParam` -- a mirror of the parameter renderers, pinned against them
  by `tpyc/test_loan_class.py` (TODO follow-up (i) makes the renderers read
  it) -- and a primitive-operation contract
  on the TypeDef. It admits every loan-inert primitive and enum value as a
  scalar leaf in the layouts MIR already modeled, certified primitive
  operators and scalar `print`. Aggregates keep their own models, and
  records are recognized by their TypeDef, never as "not a leaf". Operations are admitted through proved
  loan-neutral contracts, never by result type: a scalar-returning call can
  mutate a global, a user dunder runs code.
- **B2, first half: owned leaves.** Landed as the [B2 contract](#b2-contract):
  BigInt, str, String and bytes are owned storage at rest and a readonly
  borrow of storage outside the body at a `const T&` / view parameter, in
  the record model with an opaque interior; operators, conversions and
  `getitem` on them are certified reads; stub calls get a contract from the
  stub's declared facts (`@pure`, `transient=True`); summaries cover raising
  and cyclic bodies and global reads.
- **B2, second half: views as places.** Landed as the Views, Field and
  Conflict naming rules of the [B2 contract](#b2-contract): `StrView` /
  `BytesView` holders borrow parameter, local, field, global and static
  roots under one family predicate (str and bytes by construction),
  owned-leaf record fields are places with constructor member-init, and the
  first str conflicts are the existing `replacement`, `scope_end` and
  `return_escape` kinds. `tests/cases/mir/views_as_places` pins
  `replacement`; `return_escape` has a unit pin in
  `tpyc/mir/test_views.py`; a view `scope_end` is pinned nowhere yet.
  Moved to B3: view FIELDS (a record retaining a loan), method calls and
  method summaries, container-element views and Span. The classification
  still UNKNOWN stays open: `TupleType.is_value_type()` is always true;
  `Own` is not position-transparent (a value type becomes a value, a
  reference type `T&&`); `Ptr` is value-typed; `readonly` limits access,
  not lifetime.
- **B3, first half: containers as places.** Landed as the
  [B3 contract](#b3-contract-first-half-containers-as-places): owned and
  borrowed container holders with `[structure]` / `[elements]` places,
  element reads, Span and dict views as views of `[elements]`, iterator
  loans over any container place, builtin container methods as reads,
  element writes or structure writes derived from the stub's receiver
  facts, container writes in free-function summaries, and the first
  container conflicts (`replacement`). `tests/cases/mir/containers` pins
  them, each beside a certified safe sibling.
- **B3, second half: retained loans.** Three items landed. Native types
  declare their storage members and element writes on the stub
  (`@native(..., elements=True)`, `@native(..., mutates="elements")`), read
  through `scalar_leaves.declared_members`, with no container kind table.
  A user `@native` container lowers from the same two facts where THIR
  lowers the shape (loops, a field read through a subscript, method calls
  with literal or leaf arguments). Calls of user record methods lower
  through per-method summaries whose parameter 0 is the receiver
  ([B3 contract, second half](#b3-contract-second-half-user-method-calls)),
  and so do property getters and setters and `@auto_readonly` defs, whose
  results may be rooted inside a parameter (a container field, a view of
  an owned-leaf field) through projected return origins
  ([accessor and twin callables](#accessor-and-twin-callables),
  [projected return origins](#projected-return-origins)). Records with
  plain struct bases are records of the same model: a field is keyed by
  its declaring record, a layout spans the inherited fields, a subclass
  binds at its base at a call argument, and a subclass constructor's
  definition chains to its base's ([inherited records](#inherited-records)).
  A record returned by value (`-> Own[R]`) is the body's own record
  storage moved out, and a resolved call handing one over is a record
  value wherever record storage takes a construct -- an owned local, a
  reseat, an element, a handed-over argument, a full-expression temporary
  ([owned record results](#owned-record-results)). An inline record field
  is a place of its record's storage: a record's definition composes its
  member records' definitions, a member is replaced whole in place, copied,
  borrowed, returned as a borrowed result and lent to a call, an `Own[R]`
  parameter is the body's own storage, and a parameter path runs through
  one or more members ([nested records](#nested-records)). A
  `StrView` / `BytesView` member stores a loan in the record object, set
  by its constructor, rebound by a view member write or by a callee
  through a published loan transfer (holder: a parameter or the owned
  result's member; source: a parameter path or static; a weak union at
  the caller, never a `MIRParameterWrite`), carried by a copy or move,
  keyed through inline member records, handed out by an owned result and
  in by an `Own[R]` parameter, and read whole; a loan of the body's
  storage stored where the caller reaches is a `store_escape` conflict
  ([view fields](#view-fields)); a record written through a place (an
  in-place reseat, a member replaced whole) fills the entries of every
  object the place reaches; every other way such a record moves or
  is written refuses. A wrapper's layout belongs to the representation at
  a place, and an Optional field of a loan-free record is a place of its
  holder with its payload stored inline
  ([Optional record fields](#optional-record-fields)). Remaining, in this
  order: Optional scalar and tuple fields; view fields slice 4
  (wrapper and container members; TODO.md lists the items);
  nested container elements; record elements whose record has record
  fields (`list[Line]`); record-element literal member-init. From here
  on each step builds the call-effect contracts it needs -- invalidation,
  exceptional behavior, retention beyond view members -- where
  `call_contract.py` today excludes globals, requires empty invalidation
  and publishes retention only as loan transfers into view members.
- **Cleanup:** exceptional exits and destruction.
- **B4: generator and async frames.** Frame placement and lifecycle facts
  published by THIR (close, cancellation, cleanup), not only suspend/resume
  edges. First slice landed (2026-10-29): a `def` generator's body (free
  function or method) lowers from the frame and leaves its emission
  published (`THIRResumableFrame` / `THIRResumableLeaves`, read, never
  built): the frame's blocks become MIR fragments, each `yield` a
  `MIRSuspend` (reads the value, may abandon the frame -- an exceptional
  exit -- writes nothing) followed by the edge to its resume block,
  falling off the end or a bare `return` a valueless return; frame locals
  are declared at entry by the hoist helper (MIR's type vocabulary:
  inert leaves); a by-value capture of a lent parameter is the frame's
  own copy. The verdict is `covered` at best (frames publish no storage
  facts) and the body publishes no summary. Remaining, each refused by
  name: decomposed `for` loops (a loop whose body suspends; waits for the
  one loop-binding record), `async def` bodies, generator expressions
  (their source's display name), try/except/finally/with regions,
  suspending `match`, nested defs, module globals read or written by a
  frame, reference yields, owned-leaf and aggregate frame locals, an
  argument the frame holds across a suspension (a handle built over a
  temporary), a by-value capture of an explicit `StrView` parameter, and
  the caller's half (a generator handle's summary).
- **B5: remaining general call summaries** (pending callee facts, globals,
  callbacks) proceed alongside B1-B4 as each step needs them.
- **B6: advisory checker and authority transition.** Whether authority moves
  per diagnostic family or in one switch is decided here; this supersedes
  M5's single switch. The first advisory family landed early (2026-10-10,
  [argument-alias obligations](#argument-alias-obligations-the-first-mir-diagnostic)):
  a MIR warning beside sema's checks, which stay authoritative; it decides
  nothing about authority. The second landed 2026-10-27: **evidence vs
  warning.** Every conflict stays evidence (the verdict's conflict kinds,
  `--dump-mir`, the storage certificate); only a *definite* hazard warns.
  Each conflict-finding analysis (retention, scope lifetime, payload
  lifetime, return and store escapes) emits one `witness.MIRHazard` per
  event beside its unchanged conflicts, and `witness.decide` proves it or
  leaves it evidence: strict reach over the full written and retained sets
  (`reach.reaches_definitely`), a non-call read every path reaches before
  the holder is rebound, a fresh store every path carries to a normal
  return unrewritten (one warning per kill, affected root and holder,
  `mir_diagnostics.hazard_warnings`). Not-definite classes -- a read on
  some paths, element identity, caller-dependent aliasing, raise-path
  escapes, joined writes into a parameter's object -- are precision items
  (TODO.md), each a later promotion. A hazard sema also warns is reported
  by both (decided 2026-10-28): no code matches one analysis's warnings
  against the other's, and B6 removes the duplicate per family when it
  hands that family to MIR; this split still decides nothing
  about authority. **Gate for later units:** a unit that changes which bodies
  lower, or MIR verdicts or conflicts, lists and classifies every corpus MIR
  warning before landing, with zero false ones. How: regenerate the corpus
  snapshots (`uv run pytest --update-snapshots`, through the hosts) and list
  the MIR warning lines `git diff -- '*/diag.txt'` adds or removes (every
  case's `diag.txt` records its MIR warnings); classify each by its program
  (a valid input that reaches the hazard, else false). The 2026-10-27 unit
  also ran a census driver that lives only in /tmp (`/tmp/agents/mrc/impl/warn.py`
  and `run_warn.sh`): it compiles each corpus program that has a MIR conflict
  (the survey's list), calls `Compiler.report_mir_warnings(..., strict=True)`
  over its user modules and prints every warning; examples and the stdlib
  render case are covered the same way.

**Constraint on B1-B5**, so both B6 outcomes stay possible: evidence --
structured conflict kinds, explicit unknowns, locations, complete obligation
inventories, certificates tied to the exact THIR body/instantiation and
summary inputs -- is produced independently of which checker owns a
diagnostic. Sema is never silenced because MIR lowered a body or found no
conflict.

**Progress metric.** Over a fixed stratified denominator, four counts --
lowered, all required analyses complete, conflict found, certified for its
obligations -- plus blocker transitions. Generic coverage counts
instantiation obligations. A `deferred: MIR` reproducer counts only when MIR
gives the expected unsafe verdict and a safe sibling is certified;
sema-rejected reproducers are evaluated on a separate path. The counts come
from `scripts/mir_coverage/` (its README defines them), measured over its
name-hash sample of `tests/cases` (a rule, not a file, so membership is stable
as the corpus grows).

### B1 contract

- **Invariant.** A value MIR admits as a scalar leaf holds no borrowed leaf,
  has no storage a compiler-introduced borrow can point into, and is passed
  by value, in the representation it has at that point
  (`typesys.loan_class`; one predicate, `thir/scalar_leaves.storage_leaf`,
  asked at the parameter's passing convention, the return representation,
  or storage). Leaves: every fixed-width int, `float`, `float32`, `bool`,
  `char` (`TypeDef.loan_inert`), and enum values. User enums and records
  are TypeDefs of one compilation, so every MIR lowering path
  (`--dump-mir`, the coverage tool, the tests) runs under
  `activate_compiler`. A parameter's representation is the passing THIR
  publishes on `THIRParam` and MIR carries on the parameter slot; an
  unpublished passing is refused. Aggregates are never leaves: tuple,
  Optional and union keep their own models (a tuple or scalar-union
  parameter is still a `const &` to the whole, a payload extraction still a
  payload alias). Records are recognized by their TypeDef
  (`scalar_leaves.record_type`, inside `value_class`), never as "not a
  leaf"; a type with no TypeDef still reads as a record (failing closed
  there is filed in TODO.md, MIR entry). The validator checks every slot's
  class, and every wrapper and container member's, against `value_class`
  at its representation, and the dependency pass's "no leaves" answer keys
  on that verified class.
- **Literals.** A number literal takes its context's leaf type (the other
  comparison or arithmetic operand, the slot, the parameter); alone it is the
  C++ literal it spells (`int` / `double`). Its value must fit that type
  (`scalar_leaves.leaf_constant`, from `TypeDef.zero_value` and the int
  traits). Conditions stay `bool`, range induction stays `int32`, constant
  CFG folding stays `bool`.
- **Operation rule.** An operator is admitted only when THIR publishes
  `certified_op` on its node: the resolved dunder's owner carries the
  primitive-operation contract (`TypeDef.primitive_ops`), no promotion
  (`ResolvedBinop.promotion`), operand cast or template override converts
  an operand, and every operand and the result is an inert leaf; a
  number-literal operand converts into the typed operand's primitive. Comparisons lower to `MIRCompare`, every
  other certified operator to `MIROp(op, operands, may_raise)`, a read of its
  operands with no dependency transfer. No method-name list decides either.
  The validator re-checks the contract on every `MIROp`, `MIRCompare` and
  `MIRPrint` operand (`scalar_leaves.primitive_leaf`).
- **Print rule.** `print` lowers to `MIRPrint`, a read of its arguments, when
  every argument is a leaf whose TypeDef carries the primitive contract
  (printed by the runtime, no user method can run) and whose `PrintForm` is
  scalar (RAW, INT8, BOOL, FLOAT, FLOAT32), with a literal or default
  `sep`/`end` and no `file=` sink. Enum values stay out of print (a user enum
  may define `__str__`).
- **Exception rule** (superseded by the B2 contract's exit rule). The
  primitive contract admits raising (checked overflow, a zero divisor), so
  every `MIROp` is `may_raise`. Under B1 a body containing one had an
  OPAQUE summary; B2 summarizes it with `normal_return_only=False`. A body
  that prints is OPAQUE ("summary output effect"): output is no
  parameter-rooted effect.
- **Deferred (not covered).** User ValueType records as opaque kinds;
  certifying operations on whole aggregates; enum operations and enum print
  (no enum TypeDef carries the primitive contract); formatter resolution for
  aggregates in print. (Summaries of raising calls, builtin calls such as
  `len()`, and BigInt, str, bytes and String were deferred here and are
  covered by the B2 contract below.)
- **Measured** (`scripts/mir_coverage/`, same name-hash sample as the
  baseline): lowered test bodies 7.1% -> 9.4% (997 of 10554), example bodies
  1.8% -> 9.0%, stdlib bodies 2.1% -> 2.9%. Loan-active USER bodies (tests
  and examples, 2215; the 310 stdlib ones lower on neither tree) 25 -> 47
  (1.1% -> 2.1%); certified test bodies 26 -> 35. One conflict, and a true
  one: `pointers/escape_hoist_conditional` binds a loop-local record to an
  outer name in one branch, which sema already warns about; MIR's retention
  pass reports the replaced storage. The top loan-active blockers are now
  return and local types outside the leaf set (str views first), resumable
  frames and module init.

### B2 contract

- **Invariant.** A value MIR admits as an OWNED LEAF holds no borrow and
  owns opaque storage a compiler-introduced borrow can point into: at
  STORAGE it is an owned place (local, temporary, return, global); at
  CONST_REF or VIEW passing it is a readonly borrow of storage outside the
  body; every admitted operation reads that storage through a borrow that
  lives through the operation and yields a fresh owned value or an inert
  leaf. A view of one is a borrowed holder (Views rule below). The
  TypeDef declares it:
  `owned_leaf` on `int` (BigInt), `str`, `String` and `bytes` (their C++
  types own a buffer, copy and move with no observable effect beyond
  allocation, destroy with no hook), `copy_may_raise` (a standard
  container's `bad_alloc`, which a bare `except:` catches; BigInt's
  allocation failure is a panic) and `compares_fixed_ints` on BigInt (the
  runtime's comparison operators take every fixed-width int).
  `typesys.loan_class` answers `LoanClass(holds=NO, lendable=YES)` at
  STORAGE (`is_owned_leaf`, `thir/scalar_leaves.owned_leaf`); views stay
  borrows, `bytearray` (a reference type) and everything else stay UNKNOWN.
  The four carry `primitive_ops`, whose contract now reads "may allocate".
- **Model.** The record model with no interior: `MIRRecordLayout.opaque`,
  answered by `MIRDefinitions` from the TypeDef as a tagged builtin
  certificate beside the verified constructor definitions, never for any
  other missing type. A `const T&` / view parameter is a readonly
  pointer holder with an external referent; a local, temporary,
  by-value parameter (entry-initialized) or result is owned storage with
  a duration; every write to owned storage carries a `MIRStorageWrite`
  (storage events key on the DESTINATION, not the rvalue class); no
  `MIRField` projection exists under an opaque layout. A global of
  owned-leaf type is a readonly handle to the immortal external identity
  `global:<module>.<name>` (seeded in the dependency pass; never a private
  root, since a parameter may borrow the same global); its writes refuse. A
  borrowed str / bytes literal has an immortal static origin; a
  storage-form bytes literal is an owned constant.
- **Operation rule.** Operands of owned-leaf kind are borrows (`MIRBorrow`
  holders) live through the operation; results are owned temporaries;
  `MIRCopy` (with `may_raise` from the TypeDef) only where the C++ copies:
  an initializer from a borrow, `return a`, a `param_copy`, a by-value
  argument, a BORROW -> STORAGE form conversion. Certified on the THIR
  node: an operator whose resolved dunder's owner carries `primitive_ops`
  over inert or owned leaves (`certified_op`); a derived comparison of one
  inert type, or of BigInt with a fixed-width int (`compares_fixed_ints`);
  a non-literal coercion under a declared `Coercion` row (`bigint_narrow`
  included) whose target is an inert leaf or an owned leaf the render
  materializes as a new value (`THIRCoerce.wrap`, e.g. `::tpy::BigInt({0})`,
  `std::string(::tpy::char_to_str({0}))`); a passthrough into an owned leaf
  is the Views rule's `owned_passthrough` (lent at a borrowing sink,
  copied at an owning one), and every other passthrough refuses
  (`THIRCoerce.conversion_refusal`, "conversion aliases its source"); a
  promotion that is an argument-less method of one operand's
  type yielding the other's (`THIRBinOp.promoted_operand`, lowered as a
  `coerce` op feeding the operator); `s[i]` on a str / bytes place with a
  fixed-width int (or int literal) index and an inert result
  (`THIRSubscript.certified_op`). `THIRStrAppend` is an
  IN_PLACE write; `print` admits str and bytes (RAW / BYTES forms).
- **Exit rule** (supersedes B1's Exception rule). `MIRFunction.
  exceptional_exits` is derived at lowering and re-checked: any `MIROp` or
  `MIRCopy` that may raise, any owned constant whose materialization may
  raise, any `MIRCall.may_raise`, any `print` (formatting allocates). Such
  a body's summary is KNOWN with `normal_return_only=False`; its effects
  are promised over every exit
  (possible writes cover a prefix ending in a throw). Within MIR's coverage
  an exceptional exit is an exit without a result and without a handler
  (no `try` lowers). Storage evidence stays normal-path evidence (B1's
  meaning, written down); the coverage tool reports bodies with
  exceptional exits as a fifth count. Whether a certificate must cover
  exits is decided per diagnostic family at B6.
- **Call rule.** `MIRCallSummary.parameters` are `MIRParameterBinding(declared,
  value, passing, private_owned)` (`call_contract.derived_binding`; the
  class, storage and loan access are its properties); an unpublished
  passing fails closed, and the caller cross-checks
  `THIRCallableSignature.passings` against them. Both passings come from one helper,
  `predicates.param_passing`, over the FunctionInfo that holds the body's
  const verdicts: the definition's `THIRParam.passing` and the signature
  every call publishes for it (a stub's from its declaration,
  `param_contract.stub_param_consts`); the validator (`call_contract.summary_problem`,
  and the caller's check at lowering) enforces that the two agree. A
  callable whose FunctionInfo carries no const verdict passes not-const on
  both sides, and an accessor or twin named like an in-place dunder takes
  the forced-const rule a method does. An owned-leaf
  argument at CONST_REF / VIEW passing is lent for the call (a borrow of a
  name, a global handle, a static literal or an admitted temporary); at
  VALUE / OWN it is copied. A user callee's owned-leaf result is fresh
  owned storage. Summaries cover cyclic bodies (`MIRRangeAdvance`, private
  owned writes; recursion stays opaque) and carry `global_reads`, unioned
  through callees; any global write keeps a summary opaque. A global
  argument is read into a temporary (scalar) or lent through its handle
  (owned leaf), sound because a KNOWN callee writes no global and an owned
  leaf is reachable only through its own storage.
- **Stub rule.** A call to a `@native` / `@cpp_template` free function or a
  builtin type's `__init__` carries `THIRCall.stub_callee` (identity =
  qualified name plus parameter types, signature with passings and the
  actual return representation, per-parameter readonly, and the declared
  contract). MIR derives its summary from the declaration -- reads every
  parameter, writes / invalidates / retains nothing, may raise -- when the
  contract is `@pure` (no non-local mutation, nothing retained after
  return or raise) or `transient=True` (reads or writes only its arguments,
  retains nothing, reaches no other TPy storage, runs no user code; a
  `@pure` stub counts as transient only under the per-parameter gates
  below, since `@pure` was never audited for "runs no user code"), every
  parameter is an inert leaf by value or an
  owned leaf at a readonly CONST_REF / VIEW passing, and the result is
  void, an inert leaf or an owned leaf returned as STORAGE. A protocol
  parameter is admitted only under `@pure` and only for an argument whose
  TypeDef is `loan_inert` or `owned_leaf` (no user dunder can run). A stub
  result of owned-leaf type MAY BORROW every lent argument (`min(a, b)` is
  `@pure` and binds `std::min`, a reference to an argument); it is fresh
  only when no argument is lent. An unmarked stub stays opaque. Audited for
  the acceptance program: `len` (`@pure`), `time.time` (`transient=True`),
  `math.log` and `int(x: float)` (`@pure`).
- **Views rule.** A view is a readonly pointer holder typed by its
  family's view type (`StrView`, `BytesView`) whose referents are a
  conservative set of POSSIBLE origins, never "exactly": the owned-leaf
  storage its source reads (a local, an owned-leaf field place, an external
  parameter, a global handle, a static literal). The family is
  `typesys.view_family_of` (it answers for the view member too), and
  compatibility is ONE predicate, `thir/scalar_leaves.view_compatible
  (holder, source)`: the holder is the family's view type and the source is
  the family's owned type, an owned sibling that is itself an owned leaf
  (`String`; `bytearray` stays out) or the family's view. The validator
  asks it at every `MIRBorrow`, `MIRAlias`, dereference and `MIRCopy`, so
  str and bytes follow one rule by construction; a borrowing view with no
  owned-leaf family (Span, dict views) refuses as "view of container
  storage". Model: every view-producing operation TRANSFERS referents and
  never copies. A name, a `*_to_strview` coerce or a field read is a
  `MIRBorrow` of the place; a view-to-view binding is an `MIRAlias`; an
  unstepped slice is a borrow of the WHOLE receiver (the interior is
  opaque, so a sub-range is the buffer's loan; Python slices clamp, so it
  does not raise), with its bounds evaluated as reads BEFORE the borrow
  (fixed-width int or int literal, the getitem rule); a str / bytes literal
  in a view holder is a `MIRConstant` with an immortal static origin (any
  readonly borrowed holder that is not a parameter). Every owned sink
  copies through the view: `THIRFormConvert` (materialize) at a `-> str`
  return, an owned local or a field store is `MIRCopy((*v), may_raise)`.
  Certified operations read a view operand as the leaf it views (`len(v)`,
  `v == "x"`, `v[i]`, `print(v)`). A view PARAMETER (`v: StrView`) is a
  loan passed by VALUE: a readonly borrowed slot seeded external like a
  `str` view parameter; reseating one refuses ("reassigned view
  parameter"), while a reassigned `str` parameter owns a local
  (`THIRParamCopy`) and a later view borrows that local. A view RESULT of a
  user callee is a borrowed result (`call_contract.view_result`, one fact
  for body, summary and caller); the caller's holder takes the callee's
  `summary.returns` referents. A result rooted in a view-family
  parameter summarizes as that parameter, one viewing an owned-leaf field
  of a record parameter as that field's path
  ([projected return origins](#projected-return-origins)), and a result whose origins include a global, a static literal
  or the body's own storage leaves the body lowered and its summary opaque
  ("view result origin outside the parameters"; an empty origin set is
  never read as fresh). A stub view result borrows every lent argument and
  refuses with none ("stub view result has no lent origin"). `String`
  bound as `str` is authorized by `THIRCoerce.owned_passthrough`, keyed on
  the sink: a `MIRBorrow` of the `String` place at a borrowing sink (a
  `str` view parameter, a view local), a `MIRCopy` at an owning one. Rule:
  a view source MIR cannot name refuses ("unsupported view source": an
  if-expression or select producing a view, a method stub's view result
  (`v: StrView = s.strip()`), a slice-object index; a user
  method's view result is a user callee's, above, and a view member read
  `t.s` takes the loan the record stores, [view fields](#view-fields)),
  as does a stepped slice ("stepped slice"), which allocates.
- **Field rule.** An owned-leaf record field is a place: `MIRField` admits
  owned-leaf field types under the owner's layout (`MIRDefinitions`
  layouts admit `storage_leaf or owned_leaf` fields; projecting INSIDE the
  leaf stays banned). Model: a read is a borrow of `%r.Owner::name` at an
  operand position and a `MIRCopy` at an owned sink; `r.f = v` and the
  field `+=` are `IN_PLACE` replacement events on the field place
  (`storage.storage_destination`), as for owned-leaf locals. A
  constructor's member-init entry is the field write `self.f = v` is, an
  INITIALIZE through `self` ([Constructors are ordinary bodies](#constructors-are-ordinary-bodies-unit-7)): a scalar parameter's `MIRRead`, a
  `MIRCopy` of a parameter's storage (`may_raise` from the TypeDef), a
  constant (materialized: it raises as owned storage taking it does,
  `validate.value_may_raise`), a `MIRMove` out of a by-value parameter; a
  copy that may raise is an exceptional exit of the constructor, and a
  caller's call raises as the constructor's summary says.
  A record declaration's initializer is a full expression that owns its
  argument temporaries. A callee's field writes are replacement events at
  the call: `MIRStorageEvents.call_writes` lists every place a call may
  write (`call_effects.call_write_places`, from the callee's summary, a free
  function's or a user method's), so
  `v = r.name; rename(r, s); len(v)` is a conflict when sema has not already
  owned `v`. Retention asks `reach.reaches` (with its oracle,
  `reach.storage_exclusions`): replacing an owned-leaf field reaches holders at or under
  that field under ONE root (sibling fields are disjoint), while distinct
  external roots may alias, so a write through one parameter reaches every
  non-record holder of another (`write_then_reuse` in the case: `s` may
  view the very `r.name` the write replaces). A `StrView` / `BytesView`
  member is a stored loan of the record object, never an owned-leaf place
  ([view fields](#view-fields)); a container or wrapper member holding a
  view refuses ("record member holds a borrow").
- **Conflict naming.** No view-specific conflict kind exists; the
  tentative names map onto the structural kinds. `view_return_escape` is
  `return_escape`: a borrowed (record or view) result reaching a local or
  a temporary, discovered unconditionally by
  `storage_evidence.analyze_return_escapes` for every lowered body (split
  from certificate attribution, which filters by required roots) and
  reported in `MIRAnalyses.conflicts`, a required analyses field.
  `view_source_mutation` is `replacement`: an in-place write or rebind of
  the source (a local, a field place, a call write) while a view of it is
  live. `temporary_borrow` is `scope_end` whose ended storage is a
  TEMPORARY. Last-use precision follows from liveness: a view dead at the
  write is no conflict even where sema warns.
- **Deferred (not covered), B3 or later.** Wrapper or container storage
  of a view-holding record (view fields slice 4,
  [view fields](#view-fields)); method calls on owned leaves and views
  (`s.strip()`, `String` in-place methods beyond `+=`, `THIRMethodCall`
  stub contracts; a user method call such as `r.rename(s)` publishes its
  write through the method's summary, B3 second half); views and owned
  leaves as container elements, Span and iterator
  loans (B3); tuple / Optional / union members; `bytearray`; owned-leaf
  global writes; f-strings; stepped slices ("stepped slice"); reassigned
  explicit view parameters ("reassigned view parameter"); view-producing
  if-expressions and selects ("unsupported view source"); structured
  return origins, so a view result rooted in a global or a static literal
  can be summarized (B5); the private-write summary rule is implemented but
  a body with local record storage still stays OPAQUE under "summary
  storage or value shape"; constructor-argument temporaries outside a
  declaration (reassignment, optional backing, argument temporaries);
  `readonly[str]` fields ("owned-leaf record field"); BORROW-form field
  reads; a lent holder overlapping the call's own field write
  (`rename(r, r.name)`) is not checked, the holder being dead after the
  call; hoisted owned-leaf locals (a view or str bound in an `if` arm)
  refuse as "missing or inconsistent hoisted binding facts"; the
  cannot-raise stub fact so `may_raise` can be False; the conversion
  certificate keys freshness on the render's `wrap` where the declared
  `Coercion.builds_fresh_value` should decide it (the rows that would need
  the flag are read by sema too, so that is its own zero-churn unit); the
  fixed-int constructors (`int32(a)`) publish no `THIRStubCallee` until a
  stub-callee shape for the generic `__init__[T]` is decided. Test
  hazard: the `_dynamic_attached_qnames` holdout latches
  `TypeDef.is_borrowing_view`, so a per-test compiler reset drops it
  mid-test (`tpyc/mir/test_views.py` re-latches in its fixture).
- **Measured** (`scripts/mir_coverage/`, name-hash sample, branch base vs
  the reviewed B2 tree): lowered test bodies 9.5% -> 11.5% (1001 -> 1218
  of 10577; 11.5% -> 14.0% excluding module init), of which the body-kind
  fix alone accounts for 1001 -> 1024 and the owned leaves for the rest;
  example bodies 9.0% -> 12.0% (15 -> 20 of 166), stdlib bodies 2.9% ->
  3.8% (39 -> 52). Loan-active USER bodies 47 -> 74 of 2219 (2.1% ->
  3.3%); normal-path-certified test bodies 35 -> 45; 383 lowered test
  bodies may exit by exception (print counts), which no certificate
  covers. Conflicts 1 -> 2, both true and both record shapes -- no
  owned-leaf conflict yet: the first half proves coverage, the first str
  conflicts come with views:
  `pointers/escape_hoist_conditional` (B1) and
  `records/warn_alias_rebind_clobber` (a rebind sema warns about, reached
  now that its str print lowers). The top first blockers after B2: module
  init, resumable bodies, return / parameter / local types outside the
  leaf set (records, containers, views), then "stub declares no contract"
  (216 bodies: unmarked stubs, the cheapest next lever), "owned-leaf record
  field" (174, second half), f-strings (105) and "view local" (74).
- **Measured** (views half; `scripts/mir_coverage/`, name-hash sample,
  master 9cbcc321e0 vs the integrated branch): lowered test bodies 12.0% ->
  16.1% (1286 -> 1733 of 10772; 14.5% -> 19.5% excluding module init),
  example bodies 12.0% -> 19.9% (20 -> 33 of 166), stdlib bodies 3.8% ->
  5.0% (52 -> 68). Loan-active USER bodies 78 -> 138 of 2279 (3.5% ->
  6.1%); certified 46 -> 49; 889 bodies changed first blocker, the largest
  moves "unsupported record fields" at constructors (315 lowered, 64 now
  "record holds a borrow"), "owned-leaf record field" (69 lowered, 75 now
  "missing receiver fact"), "view local" (22) and "view return" (12); the
  blocking type `str` 423 -> 335 bodies, `StrView` 94 -> 27. Conflicts
  3 -> 7: those the `views_as_places` case pins -- three at the time of the
  run, four since review added `forwarded_then_reuse` (a source written while
  a view is live, its bytes twin, a `str` parameter read after a field write
  it may view) and one stdlib body, `ssl.SSLContext.load_cert_chain`
  (`self._certfile = certfile; self._keyfile = keyfile`: the second view
  parameter may view the field the first write replaces -- sound for a
  caller passing `ctx._certfile`, and the shape every two-field setter has,
  so a precision item for the advisory checker: distinct external roots are
  assumed to alias). No MIR crash; no body that lowered before stopped
  lowering; no snapshot changed.

### B3 contract (first half: containers as places)

- **Invariant.** Every container place -- a slot, a field or an element of
  a native container type, one whose stub declares
  `@native(..., elements=True)` (in the stdlib `list`, `set`, `dict`,
  `Array`) -- is opaque
  storage with two summary sub-places, `[structure]` (its shape) and
  `[elements]` (every element at once). Element reads, views, slices and
  iterators borrow `[elements]` (an iterator also retains `[structure]`); a
  container operation writes `[structure]` when the callee's declared
  receiver effect is a structural mutation, `[elements]` when it replaces
  elements in place, and nothing when it only reads. Element identity is
  never tracked: a write of one element is a write of any.
- **Container rule.** One member tuple per type (`MIRDefinitions.container`):
  its members `(element, value)`, built from the members the type's
  stub declares (`scalar_leaves.declared_members`, reading
  `TypeDef.native_members`, latched when the stub is registered), by
  position among its
  type arguments: `element` is the type parameter the readonly `__iter__`
  yields (the list / set / Array element, the dict KEY) and `value` the
  other parameter `__getitem__` returns (the dict value) or None; a
  subscript is keyed when its index parameter is the element (dict), else
  positional, and a subscript on a type whose keyedness disagrees with
  having a value member refuses ("unsupported keyed container").
  `dict[K, K]` has two members (`value.container_members`, shared by the
  definitions and the validator). Each member is an `MIRShape` held by
  value -- the container stores it -- whose class is an inert leaf, an
  owned leaf or a plain record, its access the container's
  (`value.with_access`). Admission keys on `typesys.loan_class` (a native
  container's `holds` is its element's, it is lendable) and on the
  declared type: since slice 3 MIR derives a container binding from its
  type and access and reads none of the `native_container` facts THIR
  still publishes on `THIRParam`, `THIRVarDecl` and `THIRForEach.iteration`
  (owned-leaf keys, values and elements, `Span[T]` parameters, owned
  container locals and record elements); no renderer reads them. Admitted elements: inert leaves, owned
  leaves, and plain records whose fields are leaves or owned leaves (no
  container field inside an element, so no path is deeper than
  `container[elements].F::x`; a top-level record may hold container fields).
  A type with a value member (a dict, an items view) holds leaves in BOTH
  members: a record-valued dict refuses at the layout ("unsupported native
  container element"). No rule names hashing: sema refuses a record without
  `__hash__` / `__eq__` as a set element or dict key, and a record
  declaring them is no plain record element (`plain_record_element`), so
  no admitted element runs user code inside a container operation. An
  element whose `loan_class.holds` is YES refuses
  ("container holds a borrow"); a container element of a container refuses
  ("unsupported native container element"). Holders: an owned container
  local or `Own[list[T]]` parameter is a holder over a backing slot of its
  own, as an owned record is (one aggregate, 2026-10-22); a field and a
  result hold container storage by value; a `list[T]` parameter at CONST_REF /
  MUT_REF holds a POINTER at the container (a borrowed container, reached
  through `[deref]` as a borrowed record is) with an external referent,
  readonly from the passing; a container view (a Span, a dict view) holds
  the region it views (`REGION`, container views only); an iterator is its
  container's cursor. Places: `MIRContainerElements` (the
  "any element" place) and `MIRContainerStructure` compose after a slot
  root, a `MIRDeref` or a `MIRField` of container type
  (`(*%self).Bag::items[elements]`, `(*%xs)[elements].Point::x` for a borrowed `xs`). A dict's
  `[elements]` is ONE region typed by what the access yields: the value at
  a subscript, the key at iteration (no key / value projection). Rvalues
  reuse the existing nodes: a scalar element is `MIRRead(xs[elements])`
  after its index read, a record element at BORROW form `MIRBorrow`, a str
  element at a view holder `MIRBorrow` and at an owned sink `MIRCopy`; an
  unstepped slice is `MIRBorrow(xs[elements])` into a Span holder; `for`
  runs over any container place (owned local, field, parameter); a literal
  is `MIRConstruct` with a container-layout target, one operand per element
  and always `may_raise` (allocation); `len(xs)` lends the container
  readonly. Writes: `xs[i] = v` is `MIRAssign(xs[elements], v,
  MIRStorageWrite(IN_PLACE))`, a WEAK update (never a strong kill in the
  dependency transfer); rebinding an owned container (`xs = [..]`) builds
  the new list into storage of its own and repoints the holder, as a
  record rebind does; a whole container field write (`self.items = data`)
  is a member write, as a record member's is.
- **Stub method rule.** A native container method (builtin or user
  `@native`) carries
  `THIRMethodCall.stub_callee` (a subscript write, `THIRSetItem.stub_callee`,
  from the resolved `__setitem__`): parameter 0 is the INSTANTIATED receiver
  (`signature.param_types[0]`, `CONST_REF` when the stub is readonly, else
  `MUT_REF`), the explicit parameters follow substituted, and the identity
  is `owner.name` plus those types, so `list[int32].pop()`,
  `list[int32].pop(i)` and `list[Point].pop()` are three identities.
  `THIRStubCallee.mutates_elements` carries the stub's
  `@native(..., mutates="elements")`. The
  receiver effect is derived from the declaration, first match wins:
  `@pure` -> reads only (this covers the `@auto_readonly` mutable clones of
  `values()` / `items()`); readonly receiver -> reads only;
  `mutates_elements` -> writes `(0, (elements,))`; otherwise (a mutating
  method that declares nothing) writes `(0, (structure,))`. A `@pure` stub may read a READONLY receiver through
  a mutable binding: sema resolves the mutable `@auto_readonly` clone of
  `values()` / `items()` on a const dict too, and a pure read cannot write
  it. A mutating method reached through a VIEW (`Span.__setitem__`,
  `Span.sort`) writes `[elements]`, never `[structure]`: a view passed by
  value cannot change its source's shape. The result follows its published
  `return_representation`: REFERENCE or VIEW borrows parameter 0 (and every
  other lent argument), STORAGE or VALUE is fresh (`pop -> Own[T]`, `copy`,
  `index`). Parameters follow the B2 stub rule (inert leaf by value, owned
  leaf lent readonly, view by value; an element argument at OWN passing is
  copied or moved into the container). A protocol, callable or
  `Iterable[...]` parameter keeps "stub declares no contract", so `extend`
  refuses, and so does `xs += ys` (the in-place `__iadd__` call over the same
  `list_extend` binding); slice assignment is a statement, not a method call,
  and refuses as "unsupported statement". A
  method whose type
  parameter carries a protocol bound (`sort`, `remove`, `index`, `count`)
  is admitted only when the bound argument is an inert or owned leaf. A
  record element must be PLAIN -- no custom copy, move or destructor and no
  `__eq__` / `__ne__` / `__lt__` / `__le__` / `__gt__` / `__ge__` /
  `__hash__` -- so no container operation dispatches user code on it.
  Every stub may raise.
- **Reach rule** (`reach.reaches`, with retention's `reach.storage_exclusions`). A written `[structure]` reaches
  every holder STRICTLY inside the container (iterators, element borrows,
  Spans, element views) and not the whole-container holder itself: the
  object survives an `append` in C++ as in Python, so `xs.append(1);
  len(xs)` is no conflict. For the same reason a call result borrowed from a
  container argument resolves to the argument's `[elements]`, not to the
  whole argument (the summary names the parameter; the caller projects). A
  written `[elements]` reaches holders at or under `[elements]`, a live
  iterator included (conservative: `xs[i] = v` and `d[k] = v` under a live
  loop conflict). Sibling fields and sibling containers under one root stay
  disjoint; distinct external roots may alias (the B2 rule), so a function
  iterating one container parameter while growing another conflicts
  (`copy_into` in the case). `static` referents are never affected.
- **View transparency.** A container view -- a `borrowing_view=True` type
  whose stub declares members: `Span`, `varargs`, the dict views, a user
  view -- is a holder of its source's region typed by its view type (the
  `StrView` template). The holder IS its
  source's elements region: a container projection applied to a view
  holder resolves to the holder's referents (never `xs[elements][elements]`),
  an iterator over a view depends on the view's referents only, and a write
  through a `Span[T]` (`s[0] = v`) is an elements write of the source.
  Mutability comes from the view's own type arguments
  (`Span[readonly[T]]`). A view's layout is its OWN declared members
  (`declared_members` of the view type, readonly from its own type
  arguments), so a cursor over a view walks the view's layout on the one
  container path: `keys()` declares the key, `values()` the value.
  `scalar_leaves.view_compatible(holder, source)` compares the two types'
  type arguments (readonly stripped), which name the elements both sides
  store and keep `dict_values[str, int32]` off a `dict[int32, str]`; a
  mutable view views no readonly elements, and a view of a view has the
  same type. `dict_items`, whose `__iter__` yields
  `tuple[K, V]`, declares both members and binds no cursor
  (`scalar_leaves.binds_cursor`): `len(d.items())` is covered, iterating
  it refuses. `Span.__setitem__` and `Array.__setitem__` do NOT declare
  `mutates="elements"`, although a span or a fixed-size array never
  moves its elements: sema reads that fact as "invalidates nothing" and
  would stop warning on the explicit spelling `a.__setitem__(i, v)` under a
  live loop or element borrow (`BUGS.md#setitem-write-under-live-element-borrow`),
  and this unit changes no diagnostic. A `__setitem__` whose stub declares
  no `mutates` lowers as the stub call it is -- a structure write of
  the receiver (`arr_write` in the case: `writes={param0[structure]}`), the
  conservative verdict that reaches every holder inside the container; a
  write through a Span stays an element write by the view rule. The
  subscript spelling `a[i] = v` never warned for any container; MIR reports
  it as `replacement` on list, dict, Array and Span alike.
- **Moves.** A `MIRMove` out of owned storage (an `Own[list[T]]` argument or
  return, an owned leaf) is a replacement event on the source place: a live
  holder of its elements conflicts, a dead one does not.
- **Returns and summaries.** `-> list[T]` returns a C++ reference, so it is
  a BORROWED result summarized by `returns` like a borrowed record result;
  `-> Own[list[T]]` is an owned result moved out (the template an
  `Own[R]` record result follows, [owned record results](#owned-record-results)).
  A Span or element result
  rooted in `param[elements]` publishes the whole parameter (a caller's
  structure write still reaches it by prefix overlap); a container field
  of a record parameter publishes its field path
  ([projected return origins](#projected-return-origins)). `MIRParameterWrite.path` widens to field
  identities and container projections (`(structure,)`, `(F::items,
  structure)`, `(elements,)`), so a free function's container writes reach
  its callers as call-write events (`grow_if` / `forwarded` in the case);
  `invalidates` stays required-empty, and a callee keeps a loan only as a
  transfer into a view member ([view fields](#view-fields)). A user method's summary
  follows the same rules with the receiver as parameter 0
  ([second half](#b3-contract-second-half-user-method-calls)).
- **Conflict naming.** No container-specific kind: sema's
  `iter_invalidation`, `borrow_invalidation` and `borrowed_container_arg`
  families are all `replacement`. `p = ps[0]; ps[0] = Point(..)` is a
  `replacement` where sema says ok
  (`BUGS.md#setitem-write-under-live-element-borrow`: C++ `p` reads the new
  point where CPython keeps the old). `tests/cases/mir/containers` pins
  each conflict beside a safe sibling whose holder is dead at the write.
- **Deferred (not covered), second half or later.** A view-holding record
  as a container element ("container holds a borrow"; the rest of what a
  view-holding record leaves out is under [view fields](#view-fields)); a
  method's borrowed result rooted inside its receiver (the second half's
  own deferred list); a callee retaining a loan anywhere but a view
  member (the loan-transfer contract covers only those,
  [view fields](#view-fields)); nested
  container elements beyond one hop ("unsupported native container
  element"), which also keeps
  `BUGS.md#elem-index-certainty-ignores-rebinds` and the `rows[0]` face of
  `BUGS.md#mutating-callee-non-name-arg-unchecked` out of reach; `extend`
  (an `Iterable` parameter), `xs += ys` and slice assignment ("unsupported
  statement"); iterating or
  unpacking `items()` (tuple elements); `in` (`THIRMembership`); protocol
  `for` over user iterators, comprehensions and generator expressions
  (frames, B4); call-duration loans (`xs.extend(xs)`, `rename(r, r.name)`);
  a result rooted in `param[elements]` of a container parameter summarizes
  as the whole parameter (an empty-path return origin); sibling-element precision (element identity is
  not tracked, so `xs[i] = v` under a live iterator conflicts); stepped
  slices; tuple, Optional and union elements; `bytearray`; a dict view
  bound to a local is a lowering reject today
  (`BUGS.md#dict-view-local-binding-rejected`); a constructor member-init
  from a literal of RECORD elements (`self.ps = [Point(1), Point(2)]`)
  lowers since unit 7, as an ordinary statement; a container returned by reference from a RECORD
  parameter (`items_of(b) -> list[int32]` returning `b.items`) now
  summarizes with the field path (`returns={param0.items}`) and its caller
  binds the field place ([projected return origins](#projected-return-origins));
  a record element
  write through a Span is a THIR reject (`setitem.family`), so that conflict
  is pinned over hand-built MIR only; a parameter whose container field is
  grown through a local alias is `BUGS.md#param-field-alias-growth-not-mutation`
  (ill-formed C++ before any run).
- **Refusal reasons beside the deferred list.** "reference parameter
  reseat": rebinding an `Own[...]` parameter (`xs = [1]`), record or
  container alike; "constructor argument type": an aggregate handed over
  (an `Own[...]` parameter, an element-taking one) as a name rather than a
  construct, literal, copy, move or call (`take(ys)`); "unsupported record
  argument": a loan-holding record handed to a parameter private to the
  callee as anything but a constructor call or a call handing over an
  owned result ([owned record results](#owned-record-results)); "unsupported
  record initializer": a repeat (`[0] * 3`), which no MIR value states
  (TODO.md "MIR has no repeat value op"); an `if`-arm container binding
  hoisted before the `if` ("missing or inconsistent hoisted binding
  facts": THIR publishes no hoist fact for a container, TODO.md "Container
  and record binding facts are derivable" item (i)). One is a guard no
  probed shape reaches: "element index needs a fixed-width int" (a
  `BigInt` index lowers). Since slice 3 a rebound element holder (`p = ps[0]; ...; p =
  ps[1]`) lowers: a rebind takes the element source as a declaration does.
- **Measured** (`scripts/mir_coverage/`, name-hash sample; the branch base
  23f4775733 vs the reviewed branch at 41d4bd197d, which also carries
  master's one-view rule for str and bytes; each figure over its own tree's
  denominator): lowered test bodies 1756 of 10870 (16.2%) -> 2013 of 10929
  (18.4%), 19.5% -> 22.2% excluding module init; example bodies 34 -> 34 of
  166 (the sampled examples hold no container shape this half admits),
  stdlib 68 -> 72 of 1373. No body that lowered on the base refuses on the
  branch. Loan-active USER bodies 142 of 2297 (6.2%) -> 234 of 2334
  (10.0%); by loan kind `borrow:iter` 14 of 890 -> 63 of 912 lowered,
  `borrow:element` 15 of 295 -> 28 of 301, `provenance:param_derived` 64 ->
  95, `view_var:str` 41 -> 67 (the one-view rule turned two inferred views
  into owned copies), `param_returned` 30 -> 50. Certified 49 -> 50 (the
  certificate still covers THIR materialized backings only), exc-exit
  bodies 717 -> 949 (element reads that check an index or key are
  exceptional exits). Conflicts 8 -> 21 bodies, every one `replacement`:
  the eight of the base unchanged, the twelve conflict sections of
  `mir/containers`, and one new outside the case,
  `list/setitem_str_concat_aug::bump` (its shape pinned in the case as
  `str_elem_aug`): the `str` parameter `k` is live across `ys[0] += "a"`,
  and a `str` parameter may view the very element the write replaces
  (`bump(ys, t, ys[0])`) -- the conservative external-alias class of
  `SSLContext.load_cert_chain`, the filed alias-basis precision item. 1245
  bodies changed first blocker; the largest moves are local / parameter
  container types into "unsupported native container element" (nested,
  177), "stub declares no contract" (70: `extend`, callbacks), "constructor
  initializer needs parameter or literal" (125: 65 constructor bodies with
  record-element literals in member-init, the rest callers constructing
  such records) and `storage: no_proof_required` (207 bodies now lowered to
  the end, 143 of them from container-typed locals, parameters, returns and
  record fields). Top loan-active blockers after this half: resumable
  bodies 555 (B4), return types 281 (tuples and records by value), module
  init 186, generic bodies 137, `THIRForIterProto` 79.
- **Measured, declared storage members** (same tool and sample; branch
  base 961e1e99c0 vs the integrated branch, 10957 test bodies on both):
  lowered 2013 -> 2019, conflicts 20 -> 20, certified 50 -> 50; no body
  that lowered on the base refuses. The six new bodies are `*args`
  functions that mutate their elements or declare them `readonly[...]` (a
  `varargs` parameter is a view holder like a `Span` parameter). 26 bodies
  changed first blocker: besides those six, ten `*args` bodies that only
  read an unannotated element move from "view of container storage" to
  "container name type mismatch" (sema types the parameter and the name
  differently), six record-valued
  dict types from "missing native container fact" to "unsupported native
  container element" (the refusal moved from the THIR fact to the MIR
  layout), and four varargs bodies reach a later blocker.

### B3 contract (second half: user method calls)

- **Invariant.** A user record's ordinary instance method and every call
  that statically resolves to it publish ONE callee identity and ONE
  signature whose parameter 0 is the receiver.
  `THIRFunctionIdentity(module, name, owner)` names the owning record's
  qualified name in `owner` (None for a free function), so two records'
  same-named methods are two callees. The signature is
  `(record, *declared)` at passings `(receiver, *declared)`, the receiver
  `CONST_REF` under the method's readonly verdict (declared or inferred),
  else `MUT_REF` -- one helper, `thir.nodes.effective_params` /
  `receiver_param`, feeds the published signature, the THIR validator, the
  MIR receiver's binding and `summarize_function`. Parameter types
  compare through `thir.nodes.declared_param_type` (reference and access
  stripped): an explicit `@readonly` / `@pure` body reads its parameters
  as readonly while the signature keeps the declared types, and the
  access is the passing's fact, so such a callable taking a record
  summarizes KNOWN (`Counter.ahead_of`, `count_of`). The definition
  carries it on `THIRFunction.resolved_callee`, a call on
  `THIRMethodCall.resolved_callee`, never beside a `stub_callee`;
  `thir/validate.py` checks each against its receiver and parameters or
  arguments, and `call_contract.summary_problem` refuses an owner whose
  parameter 0 is no borrowed record of that owner ("method summary without
  its receiver"). Analysis only: no render reads the fact.
- **Body rule** (`thir/lower/callables.method_receiver`). The receiver fact
  -- `self` a pointer parameter 0 at the method's readonly verdict -- is
  published for an instance method of a plain record (`borrowed_record`:
  not native, not a value type, no type parameters, every struct-base
  ancestor plain as well -- `scalar_leaves.modeled_hierarchy`, the
  [inherited records](#inherited-records) eligibility; not an
  enum companion), dunder bodies included (`__eq__`, `__hash__`,
  `__bool__`, `__getitem__`, `__setitem__` are pinned), except the
  lifecycle hooks `__del__` / `__copy__` / `__move__`, which the generated
  special members run around, so their `self` is no initialized object
  borrowed for a call (`TpyFunction.is_lifecycle_hook`), a constructor
  body (`TpyFunction.is_initializer`: its `self` is storage under
  construction; a class has one TPy-bodied `__init__`, sema refusing a
  constructor set, and it lowers as the constructor, not as a method),
  static and class methods, consuming, generic, async and generator
  methods and auto-own clones. Property getters and setters and both
  clones of an `@auto_readonly` def are method bodies too, under the
  [accessor and twin](#accessor-and-twin-callables) rules. Such a body
  lowers as a METHOD body, and
  `summarize_function` summarizes it with the receiver bound as
  its parameter 0, a `MIRParameterBinding` whose value is a pointer at the
  owner record (`call_contract.bound_params`); every other summary rule is
  the free function's.
- **Call rule** (`method_callee`, `with_method_callee`). A call carries the
  callee when the target is proven: a body-eligible method with no
  template (a dunder's injected operator included: an explicit
  `p.__eq__(q)` renders `(p) == (q)`, while a template-less `p.__bool__()`
  is an ordinary member call and a call target, `call_bool`) or native
  symbol, and no deref, move or
  unwrap at the call, exactly one defining body for (owner, name) in the
  call's role (`Compiler.callable_body`; an overload or `@dispatch` group
  has none), a closed signature, a receiver whose static type (readonly and
  reference stripped) is the declaring record or has it among its
  struct-base ancestors ([inherited records](#inherited-records)), and a
  receiver hierarchy that inherits no `@dynamic` protocol (its methods are
  virtual: a base-typed receiver may run a subclass override). MIR lowers a resolved method call
  at every position a resolved free call lowers at (statement, scalar,
  owned-leaf, borrowed record, view and container result, argument),
  binding `(receiver, *args)` to the summary's parameters
  (`lower._call_arguments`). The receiver follows the record-argument
  rule: a borrowed record name or `self`, at no more access than its
  holder has ("call record argument mismatch"). The call workspace follows
  `THIRMethodCall.resolved_callee`, so methods are scheduled leaves first
  beside free functions (a method -> method -> free-function chain
  forwards the leaf's write through each receiver) and recursion stays
  OPAQUE; a method body's id is `Owner.name@line:col` in the workspace and
  the verdict walk alike (`collect.body_declaration`).
- **What a caller consumes.** The method's `writes` with their paths
  (`(F::n,)`, `(F::items, structure)`, `(F::items, elements)` of the
  receiver or of a record parameter), `returns` as
  [return origins](#projected-return-origins) (a method returning `self`
  publishes `param0`, one returning a view of a `str` parameter that
  parameter, one returning a container field of the receiver
  `param0.items`), nothing invalidated or retained. A
  borrowed record result is readonly exactly when the emitted C++ result
  is const (`callables._borrowed_result`: a `readonly[...]` declared
  return, `typesys.declared_result_readonly`, or the receiver's access for
  a result that follows it), at the definition and at every
  call: a method that does not write `self` and returns a record
  parameter hands its caller a mutable borrow (`call_other`), an explicit
  `@readonly` method returning `self` a readonly one (`call_me_ro`). An
  explicit `@readonly` callable returning its record parameter declares
  `-> readonly[P]` (sema refuses a mutable return of it) and summarizes
  KNOWN, a free function and a method alike. A
  scalar field written through a call is no replacement event (no
  `mir_write` line fact), as a direct scalar field write is none.
- **Conflicts.** The existing `replacement` kind:
  a container field grown through a method under a live iterator over it,
  in a free function and in a method, and a field view
  (`v: StrView = c.name`) live across a method that replaces the field.
  Sema warns on none of them
  (`BUGS.md#field-loan-whole-record-callee-unchecked`,
  `BUGS.md#explicit-view-local-source-mutation-unguarded`).
  `tests/cases/mir/method_calls` pins each beside a safe sibling: a call
  under the iterator that writes a scalar field only, a view dead before
  the replacing call.
- **Aliased arguments.** A body assumes its borrowed parameters may alias,
  so a method iterating `other.items` while growing `self.items` carries
  the `replacement` conflict in its own body (`Counter.merge_from`) and its
  caller passing two records (`a.merge_from(b)`) is covered: call-duration
  aliasing is reported at the callee, never re-checked at the caller.
  Precision stays the alias-basis item.
- **Kept refusals**, each pinned in the case with its reason. A virtual
  owner's method call, "unsupported expression type" (its body lowers, with
  no summary), and its property read, "unsupported expression"
  (`mir/accessor_calls` `call_virtual_getter`); an explicit call of a dunder with an
  injected operator template (`p.__eq__(q)`), "unsupported expression"; a
  constructor tail calling a method on `self` (`Tally.__init__`) lowers
  since unit 7 (the constructor is an ordinary body); a
  call-result, constructor-temporary or
  element receiver (`pick(g).read()`, `Gauge(k).read()`,
  `gs[0].bump()`), "call needs borrowed record name", the limit record
  arguments of free functions have (an inline record field receiver,
  `o.inner.bump()`, lowers through a holder of the member:
  [nested records](#nested-records)) -- a getter through a call result
  (`c.via().count`) is the same refusal (`mir/accessor_calls`
  `twin_via`); a generic record's
  method, where the caller's parameter of that record refuses first
  ("unsupported parameter type"); a generic or a consuming method,
  "unsupported expression"; a staticmethod, "call needs resolved ordinary
  callee"; a recursive method, summary OPAQUE "recursive or
  recursion-dependent call", caller "call needs finalized known summary".
  Probed, not pinned: a classmethod refuses as the staticmethod; an
  operator-dispatched dunder (`p == q`) "uncertified binary operation".
  Unit-pinned without a callee: an `@error_return` or async method, a
  `@dispatch` or `@overload` group, a `Ptr[T]` receiver, accessors and
  twins of a generic or virtual owner (a derived owner's accessor resolves:
  `test_a_derived_owner_resolves_its_own_accessor`), a method and a property
  sharing one name (`callable_body` finds no body for either role:
  `test_a_method_and_a_property_sharing_a_name_publish_no_callee`; the
  language defect behind the shape is
  `BUGS.md#method-property-name-clash-miscompile`).
- **Limits.** `--dump-mir` and the snippet harness analyse the modules
  under the entry point's directory only (the harness's `mir.txt` adds the
  library modules a case snapshots, in a workspace of their own), so a method of a record defined
  in a library module has no summary there ("call needs finalized known
  summary"). The lifecycle hooks (`LIFECYCLE_HOOKS`) and the constructor
  (`INIT_METHOD`) are the names the language defines them by, decided once
  at the parse node and read by the record's accessors and the body
  predicate alike. Receivers beyond a name, `self` or an inline record
  field (a call result, a temporary, an element) refuse, so a getter
  reached through `c.via().count` refuses where `c.count` and
  `self.c.count` lower.
- **Measured, user method summaries** (same tool and sample; branch base
  d1fb396703 vs the finished branch; 11046 test bodies on the base, 11140
  on the branch, the 94 more being `mir/method_calls`): lowered 2033 ->
  2258, conflicts 22 -> 28, certified 50 -> 58. Over the 11046 bodies both
  trees hold, 161 more lower -- 115 dunder bodies off "missing receiver
  fact", 23 dunder bodies (`__iter__`, `__enter__`, `__iadd__` returning
  `self`) off "unsupported return type", their record result now a
  published borrowed result, 17 free functions and 6 methods off a method
  call -- and no body that lowered on the base refuses; certified 50 -> 54;
  loan-active user bodies lowered 243 -> 274. The six new conflicts are
  every one `replacement`: the five conflict sections of the case, and one
  outside it, `dynamic_attrs/dyn_name_setattr::Headers.__setattr__`, a
  dunder body that newly lowers and stores two `str` parameters into two
  fields (the second parameter is live across the first write, and a `str`
  parameter may view the field that write replaces: the conservative
  external-alias class of `SSLContext.load_cert_chain`). 268 bodies changed
  first blocker: besides the 138 dunder bodies, the largest move is
  callers from a method-call refusal to "call needs finalized known
  summary" -- 44 bodies sit there now, because the called method's summary
  is OPAQUE. Over the 32 programs holding them the opaque method summaries
  give as reasons: a nested call with no known summary (11), "stub protocol
  argument is not a builtin leaf" (8), a record whose constructor refuses
  (13 over five reasons, the constructor-tail call among them),
  "unsupported parameter type" (5), "summary storage or value shape" (5),
  "unsupported statement" (5), "unsupported return origin type or access"
  (4), "unsupported owned-leaf expression" (4). Seventy-six bodies leave
  "unsupported return type": a record returned by reference is now a
  published borrowed result, and most refuse next at the field expression
  they return ("unsupported borrowed expression form") or at the storage
  certificate ("demanded operation is not a supported record borrow").

#### Accessor and twin callables

`tests/cases/mir/accessor_calls` pins each rule below at its call sites;
the unit tests are `tpyc/thir/test_method_callees.py` (THIR facts and
validator) and `tpyc/mir/test_return_origins.py` (MIR).

- **Invariant.** Every callable body a record exposes -- a method, a
  property getter, a property setter, the clone pair of an
  `@auto_readonly` def -- is ONE callable with ONE identity and ONE
  signature, published on its definition and on every call that
  statically resolves to it.
- **Identity.** `THIRFunctionIdentity(module, name, owner, accessor)`,
  `accessor` None for a method and `"fget"` / `"fset"` for a property's
  getter / setter (Python's own names for the two callables). A setter's
  `name` is the property name: Python has no differently named setter
  (`@size.setter def set_size` is "property has no setter" in CPython and
  a "conflicts with method" error in TPy). The THIR dump spells them
  `Counter.count.fget` / `Counter.count.fset`.
- **Twin contract.** An `@auto_readonly` def and every property getter (a
  getter is an auto-readonly pair; a value-returning getter keeps only its
  const clone) is access-polymorphic: its signature publishes
  `passings[0] = CONST_REF` -- what the body is certified against, since a
  twin body writing `self` does not compile -- and
  `THIRCallableSignature.result_follows_receiver`, on the definition and on
  every call (dump: `, follows receiver`). A borrowed result of a
  follows-receiver call is readonly exactly when the receiver at the call
  is readonly or the return type is `readonly[...]`: the emitted C++ picks
  the const or the mutable overload by the receiver's constness. The
  signature carries ONE result value, at the definition and at every
  call: the one the const clone binds (readonly; the THIR validator holds
  every follows-receiver signature to it). The access of one call is
  derived where it is read (`call_contract.call_result`): MIR binds the
  result at the receiver ARGUMENT's binding (`MIRCallSummary.result_at`),
  so an inferred-const receiver binds a readonly result although its
  static type is mutable. The call arm records the access its receiver is
  emitted at (`THIRMethodCall.receiver_access`, set exactly with
  `resolved_callee`; `self` at its own body's verdict, so the mutable
  clone's `self` is mutable); the for-loop arm reads it for a
  follows-receiver container result, MIR refuses a call whose receiver
  binding disagrees with it ("call receiver access disagrees with its
  binding"), and the validator never lets it be mutable where the
  receiver's static type is readonly. `twin_mutable` (`d = c.me(); d.count = 7` writes
  through a mutable result) and `twin_readonly` are certified; a setter
  is no twin (its receiver passes at the setter's own readonly verdict).
- **Both clones publish.** Both clone bodies of a pair carry the same
  `THIRFunction.resolved_callee`, so each lowers for its own verdict with
  its own receiver fact; the mutable clone also carries
  `THIRFunction.access_twin`. The THIR validator admits at most one
  definition and one twin per identity, the twin's callee equal to the
  definition's, follows-receiver, on a mutable receiver -- checked over a
  module's bodies together by `validate_definitions` in the codegen pass,
  once every function body is lowered. The call
  workspace takes the const clone as the definition
  (`MIRCallWorkspace.definitions`), keeps the twin in
  `MIRCallWorkspace.twins`, and summarizes the twin too, its receiver
  normalized to `CONST_REF`: unequal summaries, or a twin that does not
  lower, make the callable OPAQUE ("twin bodies differ";
  `test_a_differing_twin_makes_the_summary_opaque`). Inside a clone a call
  may resolve by the receiver's access, so the equality is checked,
  never assumed. The two bodies' ids are `Counter.elems@48:4` and
  `Counter.elems@48:4#2` in the workspace and the verdict walk alike; the
  twin check's lowering is cached under the twin's own id and served to
  the twin's verdict, and neither body ever reads the other's MIR
  (`tpyc/mir/test_collect.py`).
- **Body lookup.** `Compiler.callable_body(owner, name, accessor)` sits
  beside `Compiler.single_method_body(owner, name)`, both over
  `Compiler.method_bodies`. `callable_body` filters the entries by role
  (method, getter, setter: `typesys.accessor_role`), collapses a clone
  pair -- sema links the mutable clone to the const clone where it makes
  them (`TpyFunction.clone_of`), and the pair counts only when the two
  bind the same parameters and return -- to its const clone, and returns
  None for anything else: an overload or `@dispatch` group, a pair whose
  clones differ (`pick(self, other: Counter)` takes `other` at two
  accesses), a method and a property sharing the name.
  `callables.method_callee` is the one builder for every role: the
  defining body from `callable_body`, the declaration check `_declares`
  by role, the receiver at that body's access, and
  `callables.method_definition` publishes the same value from the body
  (the twin by `clone_of`). `single_method_body` keeps its stricter
  rule -- one body or None -- because sema's with-exit check
  (`sema/loop_frames.py`) reads the body it returns and falls back to the
  call's declared write facts when there are several; collapsing a pair
  there would change which diagnostics fire.
- **Setter passings.** Sema pops accessor FunctionInfos from the method
  table before const inference, so they carry no `const_borrow_params`;
  the callee publishes each declared parameter by the one helper the
  body's own `THIRParam.passing` reads (`predicates.param_passing`):
  `param_passing(False)` of its expanded type, and the validator checks
  the definition's passings against the body's `THIRParam.passing`. An
  `int32` value passes VALUE, `str` VIEW, `Own[str]` VALUE, a record,
  `readonly[R]` or a container OWN, each expanded to `Own[...]`
  (`test_a_setter_passes_its_value_as_its_body_declares`).
  MIR verdicts: the `int32` and `str` setters summarize KNOWN (writes
  `param0.n`, `param0.name`); an `Own[str]` setter too (probe); a record
  setter summarizes KNOWN too, its `Own[R]` parameter the body's own
  storage moved into the inline field (`Holder.part`,
  [nested records](#nested-records)); a container setter
  refuses at the field it replaces, "container field replacement is
  unsupported" (probe).
- **What a caller consumes.** A getter read `c.count` is a
  `THIRMethodCall` to the fget callee and a setter write `c.count = v` a
  void call statement to the fset callee; both take the existing
  user-call arms, at every position a resolved method call lowers at. A
  container getter's result is a borrow of the field place
  (`xs = c.elems` has referent `c.items`, as the direct alias `xs =
  c.items` has), also as a write receiver (`c.elems.append(3)` writes
  `c.items[structure]`); a view getter bound to a `StrView` local views
  the field (`t: StrView = c.tag` borrows `c.name`), while an unannotated
  `t = c.tag` owns a copy (the view rule) and no later write reaches it
  (`copy_then_rename`).
- **Conflicts.** The existing `replacement` kind, each beside a covered
  sibling: a container getter's or a twin's result iterated while the
  field grows through a method (`grow_under_iter`, `twin_items_grow`), a
  getter's field view live across a method replacing the field
  (`view_then_rename`). Sema warns on the two iterations and is silent on
  the view (`BUGS.md#field-loan-whole-record-callee-unchecked`); it warns
  on the safe `alias_survives_growth` and `view_then_sibling`, which MIR
  covers with no conflict
  (`BUGS.md#getter-borrow-whole-record-false-positive`).
- **Kept refusals.** A getter on a record inheriting a `@dynamic`
  protocol, "unsupported expression" (`call_virtual_getter`); a getter
  through a call result, "call needs borrowed record name"
  (`twin_via`; a getter through an inline record field receiver lowers,
  `getter_through_field`); a field read through a getter CALL result
  (`h.part.x` in `record_accessors`), "reference needs local name": THIR
  publishes no field identity on a field read whose receiver is a call,
  while `q = h.part; q.x` lowers (the getter returning the inline field,
  `return self.inner`, summarizes KNOWN with origin `param0.inner`:
  [nested records](#nested-records)); accessors of a generic or virtual
  owner and a method + property name clash (unit-pinned).

#### Projected return origins

- **Invariant.** A summary's return origin is a PATH into a parameter,
  `MIRReturnOrigin(parameter, path)` with `MIRCallSummary.returns:
  frozenset[MIRReturnOrigin]`, and the caller binds the result holder to
  exactly the referent the direct borrow of that place produces. The path
  is a `MIRParameterPath`, the alphabet `MIRParameterWrite.path` uses, under
  one grammar (`call_contract.resolve_path`): fields through inline record
  members, then at most one container projection
  ([nested records](#nested-records) has the hop rule). The whole
  parameter is the empty path. The MIR dump spells
  `returns={param0.items}` / `returns={param0}`.
- **Body rule** (`summaries.summarize_function`). The field path of a
  returned referent is kept, never widened to the whole parameter. Into a
  borrowed record parameter it admits the record itself (a record result
  of its own type), a container field (a container result:
  `return self.items` -> `param0.items`), an owned-leaf field a `str` /
  `bytes` view result views (`return self.name` at `-> StrView` ->
  `param0.name`) and an inline record member a record result of its type
  is (`return self.a` -> `param0.a`); each
  field must be a member of the storage its hop reads -- one of its
  record's declared fields -- by the walk of the path. Anything else refuses
  "summary unsupported return origin", followed by the path check's own
  reason (a field its record does not declare: "field missing from declared
  fields"): a Span over a field
  (`test_unspellable_origins_refuse`). A result rooted in a parameter that is
  no record (a container, a Span, a leaf) keeps the whole-parameter
  origin.
- **Summary check** (`return_origin_problem`, inside `summary_problem`).
  Origins are validated as writes are: the grammar, a field of the
  parameter's record, an endpoint typed for the result (a container field
  or a record member equal to a container or record result, an owned leaf
  compatible with a view result), and never more access than the source
  lends, checked with the result bound at the definition's receiver.
- **Caller** (`dependencies.resolve_call_returns`). Each origin's PLACE
  is built through `call_place` -- shared with call writes -- and its
  referents resolved in sequence, like a direct borrow of it; a non-empty
  path is never re-projected, a whole-parameter origin keeps its
  elements projection for a result that is no container, and a Span
  argument is projected once (`test_a_span_argument_is_projected_once`).
  `call_return_problem` type-checks each origin's place against the
  holder the result fills: "container result of a non-container
  argument", "... of a non-container place", "container result rooted in
  an elements region", "view result of a non-leaf place", "call result of
  a mismatched place" (`test_call_return_problem_checks_the_endpoint_type`).
  A user-call container result is a container place wherever a
  container place is read (a receiver, a subscript, a `for`, an alias
  declaration); a call that WRITES refuses there, "order-sensitive eager
  operands", since the emitted C++ may evaluate an index or slice bound
  before the container (`test_a_writing_call_is_no_container_place`).
- **Stubs.** Stub summaries keep whole-argument origins and their
  result-dependent elements projection; the derivation equality between
  a stub's declaration and its summary is unchanged
  (`test_stub_summaries_keep_whole_argument_origins`).
- **Measured, accessor summaries and return origins** (same tool and
  sample; branch base 23cefb5ab1 vs the integrated tree 545f1ac360; 11142
  test bodies on the base, 11185 on the branch, the 43 more being
  `mir/accessor_calls`): lowered 2258 -> 2333, conflicts 28 -> 32,
  certified 58 -> 60. Over the 11142 bodies both trees hold, 42 more lower
  -- 34 accessor and twin bodies off "missing receiver fact" (28 in the
  property position: 1 -> 37 of 71 property bodies lower), 3 callers off
  "call needs finalized known summary" at a method call, 3 off a property
  read or write ("unsupported expression", "unsupported expression type"),
  1 off "unsupported reference fact" (a container call result bound to a
  local), 1 free caller off a free call whose callee waited on a method
  summary -- and no body that lowered on the base refuses.
  The four new conflicts are every one `replacement`: the three conflict
  sections of the new case and `method_calls::tag_across_rename`, whose
  view-of-a-field result the previous unit pinned opaque. The two new
  certified bodies are the twin callers of the new case.
  The summaries pinned KNOWN on the base (`containers` `items_of` and
  `tail`, the `method_calls` methods) stay KNOWN; the sample records no
  per-body summary state, so no wider KNOWN -> OPAQUE count is claimed.

#### Inherited records

`tests/cases/mir/inherited_records` (exec and cpy) and
`tests/cases/mir/inherited_shadow` (a shadowed field is two storages where
CPython has one attribute, a warned divergence, so no cpy phase) pin each
rule below; the unit tests are `tpyc/thir/test_inherited_records.py` (THIR
facts) and `tpyc/mir/test_inherited_records.py` (MIR).

- **Invariant.** Field identity is (declaring owner, name); a record's
  layout is every field its C++ struct contains -- its own and its
  struct-base ancestors' -- and storage of type `S` binds at type `T`
  exactly when `T` is `S` or a struct-base ancestor of `S`. Plain
  inheritance is static and non-virtual in the emitted C++
  (`struct Sub : Base`, `use_base(Base& b)` called as `use_base(s)` with
  `Sub& s`), and a diamond is a sema error, so every field of a hierarchy
  is one storage at one place.
- **Eligibility** (`scalar_leaves.modeled_hierarchy`). A record is modeled
  when it and every struct-base ancestor (`TypeRegistry.is_struct_base`)
  is a plain user record: not native, not a value type, not a typed dict,
  not generic nor reached through a generic base, and no exception
  (`is_return_exception`, `implements_throwable`: a return exception's
  `Exception` base builds no struct, so the ancestor walk alone would not
  refuse it). `borrowed_record`, `modeled_record` and
  `plain_record_element` ask it. `plain_record_element` also checks the
  custom copy / move / destructor flags and the comparison and hash
  dunders on every record of the hierarchy -- an inherited `__eq__` would
  run user code inside a modeled container operation -- and requires
  every field, inherited ones included, to be a leaf.
- **Field identity.** `THIRFieldIdentity.owner` is the DECLARING record,
  spelled by `scalar_leaves.record_owner` (the one spelling of a record
  type in an identity, so a base's own bodies and a subclass's publish
  equal facts for one member), and found by
  `TypeRegistry.declaring_record(record, name, start=None)`: bare `self.n`
  walks from the receiver's record, nearest first. The explicit ancestor
  form `B.n` walks from the named ancestor: sema resolves it through `B`'s
  own ancestry and the C++ `this->B::n` is `A::n` when `A` declares `n`,
  so the owner is found by the walk starting at `B`, never `B` itself. Its
  receiver, `THIRSelf(result_type=B)`, is admitted when `B` is among the
  body receiver's layout ancestors. The walk is THIR's own registry helper
  beside `get_all_fields`; sema's `lookup_record_field` stays separate (it
  carries per-edge generic substitution MIR never sees).
- **Shadowing** (legal; sema warns). A subclass redeclaring an inherited
  field holds two storages: `Shadow::n` and `Base::n` are two fields of
  `Shadow`'s layout. `self.n` in a `Shadow` body names `Shadow::n`,
  `Base.n` names `Base::n`, and a call of the inherited `Base.bump` on a
  `Shadow` writes `Base::n` -- exactly what `Base::bump` writes in C++.
- **Layout** (`storage.record_layout`). The fields in C++ construction
  order (`TypeRegistry.construction_order_fields`: each struct base in
  `parents` order, recursively, then the record's own fields), each keyed
  by its declaring owner; `THIRRecordLayout.ancestors` and
  `MIRRecordLayout.ancestors` list the struct-base ancestors in MRO order,
  nearest first (`TypeRegistry.struct_ancestors`). The order is not
  `get_all_fields`', whose reversed-MRO walk lists `B`'s fields before
  `A`'s for `C(A, B)` while C++ builds `A` first. The special-member facts
  are the struct's: a custom copy, move or destructor anywhere in the
  hierarchy runs inside the implicit one. A layout field's owner is the
  type or one of its ancestors (`constructor_initialization` and the
  validator check it), and one `MIRFieldId` appearing in two layouts
  (`Base`'s own and `Sub`'s) carries one type in both.
- **Field membership.** A field place
  `MIRField(MIRFieldId(owner, name), type)` must be one of the declared
  fields of its storage's record type (`definitions.declared_fields`, resolved
  per record from its TypeDef; "field missing from declared fields", which
  the lowering reports as "field owner mismatch"); no check compares the
  owner with the storage's type. Membership needs no verified definition:
  a derived record whose definition refuses still has its fields
  (`Child.bump` writing an inherited `self.n`, or a shadowing `n` of
  its own, lowers). MIR lowering fetches a record's layout from the
  definitions index where its storage is modelled (a field access, a
  summary path applied to an argument, a binding at an ancestor's type),
  never at the bare borrow. A summary's published write
  keeps its declaring owner (`Sub.bump_twice` writes
  `param0.Base::n, param0.Sub::k`); the walk of the path checks its
  membership in the summary contract and again at the call against the
  layout of the argument bound to the write's own parameter (a free
  function's record argument as much as a method's receiver).
- **Binding rule** (`call_contract.binds_at(records, storage, slot)`:
  `slot == storage or slot in records[storage].ancestors`). Applied at
  call arguments: parameter 0 of an inherited method (`s.bump()` binds a
  `Sub` at `Base`) and a record argument (`use_base(s)`), both in lowering
  ("call record argument mismatch", whose access rule stays: no more
  access than the holder has) and in the validator's borrowed-record
  argument binding. Owned, value and scalar bindings stay exact, and so
  does every other site: an alias local (`b: Base = s`), an Optional or
  union payload, a container element, a temporary record argument, a
  whole-record return origin, and the declared signature itself
  (parameter binding and the summary's own receiver check, where the
  types are equal).
- **Callee rule** (`callables.method_callee`). A call resolves when the
  receiver's record is the declaring record or has it among its
  struct-base ancestors, and no `@dynamic` protocol sits anywhere in the
  receiver's hierarchy (`iter_dynamic_protocols` walks the ancestors).
  The identity and the signature are the declaring record's: an inherited
  method's FunctionInfo is the base's own (`owning_type_qname` names the
  declaring record) and parameter 0 is the declaring record's type, so the
  base's one summary serves every subclass caller unchanged.
  `THIRMethodCall.receiver_access` carries the ACTUAL receiver's type and
  access, and MIR compares the receiver binding against it. A subclass
  method hiding a base method is resolved statically by sema (own methods
  first), which matches the C++.
- **Definitions chain** -- *History: the pre-unit-7 model, deleted by unit 7; [Constructors are ordinary bodies](#constructors-are-ordinary-bodies-unit-7) is current.* A base initializer is now a call of
  the base's constructor (`MIRCall.initializes`) and `MIRDefinitions`
  keeps only certificates. What the chain was: `MIRDefinitions` verified
  base-first (memoized; there is no cycle). A constructor with base
  initializers (`THIRBaseInit.base`, the base's identity) composes its
  definition from the base's: every struct-base ancestor in the layout is
  built exactly once, by a base initializer of this constructor or through
  a base's own ("base constructor not called" / "base constructor called
  twice" otherwise; a skipped base admitted only when its layout has no
  fields and its definition verifies), and the base's definition must
  verify ("base definition: <reason>"). Base-initializer arguments convert
  at the base constructor's parameter, with no statement temporaries
  (`BUGS.md#base-init-args-separate-lowering` holds what stays refused),
  and each argument is a PASSING-AWARE leg against that parameter,
  admitting exactly:
  - a scalar child parameter of the base parameter's type;
  - a lend: an owned-leaf child parameter whose passing equals the base
    parameter's borrowing passing (VIEW -> VIEW, CONST_REF -> CONST_REF;
    no buffer operation);
  - a MOVE: a `THIRMove` of an owning child parameter into an owning base
    parameter;
  - a constant: a literal or a coerced literal (`super().__init__(name, 1)`).

  Anything else refuses "base argument needs matching parameter or
  literal" -- a bare name forwarded to an owning base parameter included:
  that spelling is the open bug's C++ build failure, which MIR refuses
  rather than models. Composition: a lend renames the base initializer's
  source to the child parameter and keeps its mode; MOVE after MOVE is a
  MOVE, a MOVE into a base COPY is a MOVE that may raise, SCALAR after
  SCALAR a SCALAR; a constant keeps SCALAR for a scalar field and becomes
  a COPY that may raise for an owned leaf (the caller side then refuses
  it as an owned-leaf constant). A base named by an initializer that is
  not among the layout's ancestors refuses ("base constructor
  identity"). An owning leg
  whose base parameter feeds no base initializer refuses ("base argument
  effect not modeled"). The composed initializers follow the layout order
  (`AB(A, B)`: `A`'s, then `B`'s, then the own fields'); the body-side
  certificate (`constructor_initialization`) and the caller-side check
  (`_verify`) both compose, and the caller rules -- an owned-leaf
  constant, a copied owned parameter -- apply to the composed
  initializers.
- **Inherited constructor.** A record with no `__init__` of its own
  (`class Dog(Animal): pass`, emitted `using Animal::Animal;`) has no
  `THIRConstructor`. THIR publishes `THIRInheritedConstructor(record_layout,
  base)` for a record with one direct struct base, no own fields and no
  own special members, and `MIRDefinitions(..., inherited=...)` relabels
  the base's verified definition at the subclass type: the subclass's
  layout, the base's constructor node and initializers. It refuses
  ("inherited constructor shape") unless the two layouts carry the same
  fields and special-member facts. Sema's `inherits_init_from` covers
  more -- a multi-base record whose other bases default-construct, own
  fields with defaults -- and those publish no fact, so a caller's
  construct of one refuses.
- **What a caller consumes.** Nothing new: a base method's write
  `param0.Base::n` on a `Sub` argument projects `s.Base::n`, a member of
  `Sub`'s layout, and a conflict over an inherited field is the existing
  `replacement` kind (`view_then_rename`: a view of the inherited `name`
  live across the inherited `rename`).
- **Kept refusals.** A virtual hierarchy's method call, "unsupported
  expression type" (`call_virtual`; the subclass override's body lowers
  with no callee); a subclass bound at its base at a local alias
  (`upcast_local`, `b: Base = s`: "unsupported metadata:
  cpp_local_representation", the alias binding has no fact) or in an
  Optional payload (`upcast_payload`, `cur: Base | None = s`: "optional
  backing storage"), exact-typed sites refusing before any type compare;
  `super().m()` / `Base.m(self)` inside a subclass body, whose call
  carries no receiver (C++ `this->A::m()`; "call needs resolved ordinary
  callee", probed); generic, native and exception bases (the eligibility
  refuses them, unit-pinned). A record field of subclass type is a
  [nested record](#nested-records) member: an inherited method called on it
  (`h.s.grow()`) binds at the member's own storage.
- **Measured, inherited records** (`scripts/mir_coverage --corpus tests`,
  11187 bodies common to base and after): lowered 2293 -> 2351 (41
  constructors, 10 methods, 3 dunders, 3 free functions, 1 property; none
  lost), conflicts 20 -> 20, certified 60 -> 60; generated C++ and
  diagnostics byte-identical. The old first blockers of the newly lowered
  bodies: `base_inits` 41, `missing receiver fact` 12, `unsupported
  parameter type` 3.

#### Owned record results

`tests/cases/mir/owned_results` (exec and cpy) pins the verdicts: every
covered definition shape and caller below (with `mir_owned` /
`mir_borrowed` / `mir_write` at the owned local and the reseat), the
certified call temporaries (`use_temp`, `spawn_temp`, `lend_temp`), and
the source-reachable kept refusals (`reassigned`, `reseat_return`,
`maybe`, `make_pinned`, `make_tok`, `make_box`); `take` / `give` (an
`Own[R]` parameter) and `Holder.put` (a call result stored into a record
field) are covered under the [nested records](#nested-records) rules.
`tpyc/mir/test_owned_results.py` pins the MIR each rule
builds (the returned backing, a result slot per branch, the write modes
and temporaries), the refusals the case cannot hold (`from_borrow`,
`from_param`, `make_hooked`, `stamped`, `stamp_order`, `lend_stamp`,
`keep_named`, a `__copy__` body, a native factory) and, over hand-built
THIR or MIR, the guards no source shape reaches ("readonly local
returned", "call record result mismatch", the validator's two refusals,
a borrow live past a hand-over, a `scope_end` conflict on a call
temporary). `tpyc/thir/test_storage_facts.py` pins the call temporary's
THIR fact. Probe-only: a hoisted declaration from a call and a method
call on a call result.

- **Invariant.** `-> Own[R]` returns the record by value (C++ `Point
  make(int32_t n)`, `Representation.STORAGE`); a bare `-> R` stays a borrow
  (`R&`, a borrowed result). The caller receives owned result storage with
  no retained borrowed origins: the summary's `returns` is empty. The
  callee initializes it by CONSTRUCTING it, COPYING into it (`return
  copy(p)` keeps its `MIRCopy` event, C++ `Point(p)`), MOVING a fixed local
  out, or FORWARDING another owned result (`return make(n)`). A
  borrow-returning callee at an `Own[R]` slot is refused by the callee's
  contract (its summary has a borrowed result), never by the destination's
  ownership.
- **Classification** (`call_contract.owned_aggregate_result`). The fourth
  owned result kind beside owned leaves, borrowed results and `Own[...]`
  containers: an `OwnType` return whose bare type is a `record_type` at
  `Representation.STORAGE`. `result_problem` admits it; the body's result
  check registers the record's verified definition, whose layout must be
  movable ("owned record result needs movable record") and hook-free (the
  caller destroys what it receives: `make_hooked` refuses "custom record
  special member").
- **Return arm** (`lower.owned_aggregate_source`; `_Lowering.owned_return`,
  shared with `Own[...]` container results). A `THIRName`
  of a FIXED owned local
  (`fixed_owned`: a STORAGE-form declaration, never replaced) returns the
  local's own BACKING, `MIRReturn(storage[p])` -- not the holder
  `bindings[p]`, a borrow of it -- with no MIR move event: the move is C++'s
  (`return p;`, NRVO), and the terminal return has no live successor. An
  owned record local outside `fixed_owned` refuses "reassigned local
  returned": a REBIND_SLOT local whose reseat (`RebindStorage.OWN`)
  fills its site's owned sum and retargets the holder at that sum's payload
  ("Owned sum slots") while `storage[p]` keeps the first, so its backing
  would be stale. A STORAGE-form declaration is one sema
  never rebinds (a rebound local is a rebind-slot pointer local), so
  `fixed_owned` membership is a whole-body fact, not statement order.
  The check reads MIR's own `owned_records`
  / `fixed_owned` sets, not `THIRFunctionLayout.reassigned_locals`, which
  no producer fills (`BUGS.md#function-layout-reassigned-locals-empty`). A
  name that holds no owned record storage (a borrowed parameter, `return
  p` at `-> Own[Point]`) refuses "owned record return needs fixed owned
  local"; a readonly holder "readonly local returned" (a guard no probed
  source shape reaches). Anything else -- a construct, `copy(p)`,
  `move(p)` of a fixed local, a call -- is built into a result slot
  (owned record storage of the branch's region, else the body's) through
  `aggregate_value(..., call=True)` and returned; each branch builds its own
  slot (`pick`).
- **Validation** (`validate.py`, the owned-record return branch beside the
  owned-leaf and owned-container ones). The returned slot is owned record
  storage of the result type, no parameter, not readonly, and its layout
  movable ("owned record return needs movable owned storage"); a readonly
  or non-movable record would be copied in C++, not moved, and a holder
  (a pointer) is no owned result. Pinned over hand-built MIR.
- **Summaries: private owned-record storage** (`summaries._private_records`).
  An owned record slot is private to the body in two cases, and private
  storage publishes nothing: `returns` stays empty, and a write into it is
  no parameter write.
  - HANDED OVER: its storage leaves the body only by a TRANSFER -- a
    return (the caller owns it), a move out, an argument at an owning
    passing, or a temporary a construct moves into the record or element
    it builds -- with no borrow of it live at that transfer (the dependency
    facts' holders at the transfer point), and the body reads it otherwise
    only through the borrow its holder takes. Borrows that complete before
    the transfer are harmless.
  - KEPT: its storage never leaves the body (`p = make(n); p.bump();
    return p.x`), and every use of it is the body's own -- the holder's
    borrow of the whole storage, a read, copy or borrow of one of its
    fields, a field write into it, a whole copy out of it, or a loan at a
    borrowing passing (`CONST_REF`, `VIEW`, `MUT_REF`, `POINTER`; a
    `TRAIT` passing may copy or bind and is no loan). A consumed callee
    summary keeps nothing but the loans its transfers publish, and those
    and its writes reach the kept storage through the dependency facts,
    where a write into the body's own storage is not published.

  A holder of private storage reaching the caller is refused by the
  summary's own origin checks: a returned borrow has no parameter origin
  ("summary unsupported return origin", pinned over THIR damage in
  `test_owned_results`; sema refuses the source with "Cannot return local
  or temporary as reference"), and a write origin outside the parameters
  refuses. A record member store puts no holder in caller storage: it
  copies the record into the member, or moves it out (a transfer). A view
  member store into a caller's object (`t.s = buf`) is admitted and its
  loan checked: a loan of the body's storage there is a `store_escape`
  conflict and leaves the summary opaque ("summary stores a loan of the
  body's storage", [view fields](#view-fields)). The other stores that
  would leave such a holder in caller storage are refused at admission:
  a view appended to a parameter's list (THIR rejects it), a borrowed
  record handed to an owning stub parameter ("unsupported record
  argument"). The summary's operation
  filter admits what a private slot's body does: a record holder's
  `MIRBorrow` of it, a `MIRCopy` into it, a scalar field read of it, a
  whole-record write through a holder whose every referent is private
  storage (a reseat: an `IN_PLACE` `MIRCall`, `MIRConstruct`, `MIRCopy` or
  `MIRMove`), and the empty owned sum of a lazily built temporary
  (its `MIRStorageInit`). Whoever destroys private storage -- the body
  at scope end, a callee or container it is handed to, the caller it is
  returned to -- runs no hook, so its definition must be hook-free. Every
  covered caller of the case summarizes KNOWN (`use_result`, `use_temp`,
  `spawn_temp`, `reseat`, `collect`, `named_caller`, `spawned`,
  `lend_temp`), as does every covered factory; the refused ones
  (`reassigned`, `reseat_return`, `make_pinned`, `make_tok`, `make_box`)
  summarize OPAQUE by their refusal. A record temporary a list literal
  takes as an element (`[Point(0, 0)]`) is moved in by the literal's
  `MIRConstruct`, a transfer, so such a body summarizes KNOWN
  (`test_a_list_literal_element_temporary_is_handed_over`).
- **Callers** (`lower.aggregate_value(..., call=True)`). A resolved user call
  (`THIRCall` / `THIRMethodCall`) is a record value of type `R` when its
  summary is finalized KNOWN ("call needs finalized known summary"), its
  result is no borrow ("owned result from a borrowed call") and its
  signature hands `R` over (`THIRCallableSignature.hands_over(R)`) at
  STORAGE form ("call record result mismatch"); `call()` compares the call's type against the unwrapped
  `Own[R]`. The lowering writes the `MIRCall` into the destination,
  `may_raise` from the summary. The call's argument writes
  (`call_writes`) are recorded for the operand-order rule where the
  constructor paths hardcoded none: a factory writing an argument beside
  another operand that reads it refuses "order-sensitive eager operands"
  (`stamped`: `[stamp(c), stamp(c)]`). The arm is reached at an owned
  declaration (`p = make(n)`, `INITIALIZE`, body duration), a reseat of a
  REBIND_SLOT local (`p = make(n + 1)`, `IN_PLACE`, or a fill of the
  site's owned sum, now
  inside a full-expression boundary, so a CONSTRUCTOR reseat whose
  arguments need temporaries is covered too), an element write (`ps[0] =
  make(n)`, `IN_PLACE`), a container-literal element and an argument handed
  to an owning stub parameter (`ps.append(make(n))`: MIR's own
  `aggregate_temporary`, `INITIALIZE` of region-duration storage, no THIR backing, as for a
  constructor), a readonly named argument temporary (`read(make(n))`: a
  `THIRArgTemp` whose init is the call, admitted when the call writes
  nothing and its operands are stable, "named temporary needs stable
  scalar operands"), and the return arm. The validator's record-write
  carriers admit `MIRCall`; the initialization / replacement and element
  arms take a `MIRCall` only under a `MIRStorageWrite` fact and when the
  callee's signature hands the target type over ("call record result type
  mismatch"). Dependencies are unchanged: a
  `MIRCall` result is fresh, with no origins.
- **Call-result temporaries.** THIR grants `full_expression_storage`
  (`THIROwnedRecord(R)`) on a `THIRCall` / `THIRMethodCall` as on a
  `THIRCtorCall`: `storage.full_expression_record` admits a call when
  `THIRCallableSignature.hands_over(result_type)` -- the ONE predicate for
  "a call hands over fresh storage by value": an `OwnType` return, no
  borrowed result, STORAGE, unwrapped equal to the result type -- under the
  constructor's unchanged eligibility (unique constructor, no custom
  special members, storage-leaf fields). It is asked at a field receiver
  (`direct_field` publishes the field identity) and a discarded statement;
  `storage_facts` records a FULL_EXPRESSION backing, `temp_plan` and
  `thir/validate.py` accept the field ("full-expression storage disagrees
  with its record rvalue"). MIR takes the call where it takes a construct:
  `field()` / `temporary()` check it as a record value, `temporary()`
  builds the region storage the field is read from inline, and
  the storage adapter binds the backing. A field read of a call temporary
  passes the call's argument writes to the operand-order rule
  (`stamp_order`: `c.n + stamp(c).x` refuses "order-sensitive eager
  operands", the shape of `BUGS.md#subexpression-right-to-left-eval`).
  `return make(n).x` (`use_temp`) and a discarded `make(n)` certify. The
  emitter reads none of it: generated C++ is byte-identical.
- **Wrong-verdict risks pinned.** A stale backing after a reseat
  (`reassigned`, refused); the backing's mutability taken as proof of a
  move (the source's access is the HOLDER's, the backing being allocated
  mutable; a copy is the callee contract's `MIRCopy`); a borrowed-result
  call taken as fresh (`from_borrow`: `return ident(p)` at `Own[Point]`
  refuses; hand-built MIR writing `ident(p)` or `read(...)` into record
  storage fails validation); call writes in operand order (`stamped`,
  `stamp_order`); a borrow of a call temporary kept past its statement
  (hand-built: a `scope_end` conflict on the connected backing).
- **Kept refusals**, by reason. An Optional or union payload
  bound to a call (`p: Point | None = None; p = make(n)`), "unsupported
  record initializer"; a hoisted declaration from a call (`if flag: cell
  = make()`), "unsupported expression type"; a method call on a call result
  (`make(n).get()`), "call needs borrowed record name"; a field BORROWED
  off a call temporary (`v: StrView = make_named(s).name`), "reference
  needs local name" -- a record with an owned-leaf field gets no
  full-expression storage, constructor or call, so the `scope_end`
  conflict such a shape would raise is pinned over hand-built MIR only;
  a generic record result (`-> Own[Box[int32]]`), "unsupported return
  type"; a value-type record result, "missing constructor definition" (no
  MIR layout); a native factory (`@native` returning `Own[Point]`), no
  user call, "unsupported local type or form" at an owned local and
  "reference needs local name" as a temporary receiver; a record declaring `__copy__` (or any custom
  special member), "custom record special member" at its definition.
- **Measured, owned record results**: `scripts/mir_coverage --corpus
  tests`, 11214 bodies common to base and after: lowered 2387 -> 2445
  (31 free functions, 15 dunders, 7 staticmethods, 5 methods; none lost),
  conflicts 19 -> 19, certified 60 -> 60; the old first blockers:
  `unsupported return type` 56, `unsupported record initializer` 2;
  generated C++ and diagnostics byte-identical.

#### Nested records

`tests/cases/mir/nested_records` (exec and cpy) pins the verdicts and line
facts: every constructor (copied, moved and composed members), the member
writes (`mir_write(ln.a) mir_owned(ln.a)` at `ln.a = Point(9, "n")`,
`mir_write(self.a)` in `Line.reset`, `mir_write(ln.a)` at the call
`ln.reset(4)`), member holders (`mir_borrows(held, ln.a)`, and
`mir_borrows(p, ln.a)` through `p = ln.first()`), the five `replacement`
conflicts below beside their safe siblings, the owned and handed-over
records (`build`, `build_frame`, `owned_local_path`, `adopt`,
`keep_and_store`, `composed`), their callers handing over temporaries
(`hand_over_callers`; `adopt_live`, a holder under the member a callee
replaces by a move, conflicts), and `mutate_through_member`, whose write
through a member holder is observed through the owner (after a silent copy
the owner would keep its old value). `tpyc/mir/test_nested_records.py`
pins the MIR each rule builds
and the validator's refusals over hand-built MIR,
`tpyc/mir/test_nested_summaries.py` the summaries,
`tpyc/mir/test_return_origins.py` the path grammar and
`tpyc/thir/test_constructor_params.py` the THIR constructor-parameter
fact. Other cases pin covered bodies under these rules: `mir/owned_results`
`take`, `give` and `Holder.put`; `mir/accessor_calls` the `part` getter
and setter and `getter_through_field`; `mir/method_calls`
`field_receiver`.

- **Invariant.** An inline record field is a place of its record's
  storage, keyed like every field by (declaring owner, name): built with
  the record by its member initializer, replaced in place by a whole-member
  write (a replacement event of exactly that place; the record around it
  keeps its identity), borrowed, copied and lent through its owner's
  holder. A record's verified definition composes its member records'
  definitions, and reads its container fields' element definitions, as it
  composes its struct bases'.
- **Definitions** (`mir/definitions.py`; since unit 7 certificates only, "Constructors are ordinary bodies"). `MIRDefinitions` verifies what a
  definition reads before the record itself (`_read_records`: struct
  bases, record fields, the record members of container fields). A field is
  admitted by shape through one predicate, `scalar_leaves.modeled_field`,
  a class in the admitted set (`value_class`, `readonly` removed): a scalar
  leaf, an owned leaf or a view of one declared bare, a native container,
  an Optional of a loan-free record ([Optional record fields](#optional-record-fields)),
  or a record, which the definition, the validator's
  layouts and `place_info` read; a layout holding any other field (a union,
  tuple, scalar-Optional, `str | None`, `Ptr`, `Box` or `Rc` field, or an
  Optional whose payload holds a loan or has a record field) refuses
  "unsupported record fields".
  Each member record's definition must verify (`_member_definitions`,
  "member definition: <reason>": `HasHooked` "... custom record special
  member", a value-type or
  exception member "... missing constructor definition"), and a container
  field's elements are read through the shared `container_definition`
  (`Bag`, whose `list[Loud]` element runs a copy hook: "unsupported native
  container element"). A record holding itself inline is malformed input
  (C++ forbids it): the placeholder refuses "member definition: cyclic
  record definition". A body's layouts close transitively over member
  records (`close_layouts`), and the validator requires the layout of every
  record field ("record field needs its layout").
- **Constructor parameters and member initializers** -- *History: the pre-unit-7 model, deleted by unit 7; [Constructors are ordinary bodies](#constructors-are-ordinary-bodies-unit-7) is current.* A
  member-init entry is now the field write it is, and a construction binds
  its arguments through the one binder. What the definitions did: THIR publishes
  `THIRParam.borrowed_record` on a constructor's record parameters at the
  constructor's own const verdict, as on a function's; an `Own[R]`
  parameter carries none. `definitions.aggregate_parameter` admits a record
  lent readonly at CONST_REF or handed over at OWN
  (`owned_aggregate_parameter`). A record or container parameter the
  constructor mutates is a parameter the complete constructor body writes
  through (`p.x = 1` after `self.n = 0` lowers, its write on the
  parameter), but a caller's construct binds only the parameters above, so
  a caller constructing it refuses "constructor parameter type"; a member
  assigned from such a parameter is no member initializer (`Mutated`:
  "incomplete constructor initialization"). A record member's
  initializer (`_aggregate_initializer`) takes one of three modes: COPY from a
  readonly lent parameter, spelled bare (`self.a = a`, the implicit copy
  sema warns about) or `copy(a)` -- one initializer for both spellings;
  MOVE from an `Own[R]` parameter (`self.b = b`); or a composed construct
  (`self.p = Point(x, name)`): the member's own verified definition
  composed with one leg per argument by the base-initializer composition
  (`_composed` / `_base_leg` / `_compose`, with the role's wording,
  "member argument ..."), recorded as `MIRComposedConstruct(initializers)`
  and moved in. A literal leg is a constant of the member's field; one
  parameter in two borrowed legs is two copies (`Twice`). A borrowed
  parameter is never moved ("constructor move needs an owned parameter").
- **Record copies may raise.** `definitions.layout_copy_may_raise` is the
  one exit fact of a whole-record copy: an owned-leaf field whose TypeDef
  copy may raise, any container field (a new allocation), or a member
  record whose copy may, recursively; a member whose layout is unknown
  may. Every record copy reads it: the lowering's `MIRCopy`
  (`_Lowering.copy_may_raise`), the validator's record copy
  (`record_copy_ok`, shared by record destinations, element writes and
  member writes) and the construct's member check (`member_operand`). A copy of a
  record with a `str` or a `list` field is an exceptional exit, an
  all-scalar record's is not (`duplicate_point`, `duplicate_plain`,
  `duplicate_flat`); a record whose container element runs a copy hook has
  no definition, so its copy refuses (`duplicate`, "unsupported native
  container element").
- **Receiver initialization** -- *History: the pre-unit-7 model, deleted by unit 7; [Constructors are ordinary bodies](#constructors-are-ordinary-bodies-unit-7) is current.* The entry is ordinary
  statements and `MIRMemberInits` is deleted. What it was: a composed member was
  initialized at entry field by field, as the receiver is:
  `MIRMemberInit(MIRMemberInits(fields))`, one initializer per field of the
  member's layout (a value over a parameter or a constant), nested for a
  composed member of a composed member, raising when some field may
  (`value_may_raise` recurses through it, an Optional member's over its
  payload's layout). The receiver's own initialization is one
  `MIRMemberInits` too. The dump prints each value with the CFG's rvalue
  printer: `initialize-receiver %0 ({read %1, copy (*%2) may-raise},
  read %1)` (`Built`). A copy reads the lent parameter's holder and a move
  the `Own[R]` parameter's storage slot (`Line`: `(copy (*%1) may-raise,
  move %2)`); a container literal stays a `MIRConstruct`, inside a composed
  member too (`HasListed`). The one operands walker (`validate.operands`,
  which recurses through `MIRMemberInits` and skips a literal's constants)
  lists the slots entry initialization reads (entry liveness, reachable
  slots), and one line-fact function gives an entry write the facts its
  method twin's statement has (`collect._line_write`). The entry rule
  (`validate_member_init`) stays its own: every operand is a whole
  parameter other than the receiver, or a constant (no projection past a
  lent parameter's `(*%n)`), and each member class keeps its source
  predicates and refusal texts -- what a caller's construct can reproduce
  (`definitions._caller_operands`); it recurses into each nested field by
  its own field's rule, with the member layout's movability and
  copyability (`test_receiver_record_member_validation`,
  `test_the_entry_rule_reads_only_what_a_callers_construct_can_supply`).
  A move into a base's own copy that the base copies on is a copy of the
  derived parameter (`definitions._compose`), refused at a caller as any
  copy of an owned parameter is.
- **`Own[R]` parameters.** A record handed over at OWN is the body's own
  storage: a PARAMETER slot, owned storage, BODY duration, mutable, fixed owned,
  reached through an unnamed holder borrowed at entry (the parameter's
  name spells the storage slot, so a line annotation needs no `param(p)` /
  `local(p)`), its definition verified (`take_hooked`: "custom record
  special member"). C++ passes it as `R&&`, the caller materializing the
  storage; the model follows the `Own[list[T]]` parameter's. It is returned
  as that storage (`give`), moved into a member (`adopt`, `Frame.__init__`),
  and a caller hands it a temporary (`hand_over`).
  A body binds it only as exactly `Own[R]` at OWN
  (`call_contract.parameter_binding`); `parameter_contract_problem` refuses a binding of it
  that is readonly ("call parameter binding differs from its derivation"),
  holds a pointer at the caller's record ("reference type mismatch") or is
  passed at another passing ("call parameter passing differs from its type").
- **Callers' constructs** (`aggregate_value`'s construct arm, the validator's
  `construct_ok`). An operand follows its constructor parameter's
  passing, never "a temporary moves": a record lent at CONST_REF is copied
  by the member through a holder -- the name's own, one borrowing a member
  place (`Line(ln.a, mk(x))`), or one of a full-expression temporary bound
  to the parameter (`Line(Point(x, "a"), mk(x))`: C++ binds the temporary
  to `const Point&` and the member initializer copies it); a record handed
  to `Own[R]` is built, copied or returned by a call into a full-expression
  temporary the member moves from (`aggregate_temporary`); a composed member
  is built over the caller's own operands into full-expression storage and
  moved in (the member's construct carries its copies, the outer construct
  only moves). The validator takes per member a borrowed holder of a
  copyable layout, raising by `layout_copy_may_raise`, or a mutable owned
  temporary of a movable one. A writing operand refuses "effectful
  constructor argument"; a composed construct outside a full expression
  (a tuple member) "temporary needs full-expression boundary".
- **Whole-member writes** (`ln.a = v`, `self.a = v`:
  `_Lowering.member_write`). The value is what record storage takes
  (`aggregate_value(call=True)`): a construct, `copy(x)` of a name or a member
  place, a borrowed name stored bare (C++ copies it; sema warns), a member
  read stored bare (`self.a = self.b`), an owned local stored bare (`self.b
  = t`, a copy through its holder), an `Own[R]` parameter moved at its last
  use, or a call handing over an owned result (`Holder.put`). The lowering
  writes `MIRAssign(member place, value, MIRStorageWrite(IN_PLACE))`; the
  validator requires that fact with no rebind owner, a mutable place and a
  movable member ("record member write needs a replacement fact", "store
  through readonly storage", "record member replacement needs movable
  record": C++ move-assigns the member). `storage.owned_field` is the one
  "replaced in place" predicate -- every field the record stores inline
  but a scalar leaf; a view member is the loan the record holds
  (`value.loan_field`, the one field rule the composer's field position
  reads too), whose write rebinds that loan -- so `storage_destination`
  makes the write an event of the member place, and the line facts call
  the write owned.
  `reach.reaches` (retention's `storage_exclusions`) reaches holders at or under the member, not a
  sibling member and not the record around it: replacing `f.line` reaches
  a holder of `f.line.a`, replacing `f.line.a` spares `f.line` and
  `f.line.b`; across external origins that may alias, any holder
  (`test_member_write_reach`). A scalar write under a member (`ln.a.x =
  7`, `self.a.x += 1`) stays no storage event.
- **Record moves.** A `MIRMove` out of owned record storage is a
  replacement event on the source (`coverage.moved_storage`, records beside
  owned leaves and containers): a holder of the record or of a member
  inside it read after the move conflicts. Pinned over hand-built MIR
  (`test_a_record_move_replaces_its_source`): THIR spells the bare store a
  copy while a borrow of the source is live.
- **Member copies, results, receivers and arguments.** A record source
  names storage in place (`_Lowering.record_source`): a name, `self`, or a
  member read through them. `copy(ln.a)` copies the member place
  (`copy_does_not_replace_source`, `Line.swap`); a member copied into an
  element is copied from the place (`ps[0] = copy(ln.a)`), into a literal
  through a holder of it. `return self.a` at a borrowed record result
  borrows the member place into the result holder (`Line.first`), never
  with more access than the path has ("return increases access"). A member
  receiver (`o.inner.bump()`, `ln.a.get()`) and a member argument
  (`read_point(ln.b)`) are lent through a holder borrowing the member place
  at its access, and the call binds at the member's own record
  (`via_member`: an inherited method called on a `Sub` member). Reseats,
  aliases and wrapper captures keep needing a name (`reference_name`).
- **Parameter paths** (`call_contract.py`). A `MIRParameterPath` is (under a
  wrapper parameter, after its member selector: "Wrapper parameters" below) one or
  more fields through inline record members, then at most one container
  projection. `path_walk` walks its fields with the one place walk
  (`shapes.walk`, from the object `path_projections` names): hop 0 reads
  the record the parameter lends (at a call, the record the ARGUMENT binds,
  which may be a descendant), hop k the previous field's member, each field
  a member by the one rule (`field_member`) over the layouts the caller
  holds; a field after a projection, a field under a binding with no
  record, or an intermediate field that is no record is malformed.
  `resolve_path` keeps the trailing projection beside the walk; the access
  is the walk's end, readonly when the parameter is or when any field on
  the way is `readonly[...]`. One endpoint table, `endpoint_admitted`,
  serves writes and return origins over the walk's end: a write needs a
  mutable end at a scalar leaf, an owned leaf, a record or container member
  replaced whole (the same endpoint for both aggregates), or a
  projection of a container field whose members MIR models (an element
  write keys on the container's access); a
  return takes no projection and ends at an owned leaf or a view member of
  a view result's family, or at a container or record field of exactly the result's type at
  no more access. `write_problem` / `return_origin_problem` are those two
  steps ("invalid call write path" / "invalid return parameter" for a
  malformed path, "unsupported call write field or access" / "unsupported
  return origin type or access" for its end), and `summary_problem` takes
  the layouts its paths walk over (the definitions' in lowering and in
  the summary, the body's in the validator; that a path's records verify
  is the summary's check and, at a call, the lowering's `record_layout`);
  a walk's own
  refusal is the reason ("field missing from declared fields": since slice
  3 membership is a record's declared fields, `definitions.declared_fields`,
  carried as `MIRFunction.record_fields`, with or without its layout). At a
  call, `crossed_records` reads the walks the contract check made and names
  what only the call knows -- the record each field is read from, the first
  one the record the argument binds -- whose layouts the lowering registers
  and the validator checks the body carries ("call path record has no
  layout"). The dump spells every field:
  `writes={param0.line.a.x}`.
- **Summaries** (`summaries.py`). A published write or return origin is
  the full field chain (`_published_path`) under the rules above. A read
  through members (`ln.a.x`, `f.line.a.x`) under a borrowed record or the
  body's private storage is admitted (`_member_chain`: every field before
  the last a record member, the last a storage leaf). A record holder of a
  member is tracked to the member's place, so a write through it is
  published (`mutate_through_member`: `writes={param0.a.x}`) and a returned
  one meets the return rule (`returns={param0.a}`). A member replaced whole
  under a parameter is published as a write of the member (`Line.reset`:
  `writes={param0.a}`), under private storage as nothing; a construct, a
  move, or a copy of a whole owned root, a holder's referent, another
  member or an element may fill it. A record temporary a construct names
  as an operand -- an `Own[R]` member, a composed member, a list element --
  is moved in: a transfer under the handed-over rule (private when nothing
  else reads it and no holder of it is live at the construct), so `build`,
  `build_frame` and `composed` summarize KNOWN with nothing published.
- **Conflicts.** The existing `replacement` kind: a member replaced while
  a holder borrows it (`replace_live`; sema warns "Mutation of 'ln.a' while
  borrowed"); the member of a parameter that may alias the holder's owner
  (`alias_external`); the member above the holder's replaced
  (`replace_ancestor`: `f.line` under a holder of `f.line.a`; sema warns
  on `f.line`); a method's published member write under the holder's
  prefix (`member_receiver`: `held = f.line.first(); f.line.reset(9)`); a
  member replaced through the callee's summary (`reset_live`: `held =
  ln.a; ln.reset(4)`). Sema is silent on `alias_external`,
  `member_receiver` and `reset_live`: on `reset_live` TPy reads the new
  member where CPython keeps the old object (probed: 4 against 9), the
  inline-member face of `BUGS.md#field-loan-whole-record-callee-unchecked`;
  `alias_external` called with one object twice diverges the same way
  (`BUGS.md#aliased-record-params-member-replaced`). A member holder
  outliving its owner's block is a `scope_end` conflict (`escape_member`,
  unit-pinned).
- **Kept refusals**, by reason.
  - A record ELEMENT whose record has record fields (`[Built(x, "e")]`),
    "unsupported native container element" (`listed`): a container element
    stays a record of leaf fields (`plain_record_element`).
  - Union, tuple, scalar-Optional, `str | None`, `Ptr`, `Box` and `Rc`
    fields, and Optional fields whose payload holds a loan or has a record
    field, "unsupported record fields" at the definition (a tuple and an Optional
    scalar field pinned in `test_owned.py`); Optional fields of loan-free
    records are modeled since ([Optional record fields](#optional-record-fields)).
  - In a member initializer: one `Own[R]` parameter stored into two
    members (`self.a = copy(p); self.b = p`), "unsupported record
    initializer". Since unit 7 a call result (`self.a = mk(x)`), a record
    argument of the member's constructor (`self.line = Line(a, b)`) and a
    member whose constructor has body effects or stores an owned-leaf
    constant lower: the entry is ordinary statements and a construction
    calls the constructor's summary.
  - A local moved into an `Own[R]` constructor argument (`moved_local`: `q
    = Point(x, "q"); Line(Point(1, "a"), q)`), "move needs fixed movable
    owned local": THIR spells the last-use `THIRMove` in STORAGE form over
    a BORROW-form name, and the record move requires equal forms.
  - An `Own[R]` parameter forwarded to another owning parameter (`return
    consume(p)` with `p: Own[Point]`), "unsupported record argument"
    (probed).
  - Two-level member borrows into a local (`held = f.line.a`) are THIR
    rejects (`decl.slot_type`; probed); two-level member writes (`f.line.a
    = v`) lower since 2026-10-29. MIR reaches two levels through a call
    (`held = f.line.first()`) and pins the two-level reach with
    `affects()` rows.
  - Full-expression temporaries of a record with a record member:
    `Pair(Flat(x), 1).a.x` refuses "missing or invalid full-expression
    storage", `make(x).a.x` and the scalar `make(x).n` "reference needs
    local name" (probed): `full_expression_record` admits storage-leaf
    fields only.
  - A field read through a borrowed call result (`h.part.x`,
    `record_accessors`), "reference needs local name": THIR publishes no
    `field_identity` on a field read whose receiver is a call.
  - `print` with a writing call beside a non-literal read (the case's
    `main`, pinned), "order-sensitive eager operands", as it should; a
    string literal beside a writing call (`print("label", tick(c))`,
    probed) refuses the same, because `lower._literal` counts no
    `THIRStrLiteral`.
  - Precision, with no refusal: a member-borrow result (`Line.first`) is
    covered, not certified ("borrow evidence: demanded operation is not a
    supported record borrow"); a moved `Own[R]` constructor parameter keeps
    a dead holder borrow at entry (`%2 = borrow %1` in `Line.__init__`);
    an elements write on an external list conflicts with any live external
    record holder (`held = ln.a; xs[0] = 1`, probed), the may-alias branch.
  - Identity and modeling are separate questions: THIR's
    `storage.field_identity` publishes an identity for every declared
    member, keyed by its declaring record; MIR's `modeled_field`
    (`record_type`) alone decides which members MIR models. A value-type,
    generic-base, typed-dict or exception record member is identified, and
    MIR's member definition refuses it (the value-type and exception
    members pinned: "member definition: missing constructor definition").
- **Measured, nested records** (`scripts/mir_coverage --corpus tests`).
  Base master d762c4c0d9: 2470 of 11258 test bodies lowered (21.9%; 26.3%
  excluding module init), 19 bodies with a conflict, 63 certified, 1156
  with exceptional exits. By mechanism, over the bodies both trees hold:
  definitions +72 lowered, member writes, borrows, copies and receivers
  +63, field-by-field receiver initialization +21, none lost at any stage.
  Summaries over 11368 bodies: no KNOWN summary lost or changed, 12 then
  48 gained. Chaining container-field elements into the definition took
  the definition from 24 records whose container element MIR does not
  model (`list[list[int32]]`, `list[Point | None]`, `list[Loud]` with a
  copy hook); no body that lowered stopped lowering -- only the first
  reason of bodies that already refused changed. Existing snapshots changed only in the source comments the
  flipped pins edit (echoed into the generated C++) and fingerprints; no
  diagnostic changed.
  Integrated tree (master 17b8fa09a5 merged in): 2633 of 11260 bodies
  lowered (23.4%; 28.1% excluding module init), 19 with a conflict, 73
  certified, 1176 with exceptional exits. Over the 11250 bodies both runs
  hold: lowered 2468 -> 2631 (+163: 70 constructors, 28 free functions,
  26 methods, 20 dunder methods, 19 properties), none lost; by former
  first blocker: 70 "unsupported record fields" constructors and 14 of
  their callers and factories, 48 "unsupported borrowed expression form"
  (a member returned by reference), 12 "unsupported parameter type"
  (`Own[R]`), 7 "record field replacement is unsupported", 8 callers of a
  method whose summary became KNOWN, 2 member receivers, 2 others.
  Conflicts 19 -> 19 (the sample holds none of the nested-record conflict
  shapes; they are pinned in `tests/cases/mir/nested_records`), certified
  63 -> 73, exceptional exits 1156 -> 1176, every one of the 20 on a
  newly lowered body: no body lowered in both runs changed its exit fact.

#### View fields

A record member of a view type (`StrView`, `BytesView`;
`scalar_leaves.view_leaf`) stores a loan in the record object: its
constructor sets it (a base's or member's constructor included), a view
member write (`t.s = v`), a record written through a place or a callee's
published loan transfer rebinds it, a copy or move carries it, an inline
member record keys it under the member, an owned record result hands it
to the caller and an `Own[R]` parameter brings it in; every read of it
takes that loan. `tests/cases/mir/view_fields` (reads, construction,
copies) and `tests/cases/mir/view_field_stores` (stores, transfers,
member records, owned results; both exec and cpy) pin the verdicts and
line facts; each conflict section replaces the source only under `if
flag:`, which the run never takes, so no dangling view is read.
`tpyc/mir/test_view_fields.py` and `tpyc/mir/test_view_field_stores.py`
pin the MIR each rule builds, the dependency state and the validator's
refusals over hand-built MIR. `views_as_places` `Holder.__init__` pins
the constructor as `mir(covered)` with `mir_borrowed(self.v)`.

- **Invariant.** A view member's loan is stored in the record OBJECT: the
  dependency state keys it at the member place of the slot that names the
  object (`dependencies.object_keys`, dumped `%0.__main__.Tok::s`: for the
  body's own owned record storage and for a record parameter, borrowed or
  `Own[R]`), never under a holder; an inline record member's view members
  are keyed on the full member path
  (`%0.__main__.Outer::inner.__main__.Tok::s`).
  `MIRDependencies.objects` exposes each slot's keys and `stored_loans`
  their union. Owned storage's entries are set by the whole write that
  fills it (`stored_on_fill`): a construct stores the referents its view
  operand holds (a record member's operand, the loans its own object
  stores under the rest of the path), a copy into owned storage carries
  over the stored loans of the object it copies (resolved through the
  source holder on the full member path), a move the source's entries, a
  call's owned record result exactly the loans its RESULT transfers name;
  a whole write replaces the storage's holder leaves and its entries
  together. A move out of an object with no entry, or a call result no
  transfer fills, refuses rather than storing nothing. A record
  parameter's entries are seeded at entry with one opaque referent,
  `MIRReferent(held=True)` (`held:` in the dump): the loan views
  owned-leaf storage outside the body that may be ANY external storage, a
  sibling field of the same object included, so `reach.reaches` answers
  True for a held referent against every external one. A view member write and a callee's transfer update an
  entry in place (below).
- **Reads.** `t.s` is a `MIRBorrow` of the member place into a view
  holder; `resolve_referents` replaces each object the holder reaches by
  that object's entry (`_stored_loans`), so the holder takes the loan
  itself, independent of the record (`detached_view`: `v = t.s` inside a
  branch keeps viewing `a` after `t` is dead). An object with no entry
  refuses ("view member read of an object with no stored loan",
  `MIRUnseededLoan`); it is never read as an empty set. The member place
  is read whole: the validator refuses a projection after it ("projection
  through a view member"), and a write of it is only the view member
  write below. Line facts spell a member through its holder:
  `mir_borrows(t.s, buf)`, `mir_borrows(u.s, t.s)` for a parameter's held
  loan, `mir_borrows(t.s, static)` for a literal.
- **View member writes.** `t.s = v` in a function, a method or a
  constructor body (after member initialization) rebinds the member to
  the loan the view holder `v` holds: `MIRAssign(member place,
  MIRAlias(holder))` with no storage fact, since the record keeps its
  identity and no storage is replaced (`storage.owned_field` excludes view
  members; the summary publishes no `MIRParameterWrite` for it). The
  value is a full expression, so a temporary's loan ends with the
  statement and the scope-end analysis reports it. The validator
  (`validate_view_member_write`) requires no storage fact ("view member
  write needs no storage fact"), a mutable path (the place walk refuses a
  readonly holder or member record first: "store through readonly
  reference" / "storage") and a non-global view holder of the member's
  family ("view member write needs a view holder"). The dependency pass
  (`dependencies.store_loan`) updates the entry of every object the
  record place may reach: STRONG (the loan replaces the entry) when the
  place reaches exactly one object the body owns, a weak union otherwise
  (several objects, or an external one, which may alias another): in
  `direct_write`, `t.s = b` leaves `t.s` holding `b` only, so replacing
  `a` afterwards is no conflict, while `fill_static` keeps the caller's
  loan beside the literal (`mir_borrows(t.s, t.s|static)`). A write with
  no origin or into an object with no entry refuses ("view member write
  with no origin", "view member write of an object with no stored
  loan").
- **Record writes through a place.** A record written whole through a
  place -- an IN_PLACE reseat through its rebind-slot holder (`(*r) =
  R(...)`: `reseat_self`, `branch`) or an inline member replaced in place
  (`o.inner = R(...)`: `member_self`) -- fills every entry under each
  object the place may reach from the loans the written record stores
  (`dependencies.fill_through` over `record_fill`, the fill a slot's own
  whole write takes: a construct's operands, a copy's or move's source
  object, a call's RESULT transfers), read off the state before the
  write. STRONG when the place reaches exactly one object the body owns
  (a local's storage, an `Own[R]` parameter's object), a weak union
  otherwise (several objects, or a borrowed parameter's, which may alias
  another): `store_loan`'s rule, member by member. An entry the object
  lacks refuses ("record write into an object with no stored loan",
  "record write into no object"). The write stays a replacement event
  of the place, checked over the state it leaves, so a new record
  viewing the storage the same write replaces (`r = R(mk(k + 1), r.buf)`)
  is a `replacement` conflict; `member_fresh` (the new member views
  static storage) is covered, and so is `branch` (the old object's loan
  was static). A holder of the member sees the new record: in
  `member_alias`, `a = o.inner` is `R& a`, and `a.s` reads the replacing
  record's view of the buffer it replaced. A view member write or a
  member record write through a borrowed parameter's member registers
  the parameter's layout (`lower.held_root`, as a nested view read does),
  so its nested entries exist: `fill_inner` (`o.inner.s = s`) publishes
  `param0.inner.s <- param1` and its caller `fill_inner_replaced`
  conflicts. A named record copied into a constructor operand
  (`Wrap(copy(t))`) fills the fresh temporary from the union of the
  entries of every object the source place reaches (`wrap_copy`
  covered, `wrap_copy_replaced` a conflict); a move still needs a fixed
  owned local of the move's own form (`wrap_move` refuses as for any
  record). An optional backing (a deferred argument, a select's storage)
  is filled by the write that engages it; on a path that leaves it empty
  it has no entries and the join keeps the engaged path's (`deferred`,
  `selected` certified).
- **Liveness.** A stored loan is live while a live holder reaches its
  object: `dependencies.live_holders` is the one closure (a state entry
  whose slot is live, plus each stored-loan entry, one of
  `MIRDependencies.stored_loans`, under a referent of a live entry,
  transitively). `MIRDependencies.pinned` (`caller_keys`) holds the
  entries of the objects the caller reaches -- every record parameter's,
  the constructor's receiver and an `Own[R]` parameter included -- and
  `live_entries` keeps each of them live to every exit whatever the
  body's slots are, minus the loan it was seeded with at entry (the
  caller's own, which the caller checks against its own replacements, so
  an owned-field setter on a record with a view member does not conflict
  in every callee). `MIRDependencies.live` is that one view, read by the
  dependency pass's active and holder maps and entry state, retention,
  scope ends and payload ends. The storage-evidence audit requires an
  entry for every loan of every object a live holder reaches, an inline
  member object under a reached member prefix included
  (`MIRDependencies.objects`; "live record %N has unknown stored
  loans").
- **Caller stores.** `MIRDependencies.caller_stores`
  (`dependencies.caller_stores`) is the one enumeration of what the body
  may store where the caller reaches: at every feasible point (presence
  facts; an exceptional exit may follow any statement) what each pinned
  entry holds, and at each feasible owned-record return
  (`feasible_returns`, the scan the return escapes share) what each entry
  of the returned object holds (`MIRCallerStore.result`). The store
  escapes and the summary's transfers both read it, each with its own
  filter: the escapes take every entry, the transfers leave out an
  `Own[R]` parameter's (below).
- **Store escapes.** A loan of the body's own storage stored in an object
  the caller reaches outlives the body: `STORE_ESCAPE` (verdict kind
  `store_escape`, `storage_evidence.analyze_store_escapes` over the caller
  stores, run for every lowered body by `collect.analyze_body` and
  attributed on the certificate path): `fill(t: Tok, k)` storing a local
  buffer in `t.s`, `make_local` returning `Tok(buf, 3)` with `buf` a
  local, `own_store_local` storing one in an `Own[Tok]` parameter's
  object (the caller's temporary outlives the call). A loan stored and
  later overwritten still escapes (the union over all points). The
  scope-end analysis leaves terminal edges to it and inspects every
  non-terminal edge, also one whose target has no live slot (a pinned
  entry still lives there): `loop_store`, a loop body's local stored in
  `t.s`, is also a `scope_end` conflict at the back edge. A temporary
  reaches a view member through a constructor argument
  (`return Tok(mk(k), 1)`: a `scope_end` at the full expression's
  non-terminal edge beside the store escape; `temporary_source`); sema
  refuses only a direct member write of a temporary view source, and MIR
  refuses optional and union locals of an owned leaf, so no payload loan
  reaches one. `--dump-mir` prints the return and store escapes in an
  `escapes` section after the payload facts.
- **Replacements.** A replacement event is checked against the live
  holders of BOTH the state it enters and the state it leaves (the
  complete assignment's dependency transfer: the callee's transfers and
  the result fill included), plus, for a whole write of a slot, the
  filled object's own entries before the holder rebinding to it is live:
  the statement may itself install a loan into the storage it replaces.
  `call_fill` (`buf = store_return(t, buf)`: the callee stores `buf`'s
  loan in `t.s` and the assignment replaces `buf`) and `reused_construct`
  (an own-site rebind of a reused backing whose new record views the
  backing's previous `buf`) are `replacement` conflicts.
- **Layouts.** `MIRDefinitions.layouts` holds a field-only layout per
  record whose fields MIR models (`definitions.held_record_layout`), a
  record whose constructor does not verify included; it is never a
  construct's, copy's or destruction's definition (`MIRHeldLayout`). A body
  registers it only where loans are read or stored: a view member read, or
  a record argument lent to a callee whose parameter type, or the
  argument's own type, holds a loan (`lower.lent_loans`); registering
  every borrowed record parameter's layout up front refuses covered
  stdlib bodies (25 on the census). A member place lent there (`o.inner`),
  read (`o.inner.s`) or written (`o.inner.s = v`, `o.inner = R(...)` of a
  record holding a loan) registers the root object's layout too
  (`lower.held_root`): its loans are keyed on the full member path under
  that object.
- **View member initializer** -- *History: the pre-unit-7 model, deleted by unit 7; [Constructors are ordinary bodies](#constructors-are-ordinary-bodies-unit-7) is current.* A view member-init is now
  the view field write it is (`alias` of the view holder the argument
  lowers to), and a base's or member's view loan reaches the caller through
  the constructor's summary. What the definitions did
  (`definitions._view_initializer`; the field's class says it is a loan): A view member stores the loan of a
  `str` / `bytes` parameter the caller lends (CONST_REF or VIEW, read
  through `coerce(%s -> StrView)`), of a view parameter passed by value
  (`definitions.view_parameter`), or a literal's static storage. No
  buffer is copied, so it never raises. The receiver's entry
  initialization spells it as the CFG does -- `borrow (*%1)` of a lent
  owned-leaf parameter, `alias %1` of a view parameter, the literal's
  constant -- and its line fact is `mir_borrowed(self.s)`. At a caller's
  construct the view operand is the view holder the argument lowers to
  (`view_value`: a view, or a borrow of a `str` the body owns or is lent);
  a literal member is a holder of static storage the construct builds. The
  validator checks the receiver side ("invalid receiver initializer /
  parameter / constant": a parameter the constructor owns never lends) and
  the caller side (`member_operand`: a holder lending an owned leaf
  of the member's family, `coverage.leaf_borrow`). A base's or member
  constructor's view initializer composes with the leg that feeds it
  (`definitions._compose`): a name of the derived constructor's lent
  parameter (`definitions.lends_view`: a view parameter of the member's
  family or an owned-leaf one lent) becomes the initializer a direct
  `self.s = s` would be (`Derived`, `Shell` building `Tok(s, 1)` as a
  member); a str / bytes literal the base parameter views in place (a
  view parameter, or one passed as a view: `_BaseLeg.static`) becomes the
  static loan (`Fixed`); a moved or owning leg (the callee's own copy)
  and a literal a reference parameter binds (a temporary of the call)
  refuse ("base argument borrow not modeled", "member argument borrow
  not modeled"). An inherited constructor (`using Base::Base`) reuses the
  base's initializers, view initializers included: its parameters are the
  base's (`Kid`, `child`). A temporary argument is a `scope_end` conflict where
  the member is read after the declaration (`derived_temp`, `kid_temp`,
  `shell_temp`); `derived_lent` and `fixed_base` are covered.
- **Summary endpoint.** A view member of a view result's family is a
  return endpoint (`call_contract.endpoint_admitted`,
  `dependencies.call_return_problem`): `first(t: Tok) -> StrView: return
  t.s` and `Tok.text` summarize KNOWN with `returns={param0.s}`, and the
  caller resolves the path through the argument object's stored loans
  (`callee_reads`: `len(t.text())`).
- **Loan transfers** (`call_contract.MIRLoanTransfer`, in
  `MIRCallSummary.transfers`).
  A transfer says the view member `path` names -- under parameter
  `holder`, or under the record an owned-record result hands over
  (`MIR_RESULT`) -- may hold the loan `source` names: a parameter's own
  (the view it holds, the owned leaf it lends; empty `source_path`), the
  loan a view member of a record parameter stores (`source_path`), or a
  literal's static storage (`MIR_STATIC`). `--dump-mir` prints them on
  the call: `stores={param0.s <- param1}` (`Tok.reset`), `stores={result.s
  <- param0}` (`make`), `stores={param0.s <- param2, result.s <-
  param1.s}` (`write_copy`). `summary_problem` validates each
  (`transfer_problem`: "invalid loan transfer", "invalid loan transfer
  holder" -- the path must end at a non-readonly view member under a record
  parameter lent mutably or under the owned-record result -- and "invalid
  loan transfer source" -- a literal, a readonly view parameter by value
  or owned-leaf parameter at a borrowing passing of the member's family,
  or a view member under a lent record parameter or an `Own[R]` one).
  EXTRACTION (`summaries._transfers`): the union over the caller stores
  of what each entry of a borrowed record parameter's object holds, and at
  each owned-record return what the returned object's entries hold. The
  loan a lent parameter's entry was seeded with (`seeded_loan`) is
  skipped for that entry only: the caller joins weakly there, so its own
  loan stays. A RESULT entry publishes it like any other held loan: `ident(r:
  Own[Tok]) -> Own[Tok]: return r` publishes `result.s <- param0.s`, and
  `maybe_own` (storing into `r.s` on one path) `result.s <- param0.s` and
  `result.s <- param1`, so the caller's result keeps the argument's loan
  (`own_result_seed`, `own_member_seed`, `ident_kept` conflict when it is
  replaced). A held loan maps to `(parameter, member path)`, an
  unprojected external one to the parameter, a static one to
  `MIR_STATIC`; the summary stays opaque for any store escape ("summary
  stores a loan of the body's storage", an `Own[R]` parameter's entry
  included), a loan outside the parameters such as a global's ("summary
  transfer source outside the parameters", `store_global`), a loan of a
  member or element of the caller's storage, such as an owned-leaf field
  `r.buf` ("summary transfer source is a member or element": no source
  form names that storage, `store_member`), and a path through a field
  its record does not declare (membership is checked against the
  declared fields: the walk's own refusal, "field missing from declared
  fields", reachable only with a definition the lowering did not see),
  and one through a record whose definition is refused ("summary
  record: <reason>", the summary's check over its paths' field holders).
  What a body stores in an `Own[R]` parameter's object is not
  published (`own_store_param`): the argument is a temporary of the call
  the caller never reads again, which holds because a named, copied or
  moved record argument to an `Own[R]` parameter refuses ("unsupported
  record argument": `consume_named`, `private_own_alias`,
  `own_write_transfer`). Every end of a transfer that names a path is
  walked once, by `transfer_problem` through `call_contract._end_walk`
  (under a lent record parameter from the record it lends, under the
  result or an `Own[R]` parameter from that storage; a holder end only
  under a lent parameter or the result), and recorded with the summary
  check's other walks; a call (the lowering, the validator) reads those
  walks in `crossed_records`, registering the record the argument binds
  and each holder's layout. APPLICATION
  (`dependencies.apply_transfers`, for a `MIRCallStmt` and for an
  `MIRAssign` of a `MIRCall`): each parameter transfer joins its source's
  loans (`transfer_loans`, through `call_place`) into the entry of every
  object the holder argument may reach, a WEAK union (a callee store is
  a may-effect), repeated to a fixpoint since one transfer's source may
  be another's holder and the arguments may be one object; then a
  borrowed result resolves and, in the dependency pass's final pass over
  the fixpoint's states, is checked ("missing call return origin",
  `call_return_problem`), then a RESULT fill stores exactly the loans its
  RESULT transfers name (`stored_on_fill`'s call case). A missing
  entry or origin refuses ("call transfer into no object", "call
  transfer into an object with no stored loan", "call transfer with no
  origin"). Each transfer path is walked over the declared fields, and the
  validator checks that every record a published path crosses
  (`crossed_records`) has a layout in the body ("call path record has no
  layout"), and a storing call counts as a write for the eager-operand
  order guard and the temporary-stability checks (`_summary_writes`:
  `first(t.s, put(t, buf))` refuses as "order-sensitive eager
  operands", `eager` in the case). A stub's summary has no transfers.
- **Member records.** A record whose inline member record holds a view
  (`Outer.inner: Tok`) is modeled: `definitions._record_members` admits
  the member when the member's own definition verifies, `object_keys`
  recurses through inline members, construct and copy fills and the
  audit work on full member paths. `outer_read` (`len(o.inner.s)`) is
  covered and `outer_replaced` a `replacement` conflict.
- **Owned results and `Own[R]` parameters.** A record holding a view
  returned by value (`make(s) -> Own[Tok]: return Tok(s, 2)`) publishes
  its member loans as RESULT transfers and is escape-checked at the
  return; the caller's result storage holds what they name
  (`made_replaced`: `t = make(b)` gives `mir_borrows(t.s, b)`). An
  `Own[R]` parameter's entries are seeded held external like a borrowed
  parameter's (`consume`). A record built or returned for the call --
  a construct or a call result as the argument -- may hold a view at a
  lent or `Own[R]` record parameter (`consume_built`).
- **Conflicts.** `replacement` when the viewed source
  is replaced while a holder of the record lives (`alias_replaced`,
  through `u = t`, a move at `t`'s last use; `copied_rebind`, through an
  alias `u = t` (`Tok& u = (*t)`) that keeps the first object when `t` is
  reseated; `callee_reads_late`; `callee_replaces`, the callee summary's
  write of `b.buf`), when an external write may replace a held loan
  (`sibling_view`, the parameter's member viewing its sibling field;
  `aliased_params`; `late_read`, `alias_late`, `loop_alias`, an alias
  reseated in a loop, and `pick_late`, through a borrowed record result
  reaching both arguments; these bodies' summaries are opaque, see
  "Argument-alias obligations"), and when a view read off the member outlives
  the record (`detached_view`); `scope_end` when the source is a
  temporary of the declaration (`temporary_source`: `Tok(mk(k), 1)`).
  Through stores (`view_field_stores`): `replacement` after a callee's
  transfer (`retained_by_callee`, `maybe_retained`, `call_fill`), after
  a direct write (`direct_write_source`), in a callee that stores and
  then replaces its other parameter's field (`store_and_replace`),
  through a member record (`outer_replaced`), an owned result
  (`made_replaced`), a result copied after a store into an aliased
  argument (`alias_result`), a borrowed result resolved after the
  callee's store (`borrowed_result`), a reused backing
  (`reused_construct`), the caller of a storing and replacing callee
  (`transfer_replace`, `call_fill_other`, `made_replace` and its callee
  `make_replace`), an owned result keeping its `Own[R]` argument's loan
  (`own_result_seed`, `own_member_seed`, `ident_kept`), a lent
  parameter's replacement reaching what an `Own[R]` parameter's object
  views (`change`), a record written through a place whose new record
  views the storage it replaces (`reseat_self`, `member_self`,
  `member_alias`), a copy handed to a constructor (`wrap_copy_replaced`)
  a callee's store under a member record (`fill_inner_replaced`), a
  write through a name reaching either of two objects (`member_either`,
  `copy_either`: each keeps what it stored) and a member filled from a
  call's owned result (`member_from_call`); `store_escape` for `fill`,
  `make_local`, `own_store_local`, `loop_store` (also `scope_end`)
  and a body-local buffer stored by a member replacement into the caller's
  or an `Own` parameter's object (`member_param_local`,
  `member_own_local`); `scope_end` for a temporary argument a base,
  inherited or member constructor stores (`derived_temp`, `kid_temp`,
  `shell_temp`). Sema is silent on all of them but `member_alias`, which
  it warns as a mutation while borrowed: `temporary_source`, the escapes
  and the constructor-leg temporaries are faces of
  `BUGS.md#record-view-field-escapes-local-buffer`, every `replacement`
  face one of `BUGS.md#record-view-member-source-replaced`. Covered with
  no conflict: `static_source`, `callee_reads` (the reads end before the
  replacement), `sibling_copy` (an unannotated `v = p.s` is an owned
  copy, so `v: StrView = p.s` is what the conflicting sections spell),
  `direct_write`, `fill_static`, `static_filled` (the caller joins the
  static transfer: `mir_borrows(t.s, a|static)`), `outer_read`,
  `Stamp.__init__` (a view member written in the constructor body),
  `consume_built`, `own_store_param`, `call_fill_control` (a callee that
  stores nothing), `R.set_buf` (an owned-field setter on a record holding
  a view: the caller's own stored loan is not pinned in the callee).
- **Refusals**, by reason. In the definition: an `Own[str]` or by-value
  owned parameter ("constructor view needs a lent parameter": it dies
  with the call); a base or member leg that moves or owns what it passes,
  or a literal a reference parameter binds ("base argument borrow not
  modeled", "member argument borrow not modeled"); a container field holding
  a view, an Optional, union or tuple member with a view inside
  (`StrView | None`), or a Span member ("record member holds a borrow":
  its loans would be keyed under an element or a payload); a member
  holding a loan beside a member whose loan class is not proved NO, a
  `bytearray`, native or protocol-typed field ("record member loan
  unknown beside a view member": `holds_loan` reads UNKNOWN as no loan,
  so that member could hold a loan no entry keys). Recursion is decided:
  a re-entered record adds no loan the entering frame does not join, so
  `Tree` with `s: StrView` and `kids: list[Tree]` holds a borrow and
  refuses as "record member holds a borrow". In the dependency pass: a
  whole write of view-holding record storage that names no stored loan
  ("record move of an object with no stored loan", "record storage filled
  with no stored loan"), and a write of a loan-holding value under a
  place no entry keys -- an element, a tuple member, a union payload, a
  wrapper member ("loan-holding write under an unmodeled place"; a
  container's elements are judged by its element types, since a Span holds
  a loan its elements need not). At a caller: constructing through a constructor
  whose body has effects, a view member write included ("constructor
  body effects", `stamped`). In the body: a named record at an `Own[R]`
  FUNCTION parameter ("unsupported record argument", `consume_named`); a
  record holding a view held by an Optional, union or tuple, or a hoisted
  optional backing ("wrapper holds a borrow"), or a container element
  ("container holds a borrow"). One predicate,
  `scalar_leaves.holds_loan`, decides "holds a borrow" for the
  definitions, the stub contract, the lowering and the validator.
- **Precision.** A parameter's held loan conflicts in the callee only
  (`late_read`): the caller passing the very object it later replaces is
  not told. A callee's transfer is a may-effect joined at the caller, so
  `t.reset(b)` keeps `t`'s old loan beside `b` (`mir_borrows(t.s, a|b)`)
  and replacing `a` afterwards still conflicts. A loan of the body's
  storage stored into a caller's object and later overwritten with an
  external one still escapes, and liveness is per object, not per member.
  A `BytesView` member is modeled, but THIR rejects a store into one
  (`BUGS.md#bytesview-field-store-rejects`), so no body reaches it; nor
  does a view member stored straight from another record's (`a.s = b.s`
  rejects at `field.result_type`,
  `BUGS.md#view-member-from-view-member-rejects`; `v: StrView = b.s; a.s =
  v` lowers). A whole member replaced through a pointer parameter
  (`member_param`: `o.inner = R(s, s)`; through `self` in a method,
  `Holder.swap`) reports a `replacement` C++ does
  not have: the weak update keeps the caller's seeded loan at
  `o.inner.s`, and that loan (any storage outside the body) may lie
  under the member the write replaces; a strong update of one external
  object would change the store rule above. A record written through a
  place is checked against the state it enters as well as the one it
  leaves, so reseating a record whose member viewed its own buffer
  (`r.s = r.buf`, then `r = R(mk(k), "lit")`) reports a replacement
  although the write overwrites that member too. What is left is in
  TODO.md (MIR entry, "View fields, left after slices 1-3", "View fields
  slice 4" and the units split out above them).
- **Measured** (`scripts/mir_coverage`, corpus sample, against the
  nested-records base): 13 bodies gained, none lost, no verdict changed on
  a body both runs hold. Over every case and the stdlib, the bodies
  touching a view-holding record that lower now: `str/strview_field`
  (`Wrapper.__init__`, `Wrapper.get`, `main`),
  `generics/generic_bound_sibling_ref` (`StrBox.__init__`, `StrBox.get`),
  `mir/views_as_places` `Holder.__init__`,
  `optional/view_at_owned_opt_decl` `Rec.__init__`,
  `generators/gen_yield_view_from_frame` (`Holder.__init__`,
  `Holder.__enter__`), `records/ctor_owned_str_field_sources`
  `Viewer.__init__`, `records/viewfam_field_at_owning_sinks`
  `Inner.__init__`, and the stdlib `JsonReader` scanners (`_skip_ws`,
  `_skip_str_no_ws`, `_skip_number`, `_parse_hex4`). Still refused there:
  `records/init_str_bytes_field_sources` (`Meta.__init__` and `main`,
  "unsupported metadata: materialize"), `JsonReader.__init__` and
  `JsonReader._unescape` (TODO.md has both). The stores, transfers,
  member records and owned results, against the slice-1 base (12711
  common sample bodies): lowered 2677 -> 2681, conflicts 30 -> 30,
  certified 73 -> 73, none lost, no verdict changed on a body both runs
  lower, the examples unchanged. Newly lowered:
  `records/viewfam_field_at_owning_sinks` (`Outer.__init__`,
  `Runner.__init__`, `sec_literals`) and
  `records/ctor_owned_str_field_sources` `Viewer.retarget`. Still
  refused there: `sec_insert` ("container holds a borrow"),
  `records/field_admission` `ViewSlots.set` ("missing field identity"),
  and, outside views, `sec_setitem` ("element write needs a stub
  contract"); `optional/view_at_owned_opt_decl` `Holder.__init__`
  ("member definition: constructor owned-leaf constant") lowers since
  unit 7. Records written
  through a place, copied operands, optional backings and constructor legs,
  against the slice-2 base over every case and the stdlib (29528 common
  bodies): 4 bodies changed their first blocker, all from a lifted refusal
  to covered or certified (certified 169 -> 171), none the other way; all
  four are the slice's own witnesses in `tests/cases/mir`. The census
  before the slice found the view items block about 10 corpus bodies; the
  units split out of the list (TODO.md) and `Box`/`Rc` and `Ptr` members
  are where the corpus is.

#### Optional record fields

A record field `p: P | None` whose payload `P` is a plain record holding no
loan is a place of its holder, with the payload stored inline (C++
`std::optional<P> p;`). Analysis only: generated C++ and diagnostics do not
change. Pinned by `tests/cases/mir/wrapper_fields` and
`tpyc/mir/test_optional_fields.py`.

- **Invariant.** A wrapper's members belong to the value at a place,
  never to the root slot alone: `nodes.place_shape(place, slots, records)`
  answers the value at a place (a root place: its slot's shape; a place
  ending in an Optional record field: an Optional whose payload record is
  stored inline). Every payload, element and presence operation resolves
  the shape of the place it operates on, and the wrapper rvalues
  (`MIRWrap`, `MIRCopy`, `MIRIsAlternative`) take a `MIRPlace`. A slot's payload is an inert leaf or a pointer
  at a record; an inline payload exists only at a field place. The refactor that introduced the resolver
  changed no verdict (census over the full test corpus and the stdlib:
  0 of 29713 bodies).
- **Admission.** `scalar_leaves.optional_record_field` admits an Optional
  (not pointer-represented, `readonly` removed) whose payload passes
  `plain_record_element` and holds no loan; `modeled_field` admits it (its
  class is OPTIONAL), so MIR models the field. THIR publishes its field identity as it
  does for every declared member (metadata; no render reads it), and
  `thir/validate.py` accepts a whole access (STORAGE form, the Optional
  type, or the payload type at an assign target inside a narrowed region)
  and a narrowed access (`narrowed_deref`, the payload type). `scalar_leaves.inline_member_type` is the record a member stores
  inline (the payload of such a field, else the member's own type).
- **Reads.** A narrowed read `h.p.x` is the place `h.<p>.alternative[1].x`; `is
  None` / `is not None` on a field is `MIRIsAlternative(place, (1,))`; `o = h.p`
  binds an Optional holder of the inline payload (C++
  `optional_to_ptr(h.p)`). An unproven read (under its `THIRNoneCheck`) refuses.
- **Writes.** `h.p = None`, `h.p = P(..)`, `h.p = other.p` and `h.p = o`
  are in-place replacement events through storage retention, so a live
  holder of the old payload conflicts -- through the same name, through a
  second parameter that may name the same holder (`reach.reaches` leaves
  distinct external origins to retention's oracle, which treats them as
  possibly one object), through a
  callee's published write, and through `self`. Replacement is never
  routed through payload_lifetime, which compares exact places and drops
  external referents. A whole-field write that copies a payload marks the
  copy `MIRCopy.may_raise`. A callee's write under the payload is
  published as a write of the whole field (summaries carry no payload
  selectors).
- **Presence kill rule** (`presence._kill_field_facts`). A presence fact on
  a field place dies, in the current state and on the outcomes stored on
  boolean temporaries (`if a.p is not None and empties(b)`), where the
  statement retargets the place (a write of it or of a prefix under its
  root: `presence._under`) or where storage the statement replaces reaches
  the wrapper's storage: `reach.reaches` over the referents of each written
  place of `reach.replacement_events` (the call's writes as this state sees
  the call, an owned sum's fill by the slot's selection) and of the fact's
  place, which the first dependency run gives (see "Pass order" below).
  Across distinct external origins presence's oracle decides
  (`reach.declared_containment`): a written referent naming a whole object
  may be any object (a `Base` parameter may name a `Sub`), else the written
  place's declared type may hold the member or not (`reach._may_hold`:
  scalar leaves, owned leaves and views hold none). The body's own storage
  is never a parameter's object, and a write strictly inside a payload
  never reaches the wrapper. Fail closed: a missing write fact, a place
  with no known or no resolved referent, a point with no dependency state
  kill. A read after a killed fact refuses ("payload access without
  current presence proof").
- **Pass order** (`prepare.prepare`, built once by `lower._lower` and
  carried by the lowered body; every pass takes the `MIRPrepared` it
  returns). Presence without field selections (no projected fact recorded
  or demanded), liveness (structural, it reads no presence fact), the
  first dependency run over that presence, presence with field selections
  killed over its referents, and dependencies again over the final
  presence, which every later pass reads (an absent argument the field
  facts prove, `h.p = None; o = h.p; pick(o)`, changes the call's effects
  and the caller's coverage). The kill's input is sound: it reads holder
  leaves only, the field facts only remove points and edges, so a
  non-empty holder set can only shrink, and an empty one kills. A first
  dependency run's refusal is the body's verdict (INCOMPLETE with that
  reason) and leaves the field selections unproven: `validate_function`
  refuses it, the dump (`prepare.validated`) does not. A MIR exception in
  either run propagates. Skipping initializations in the kill and in
  retention relies on the validator: owned parameters with body storage
  arrive initialized (`initialized_storage` starts with them), so a second
  initialization of one is rejected.
- **Constructors.** An Optional parameter is admitted by the predicate calls
  share (`definitions.optional_parameter`, reading the value of the one
  binding `call_contract.parameter_binding` derives): a `P | None` pointer
  at a readonly or mutable payload, or a scalar `int32 | None` by value.
  A constructor's Optional member-init is the Optional field write
  (`None`, the parameter copied, an engaged `P(..)` built in a temporary
  and wrapped), since unit 7; a caller binds an Optional argument through
  the one binder: none, `&name`, `optional_to_ptr(field)`, an Optional
  name, or an argument temporary.
- **Conflicts pinned.** `callee` (`o = h.p; clear(h); o.x`) and
  `H.via_self`: a `replacement` conflict each; `alias` (`o = a.p; b.p =
  None; o.x`) and `copy_both`, whose conflict needs two parameters naming
  one holder, publish it as an `alias_obligation` since an Optional holder
  slot has a summary ([Optional parameters](#optional-parameters)). Narrowings ended by a write through another
  path (`fresh_alias`, `fresh_call`, `fresh_condition`) refuse. Safe
  siblings stay covered (`alias_read_first`, `alias_other_field`,
  `keep_other_field`, `refill`).
- **Kept refusals**, by reason.
  - A narrowed payload bound to a local (`q = h.p`): "borrow of an
    Optional field's payload" -- the local alias rule borrows a field in
    place, never an Optional field's payload, which a replacement of the
    field ends.
  - A constructor argument built in place (`H(P(1), 2)`): "named argument
    needs complete temporary plan" (THIR's temporary planner).
  - A store under the payload (`h.p.x = 5`): "optional field payload write".
  - A payload copy into a field from a narrowed payload (`a.p = b.p` inside
    `if b.p is not None`): "optional field copy from a narrowed payload".
  - A whole-field store of a call's owned Optional result (`h.p =
    find(11)`): "record initializer type mismatch" (`store_found`).
  - Union, tuple, scalar-Optional and `str | None` fields; payloads that
    hold a loan or have a record field: "unsupported record fields".
- **Precision.** Retention's oracle stays coarse (a replacement of
  external storage may free a loan held anywhere external; making it
  precise would remove conflicts and `argument_alias` warnings, filed in
  TODO.md), while presence's is declared-type containment. A caller construct's Optional member re-derives
  `may_raise` from the payload type. The storage certificate does not model
  a payload holder (`o = a.p`): "borrow evidence: demanded operation is not
  a supported record borrow" (the verdict stays covered or conflict).
- **Measured** (`scripts/mir_coverage`, full test corpus and stdlib,
  against the merged base): 82 bodies newly lower,
  none lost, and no previously lowered body changes its verdict; certified
  171 -> 184. The newly lowered bodies are mostly constructors of records
  with an Optional record field and their callers (records 26, pointers
  15, optional 10). One newly lowered body conflicts
  (`pointers/escape_hoist_call_source` `while_nested_section`: a holder
  into a loop-local replaced before a read MIR cannot prove the inner loop
  skips; sema warns there too).

#### Optional parameters

A parameter `p: P | None` (payload a plain loan-free record) or `k: int32 |
None` (a scalar payload) has a KNOWN summary, and every call kind binds its
argument the same way. Analysis only: generated C++ is unchanged; which
bodies MIR covers, their conflict verdicts and the argument-alias warnings
change. Pinned by `tests/cases/mir/optional_arguments`.

```python
def peek(p: P | None) -> int32:     # summary KNOWN (was OPAQUE)
    return p.v if p is not None else -1
def relay(q: P, h: H) -> int32:     # covered (was "call needs finalized known summary")
    return peek(None) + peek(q) + peek(h.p) + h.peek(q)
def clear_read(p: P | None, h: H) -> int32:   # conflict: alias_obligation
    h.p = None
    return p.v if p is not None else 0
clear_read(h.p, h)   # warning: argument 'h.p' is what 'clear_read'
                     # modifies through parameter 'h' while it still uses 'p'; ...
```

- **Binding.** A record-payload Optional parameter is a POINTER at the
  caller's payload record (C++ `const P*` / `P*`), readonly iff the payload
  is; a scalar-payload Optional parameter is the callee's own VALUE. One
  binding serves constructors and calls (`call_contract.parameter_binding`,
  read by `definitions.optional_parameter`): its value is the Optional's
  shape, its payload member the record it borrows at the access THIR
  published; `parameter_contract_problem` checks the value's form, so the
  payload is never more mutable than declared.
- **Origin and summary paths.** An Optional parameter is the one-member
  case of the wrapper rule (next section): its payload record is the one
  object it lends, its own origin at the payload selector, and a summary
  path under it starts with that selector (`[payload]`, dumped
  `.alternative[1]`). A store under any other non-owned root keeps the
  summary opaque ("summary store under an unmodeled holder").
- **Callers.** Free-function, method and constructor calls bind the argument
  through the one `_Lowering.optional_operand`: null, `&name`, `optional_to_ptr(field)`, an argument
  temporary, an Optional holder, a narrowed name (a narrowed Optional name is
  a record source, `record_source`). A scalar payload is built into a
  temporary of the full expression. Passing a narrowed Optional or union
  name to a plain parameter is an ordinary scalar or record argument. A
  `None` argument is ABSENT (presence proves its only alternative is 0 at
  the call, a named `x = None` included): the callee's writes, stored loans,
  return origins and no-alias obligations through that parameter name no
  caller storage. Every call-effect consumer reads them through the one
  `MIRPresentCall` presence records for the call.
- **Kept refusals.** A ternary Optional argument ("unsupported optional
  argument"); a callee returning its payload as a borrowed record
  ("unsupported metadata: optional_read"); a record-payload temporary at a
  constructor's Optional parameter (`H(Q(4))`, "named argument crosses
  unproven evaluation order"; a scalar one, `U(1, 5)`, is covered) or a
  temporary in a body without THIR's temporary plan; Optional payload kinds not
  modeled (`str | None`, containers, `Own[P | None]`, BigInt `int | None`);
  payloads holding a loan ("wrapper holds a borrow"). TODO.md MIR entry,
  "Left by the Optional-parameters unit".

#### Wrapper parameters: one object per pointer member

Optional, union and tuple parameters follow one rule: a parameter lends
one object per pointer member of its value shape, and a summary path names
the member it goes through. Analysis only: generated C++ is unchanged.
Pinned by `tests/cases/mir/wrapper_summaries`.

```python
def write_uni(u: P | Q) -> None:          # summary KNOWN: writes p0.alternative[0].P::v
    if isinstance(u, P):
        u.v = 5
def write_tup(t: tuple[P, int32]) -> None:  # summary KNOWN: writes p0[0].P::v
    t[0].v = 7
def tup(h: H, t: tuple[Q, int32]) -> int32:  # conflict: alias_obligation
    h.q = Q(5)
    return t[0].v
tup(h, (h.q, 1))   # warning: argument 'h.q' is what 'tup' modifies through parameter 'h'
                   # while it still uses 't'; pass a copy bound to a local ('v = copy(h.q)') or ...
```

- **Summary-path grammar.** A path under a wrapper parameter starts with
  the member selector (`MIRUnionPayload(k)` -- an Optional's payload is
  `k = 1` --, `MIRTupleIndex(i)`), then the fields and at most one
  container projection of the record path grammar above. A selector alone
  names the whole object behind that member, as `()` names a record
  parameter's object. Paths and resolved referents carry no dereference:
  `shapes.object_path` (read through `call_contract.path_projections`,
  inverted by `summary_path`) is the one translation to a body place,
  inserting a dereference after the root and after each selector that
  reaches a pointer member -- never after a field hop.
- **Per-member objects.** `shapes.lent_members` (over the one member
  enumeration `wrapper_members`, which the dependency leaves also read)
  lists the pointer members; each is its own origin outside the body
  (`reach.parameter_objects`, keyed per member in `object_keys` and the
  storage oracle), so writes, stored loans, return origins and no-alias
  obligations name the member. `MIRParameterBinding.lent_members` carries
  each member's access (a `tuple[readonly[Cell], Cell]` lends one readonly
  and one mutable object); `binding.readonly` is "every lent member
  readonly". Two members of one tuple parameter are two objects lent at
  once (`shapes.distinct_lent_objects`): a conflict between them is a
  published obligation like one between two parameters; two alternatives
  of one sum never coexist, so a conflict between them stays the body's.
  `reach.parameter_object` and `MIRParameterBinding.object_selector` are
  the one answer to "which lent object does this place / path start in".
- **Obligations.** `retention.alias_obligation` publishes for a member
  origin, so a call whose wrapper argument's member overlaps what another
  parameter writes warns exactly where its scalar twin does. The warning
  spells the member's caller place: a tuple literal's element (`h.q`), a
  named tuple's element (`t[0]`), a union name (`u`; a sum payload has no
  Python token), two members of one tuple by their indices (`t[0]`,
  `t[1]`), and judges the remedy by the lent member's type (a tuple is a
  value type even when it lends a record: a copy, never "bind it to a
  local"); a tuple or union argument takes no `copy()` call in place, so
  the copy is bound to a local first (`v = copy(h.q)`), and a named tuple
  is rebuilt around it (`(v, t[1])`).
- **Callers.** A union argument goes through `union_source` against the
  parameter's value shape (`None`, a record source in place through
  `aggregate_source` -- a name, `self`, a member, a borrowed call result --,
  a union name), a tuple argument through `tuple_expr`, into the call
  temporary `optional_operand` shares (`argument_holder`). Presence drops an
  effect whose first selector names an alternative it excludes
  (`presence.present_call`): an absent Optional and a narrowed union alike.
- **Kept.** Wrapper fields and returns, temporary-bearing union lifts
  (`read(P(1))`), scalar union literal arguments, tuple call-result
  arguments, owned tuple temporaries, `Own[...]` and owned-leaf members:
  TODO.md "[MIR] FIRST", the unit-4 filed list.

#### Argument-alias obligations: the first MIR diagnostic

A call whose borrowed argument points into storage the callee writes through
another parameter is warned at the call (`BUGS.md#borrowed-arg-into-mutated-storage`):

```python
def bound(events: list[int], tag: int, value: int) -> int:
    events.append(tag)       # may reallocate the list `value` points into
    return value
bound(xs, 1, xs[0])          # warning: argument 'xs[0]' points into 'xs', which 'bound'
                             # modifies through parameter 'events' while it still uses
                             # 'value'; bind it to a local first ('v = xs[0]') or restructure
                             # so the call does not hand over both
walk(xs, xs)                 # warning: 'xs' is passed as both 'src' and 'dst' of 'walk', which
                             # modifies it through 'dst' while it still uses 'src'; pass a
                             # copy ('copy(xs)') or restructure ...
alias_external(ln, ln)       # warning: argument 'ln' contains 'ln.a', which 'alias_external'
                             # modifies through parameter 'right' while it still uses 'left'; ...
```

The phrase is the relation of the two places in the caller, decided where
the message is built (`mir_diagnostics._relation`): the argument's place
equal to the written object and to the other argument (`is passed as
both`), equal to the written object only (`is what`, `h.setname(h.name,
s)`), the written object a proper part of the argument (`contains`), or
the argument strictly under the written object (`points into`).

Generated C++ is unchanged; only the diagnostic is new. Pinned by
`tests/cases/mir/argument_alias`, `tpyc/mir/test_argument_alias.py` and
`tpyc/test_cli_mir_warnings.py`.

- **Obligations.** A retention conflict whose retained and replaced
  referents are both external, neither held nor static, with origins in two
  different parameter slots, exists only if a caller passes aliasing
  arguments. `retention.alias_obligation` (the one home of that rule) turns
  it into a `MIRAliasObligation(parameter, path, written)` that the body's
  KNOWN summary publishes (`summaries`, validated by `obligation_problem`:
  an obligation the summary cannot state makes it opaque). A conflict the
  caller's arguments can create but no obligation states -- a stored loan
  (`held`) or a global on either side -- makes the summary opaque
  ("summary retention: ..."), so its callers are uncovered, never falsely
  covered; a conflict inside one lent object is the body's own.
- **Discharge.** At each call to a summary with obligations, the argument for
  the retained parameter acts as a holder, the write through the other
  parameter is resolved on its argument, and overlap is tested with the
  existing `affects` rule; an overlap is an `argument_alias` conflict at the
  call (`classify_retention` sorts body replacements from call aliases). A
  caller passing two of its own parameters publishes the obligation again.
  A construction `R(...)` is such a call: the calls a body makes
  (`definitions.body_calls`, constructions and base initializers included)
  are the ones whose obligations are discharged, and the warning names the
  callee by its record (`'R'`), not by `__init__`. An `[elements]` step overlaps like any other step: element identity is not
  tracked, so a write to one element also flags a borrow of another (a
  constant-index false positive; see "Origins and write reach").
- **Normal compilation.** `Compiler.report_mir_warnings` runs MIR over the
  user modules' THIR after codegen, analyzing every body MIR lowers (since
  2026-10-27; before, only bodies calling a callee with obligations), and adds
  each `argument_alias` conflict and each definite lifetime hazard as a
  warning through the sema diagnostics stream (printed, replayed by the build cache,
  recorded in a case's `diag.txt`). A MIR exception drops only that body's
  warnings; the test harness runs it strictly, so an exception fails the case.
  The remedy is named by the argument's type: bind an `int` / `str` argument
  to a local first, pass a `copy()` of a list or record.
- **Body verdicts** (user decision 2026-10-10). A body whose cross-parameter
  conflict is published as an obligation keeps it as an `alias_obligation`
  conflict, a kind apart from `replacement`, so the census and the pins tell
  a precondition from a defect of the body; it never reads `covered`
  because of an obligation. Obligations are discharged only where MIR
  analyzes the caller -- generator, async, module-level,
  `Callable`-value, implicit-dunder and `-L` callers are unchecked.
  Relieving a body is a later unit: it needs the fact that every caller is
  known and checked (a private, non-escaping function).
- **Not checked** (the call stays silent; BUGS entry above): callers MIR does
  not lower, callees without a KNOWN summary, record-element arguments,
  native stub callees (a `std::vector` must accept an argument aliasing its
  own element), element writes. The census counts calls to summaries with
  obligations from bodies it does not analyze.
- **Cost.** The MIR step adds 0.006-0.34s per program on the corpus (largest
  share on MIR-heavy test cases, about half of the front end there; under
  1% on the largest cases). The warnings are the verdict pass's
  `argument_alias` conflicts: the harness reads them off the verdicts, and
  a compile without a verdict pass runs `analyze_body` only on bodies
  that call a callee with obligations.

#### One wrapper, one copy, one write fact

An Optional is a two-alternative sum, as a union is: its members are
`(None, payload)` (absent is alternative 0, as a union lays `None` out
first), so `MIRShape.payload` is alternative 1 and `alternatives`,
`scalar_wrapper` and the presence domain read both sums alike. The
operations on a value are one node each:

| Operation | Node |
|---|---|
| select an alternative | `MIRWrap(alternative, source \| None)` |
| test the selection | `MIRIsAlternative(source, alternatives)` (an Optional's presence is `(1,)`) |
| a sum's payload place | `MIRUnionPayload(alternative)` (an Optional's is `MIRUnionPayload(1)`; the dump prints `.alternative[1]`, annotations keep `o[payload]`) |
| copy a record, a sum or a tuple | `MIRCopy(source, may_raise)` |
| record a storage write | `MIRStorageWrite(mode, rebind_owner)` |

A sum's or a tuple's `MIRCopy` keeps the selection and converts member
by member by what the target member holds: a holder takes the source
member's loan, or lends an inline source member (`optional_to_ptr`); a
storage member takes a copyable copy whatever the source's access; a
scalar the same value. The lend is the copy's alone (`member_compatible`
is unchanged, so a construct or a call never lends raw storage).
`may_raise` is the copy of its storage members' buffers, or an element
access of the source.

`MIRStorageWrite` modes: `INITIALIZE` (no old value; once per lifetime of
the target slot's storage, the body or each entry of its region, read off
`MIRSlot.storage_duration`) and `IN_PLACE` (the value replaced in its
storage; every alias sees it). One classifier yields every storage event
of a statement with its kind (`reach.storage_events`: an assignment's
initialization, a wrapper constructed with its default (`CONSTRUCT`,
carrying the place it materializes: a scalar wrapper itself, an owned
sum's selected storage alternative, nothing for an owned sum built
empty), a replacement, a scalar wrapper's payload write, a call write, a
move). A whole write of an inline sum carries ONE per-alternative list of
its payloads, each marked by whether it owns storage, and one rule weighs
it against the old selection (`reach.sum_payload_ends`): a payload ends
only where the old selection may hold it; a storage payload ends whatever
is written (a record has identity, so a refill of the same alternative
replaces it, and a first fill of an empty sum replaces nothing); a scalar
payload only where the write may select another alternative. Replacement
reads its storage entries, payload_lifetime its scalar ones; a point no
feasible path reaches ends none. A field's Optional payload stays an
unconditional replacement (two holders may alias the record around it).

#### Owned sum slots

A C++ object held by value that is either empty or holds a value --
`std::optional<T>` -- is one MIR slot: an owned sum (`MIRShape.owned_sum`:
a sum held by value with an alternative that owns its storage, a record or
a container held inline), the two-alternative sum an Optional parameter or
field already is. Its one fact is presence's selection. Every lowered
producer builds one: a record local hoisted out of a branch or a loop exit
(`if flag: cell = Cell(1) else: return 0` -> `std::optional<Cell> cell;`),
a select's fresh arm and an argument temporary under a lazy operand (the
temporary plan's optional placements), a per-site reseat slot (`p =
make(n + 1)` -> `std::optional<P> __slot_N`), a hoisted pointer local
(`P* p = &*(__slot_1 = P(1))`) and a list reseat. Its lifecycle:

- built empty by `MIRStorageInit(slot, 0, None)` where C++ declares it:
  before the branch for a hoist, before the statement for a temporary-plan
  placement, at the body-level statement THIR anchors a reseat's rebind
  slot at (`THIRStmt.anchored_slots`, the `own_slot` of the rebind; a
  slot MIR cannot anchor refuses the body), at the body's top for a
  hoisted pointer local (its storage placement `BODY`);
- filled by `slot = wrap[1] temp` (`IN_PLACE`) from a record built in a
  temporary of the fill's full expression (`lower.site_fill`, through
  `aggregate_temporary`), the holder rebound within it by its shape: a
  record holder borrows `slot.alternative[1]`, an Optional holder takes
  `optional_to_ptr` of the slot (a `wrapper_copy`);
- ended with its region: the scope end ends the slot itself, which reaches
  every holder of its payload;
- a root by `MIRSlot.lifetime_root` (owned storage, or a local inline sum,
  a scalar one included), the one predicate scope roots and the storage
  evidence read; `inline_sum` (every alternative inline, never a
  pointer) answers the duration fact and the physical-initialization
  checks; `scalar_wrapper` only picks the payload event and THIR-form
  decisions.

A by-value member of an owned slot -- a sum's payload, an owned tuple
local's member -- keys its stored loans under the member's place
(`slot.alternative[1].s`, `t[0].s`; `dependencies.object_keys`). A wrap
is a one-member construct: its payload takes the loans its operand's
object stores (`dependencies.member_fill`, the member step of
`construct_fill`); a whole copy or move carries the source's entries
alternative by alternative where the source holds some; the root write
replaces every entry of the slot. The storage evidence asks for a
payload's stored loans only where the payload may be selected. A key is
live while its slot is, so a dead member of a live tuple keeps a false
conflict (filed). An Optional-of-record value outside such a slot keeps
its refusal (`lower.register`). The validator checks a root wrap as a
field write's: a defined, movable payload moved once from a temporary;
a root write of any inline sum, a scalar one included, needs its write
fact. Every scalar wrapper is an inline sum and a write into a payload
is refused, so no payload write reaches a pass without its fact: the
storage inventory and payload_lifetime assert it. Any other assignment without its write fact keeps the kind its
destination gives it, marked not `known`: an unknown replacement (storage
a borrow can point into) fails the replacement readers closed (presence's
kill, retention, the storage inventory), scope_lifetime refuses one on a
root it tracks, and the
storage evidence counts no materialization from it. The storage
inventory keeps every kind, scope_lifetime reads which roots a block
begins, the storage evidence which roots are materialized
(`reach.BEGINS`), payload_lifetime which writes replace a scalar
wrapper's payload, and retention and presence read the replacements
`reach.replacement_events` selects.
Payload ends take the writes to a scalar sum. An in-place write's rebinding holder repoints, so retention
skips its own dependency leaves only: a view member of the object it lends
still retains what the write replaces
(`BUGS.md#record-view-member-source-replaced`, `element_view_replaced`).

One construct: `MIRConstruct` builds a tuple and a container literal
alike, one operand per member of the target's shape
(`shapes.construct_members`: a tuple's elements; a literal's elements
repeat, so only the validator cycles them). A record is never built field
by field: it is its constructor's call (below). Its readers
key on the target, never on a node class: the validator's one member check
(`construct_ok` / `member_operand`: a holder member stores its operand's
loan, a storage member takes a moved temporary or a copy through a
copyable holder of its type, whatever aggregate holds it), the one
dependency rule (`dependencies.construct_fill`: member i takes operand i's
state -- a holder member the loan, a storage member the loans its operand's
object stores), the summary (a storage target, an owned tuple included,
moves its temporaries in as transfers; a target of holder and scalar
members is built from slots the dependency pass tracks) and testutil's
interpreter. A tuple's record member is built into a temporary of the
declaration's full expression (`literal_record`) and moved in, which is
what C++ does (`std::tuple<P, int32_t>{P(1), 2}`); every storage target
takes one INITIALIZE placement rule (`validate.scoped_initialization`:
scoped storage in its owning region or a full expression nested in it).

#### Constructors are ordinary bodies (unit 7)

A constructor is an ordinary body that builds and returns an owned record,
and `R(...)` is an ordinary call that reads that body's summary.

- **The record.** The body's result is body storage of the record type,
  begun at entry by a root storage begin (`MIRStorageInit` with no
  alternative) and returned at every exit (`return %result`, `Own[R]`). It
  is built where the caller receives it (`MIRSlot.destination`), so the
  `Own[R]` movability requirement does not apply. `self` is a holder bound
  to it before any statement and never rebound (`MIRSlot.bound_storage`);
  field writes go through it as in a method.
- **Member inits.** Each member-init entry is the field write it is,
  lowered by the CFG's field-write dispatch as an INITIALIZE of the field;
  values that need ordinary expression lowering lower like any statement's.
  Which fields the entry initializes is THIR's partition: a field left to
  the body is default-constructed by the C++ skeleton first, which no fact
  publishes, so such a constructor is refused ("incomplete constructor
  initialization"). THIR's list order must be the layout's ("constructor
  field order").
- **Base inits and inherited constructors.** A base initializer is a call of
  the base's constructor whose `Own[B]` result initializes exactly the
  fields B declares and inherits in the record (`(*self) = call B.__init__
  [initializes=B] [initialize]`, `MIRCall.initializes`). One helper,
  `nodes.initialized_places`, names those places, on the resolved call
  (`MIRPresentCall.initializes`, apart from `writes`, which may replace);
  the validator, the initialization analysis, the storage events (one
  INITIALIZE per storage-owning base field) and the dependency fill read
  it. Such a call writes projected places only: it kills no liveness and
  clears no other entry of the record. A record with no `__init__` of its
  own constructs through its base's constructor the same way, into a fresh
  slot. A base the source never calls is what C++ runs for it: when the
  base's constructor binds with zero arguments, C++ calls it, so it is that
  call (a constructor whose parameters all have defaults is then refused
  like any call omitting a default, "incomplete constructor arguments":
  MIR binds no default argument); otherwise C++ runs the empty default
  constructor, admitted only for a fieldless base, which it builds empty.
- **Initialization.** One place-keyed analysis (`validate._Initialization`)
  keeps each slot's storage and each field of a destination uninitialized,
  initialized or maybe. A read needs initialized; an INITIALIZE needs
  uninitialized on every path; a write under a field reads the field; a use
  of `self` other than through a field reads the record whole; any write
  of a field initialized on some path only is refused. A move, a call
  handed an argument's storage, and a sum or aggregate construction taking
  a storage-owning operand clear it (one operand moved twice is a read
  after move); a region exit ends the storage it owns. A base call
  initializing no field reads nothing. A field's state decides its write's mode and readonly
  check (`validate.field_write`): an initialization of a `readonly` field
  is allowed, a replacement is not, and only a replacement needs a movable
  record. Use after move through another holder is MIR's own MOVE storage
  event under retention, not sema's move rule
  (`BUGS.md#own-move-loop-carried-alias`): a moved named argument is
  admitted at an `Own[...]` parameter (the summary keeps the callee's
  stores into it private), and a holder of the moved storage live after the
  call is a retention conflict.
- **Callers.** A construction binds its arguments with the one binder
  (`lower.argument`) and applies the constructor's summary (`call_value`);
  its raise is the summary's. The workspace schedules constructors as
  callees (`mir_workspace.callable_identity`), function-shaped (no owner;
  a THIR-published callee is filed). `MIRDefinitions` keeps only record
  certificates and each record's callee.

Still separate, with its reason: `MIRUnionExtract` (TODO.md "[MIR] FIRST",
step (1)).

#### Origins and write reach

A place is a root plus a walk `shapes.step` takes (fields, `[elements]`,
`[structure]`, tuple members, a sum's payload); each question below is
answered once:

| Question | One answer |
|---|---|
| a summary path (write, return, transfer end, obligation) | any walk from the value the parameter binds (`call_contract.resolve_path`); a trailing region is terminal, its container checked by type (`shapes.walk`'s `region_end`) |
| may a published write end there | `endpoint_admitted`: the parameter lends, `store_problem` holds (a view never changes its container's shape), and the end is a replacement (`replacement_reach`) or a scalar leaf |
| may a published return end there | `return_endpoint_problem`, read by the summary and by each caller (`dependencies.call_return_problem`): no region, never at or under an Optional record field's payload, a view of a leaf or a place of exactly the result's type |
| does a write at this end replace storage | `shapes.replacement_reach` (storage, retention, line facts, the write endpoint) |
| what a call writes | `MIRPresentCall` per call point (`presence.present_call`): effects less what an argument proven absent reaches, and their places resolved once |
| what a parameter lends | `reach.origin`: the place (a Span's elements region; a wrapper parameter's pointer member at its selector, one object each) and the shape its paths step from |
| does replacing this storage reach that storage | `reach.reaches` on referents (retention, scope_lifetime, the presence field kill), across distinct external origins each pass's oracle beside the other in reach.py (`storage_exclusions`, `declared_containment`) |

So `bump_all(ps: list[P])` writing `p.x` in `for p in ps` publishes
`writes={param0[elements].x}` and its callers lower. Kept apart, with
their reasons: the parameter itself as a return spelling for an element
or view of a container (the caller projects it to the elements region);
the caller's "container result rooted in an elements region" (it depends
on the argument); the summary's value arms (they check the source, not
where it lands); `reach._origin` (which referents count as one
origin) and `seeded_loan` (a member storing a loan). For the
`argument_alias` warning an `[elements]` step is one step like any
other: element identity is not tracked, so `put(ps, ps[0].s)` with `put`
replacing `ps[0]` warns, as `put(ps, ps[1].s)` does (a constant-index
false positive of the kind field writes already have).

## Scope matrix and remaining increments

The M3 completion checklist lives in
[MIR_M3_COMPLETION_PLAN.md](MIR_M3_COMPLETION_PLAN.md); its open items are
scheduled under the B-steps above. Historical increment
matrices describe their own admission boundaries, not total remaining scope.
M3.12 separates physical wrapper initialization from source assignment, with
actual default selections and independent must-facts. M3.13 admits scalar
Optional/union if/while hoists through THIR
([wrapper storage plan](MIR_M3_WRAPPER_STORAGE_PLAN.md)).
M3.14/M3.15 extend physical initialization to optional-backed plain-record
hoists, separating record engagement and logical overwrite from wrapper
lifetime and source assignment ([record storage plan](MIR_M3_RECORD_STORAGE_PLAN.md)).
M3.16/M3.17 add flat inline-record tuple backing, normal lifetimes and bounded
constructor-literal producers. Borrowed siblings retain external dependencies;
tuple copying, hoists and general temporaries remain uncovered
([tuple storage plan](MIR_M3_TUPLE_STORAGE_PLAN.md)).
M3.18 admits immutable whole aliases and chains of those body-local constructor
tuples, resolving their operations to the original backing without a copy or
new lifetime ([tuple alias plan](MIR_M3_TUPLE_ALIAS_PLAN.md)). Tuple rebinding
and its existing source defects remain separate from that normalization.

M3.19 adds native container/iterator holders, structural and element summary
places, guarded element reads and captured dependencies. M3.20 connects
unit-step int32 range CFG, once-captured bounds, independent induction when
needed, target residence and normal transfers. M3.21 connects fixed borrowed
native container parameters/aliases with int32 or plain-record elements,
readonly access, retained aliases and scalar target hoists in free functions,
methods and constructor tails ([ordinary iteration plan](MIR_M3_ORDINARY_FOR_PLAN.md)).
These remain analysis-only; source acceptance and C++ emission are unchanged.

The following factored matrix covers the Cartesian product: a cell is M1 only
when all three axes say M1 and the operation is in the explicit subset above.
Otherwise it is a filed gap assigned to the earliest applicable later stage;
all prerequisite stages still apply. No omitted cell implies support.

| Axis | M1 covered by the tests above | Filed later scope |
|---|---|---|
| Position | ordinary free-function body | M2: methods, constructors, module statements, closures; M3: generator, async, comprehension, context-manager body, try/finally, error-return body, match arm |
| Shape | bool, int32; void return | M2: other scalars, tuple/singleton, Optional, union, str/bytes, reference types, Own, readonly, Ptr/Span, Box/Rc; M4: generic instantiations and callback forms |
| Slot | parameter, entry-declared local, return, expression temporary | M2: field, container element, global, capture, backing storage; M3: branch/loop-created bindings and frame-held slots |

These are work packages, not approved implementation designs or a claim that
each is one commit. Split them at coherent reviewed boundaries after M1.
The stage table is the capability map the
[breadth-first B-steps](#breadth-first-order) deliver, not their order; M5's
single authority switch is superseded by the B6 decision.

| Stage | Deliverable | Exit gate |
|---|---|---|
| M1 | Scalar CFG, typed local/temporary IDs, verifier and internal dump | Structural and evaluation-order tests pass; codegen/acceptance unchanged; explicit unsupported coverage |
| M2 | Semantic THIR metadata at existing decision sites; extended places and explicit storage operations | Qualified globals/callees/fields, structured captures and backing-storage identity survive lowering without parsing C++; alias/copy/move/rebind are carried facts, not inferred from form |
| M3 | Full required CFG/regions and liveness plus holder/loan propagation | Exceptional cleanup, destruction, suspension/resume, joins and every alias/aggregate/closure holder preserve dependency extents; emitted evaluation order is modeled soundly, not assumed from Python; unsupported coverage cannot authorize a proof |
| M4 | Finalized provenance/effect summaries and per-instantiation form obligations | Pending/unknown/known-empty remain distinct; named and imported forwarding, recursion, environment/global effects and selecting-call diagnostics compose; ownership summaries and native assignment traits resolved explicitly |
| M5 | Callable contract/admission consumer and authority transition | Re-run compatibility gate, safe/unsafe corpus and all six provenance requirements; approve language diagnostics and switch authority once, avoiding competing checkers |

Summary fixed-point orchestration stays in sema/workspace analysis as designed;
MIR consumes finalized summaries. M4 must address the existing import-component
propagation gap before claiming complete cross-module effects. M3/M4 can develop
in parallel at defined interfaces, but neither alone is enough for M5. A public
dump and optional comparison hook can land when body coverage warrants them.
MIR-backed C++ emission, SSA, exact-index disjointness and move optimization are
not prerequisites for M5. Existing checkers remain authoritative until then.

[M4.1/M4.2](MIR_CALL_SUMMARY_INTERFACE_PLAN.md) implement the first bounded
summary consumer. Local MIR evidence proves reader-only normal-returning
scalar callees; the workspace adapter schedules definitions after emitted THIR
collection and supplies finalized entries to MIR lowering. Direct forward and
imported calls with stable arguments are covered. This does not complete M4,
fix existing production summary gaps or admit named argument temporaries.

[M4.3/M4.4](MIR_CALL_EFFECTS_PLAN.md) extend the interface with typed scalar-field
may-writes, void calls and alias-aware effect substitution/forwarding. Existing
readonly named record temporaries are covered separately by the
[named-argument storage extension](MIR_NAMED_ARGUMENT_STORAGE_PLAN.md).
General invalidation, escapes and non-normal exits remain open.

## Pitfalls and risks

| Pitfall | M1 check / later obligation |
|---|---|
| silent-copy-vs-alias; copy-warning-at-wrong-site | No reference boundaries or new copy verdicts in M1; M2/M3 tests must mutate shared values across each boundary |
| tuple-equals-scalar | Tuple cells are explicitly M2, including singleton/mixed tuples; no scalar fallback for aggregates |
| same-construct-every-position | Factored matrix above and whole-body MIRNotCovered tests; no claim that free-function coverage covers sibling positions |
| conditional-operand-evaluates-in-place | Walrus effects on guarded CFG edges, once only; loop conditions re-evaluate |
| generic-equals-monomorphic-twin | Open generics are not covered; M4 compares instantiations against concrete twins |
| view-not-copy; hidden-allocation | No emitter changes; byte-identical existing C++; M2 carries view/ownership distinctions explicitly |
| const-source-const-loop-var | Iterators and reference loop slots deferred to M2/M3; no fabricated mutable scalar substitute |
| generated-cpp-readability | C++ emission untouched; internal MIR dump is deterministic and readable |
| no-cpp-in-diagnostics; no-internal-names-in-diagnostics | No source diagnostics added; internal dump may name IR kinds, future diagnostics use source names/locations |
| reject-valid-python-only-as-documented-divergence; no-warning-on-valid-code | Coverage failure changes neither source acceptance nor warnings |

Construction and structural validation should be linear in nodes/edges; definite
assignment uses a finite worklist over slot sets. It is not path enumeration.
Do not repeatedly recompile a whole fixture for individual block assertions.

Confidence for the M1 boundary: high after inspection and the native/CPython
probe. Broader analysis confidence remains limited by semantic metadata gaps,
ownership trust policy, cleanup/frame integration and summary completeness;
none is solved by introducing a CFG. No adjacent compiler fix is included.
The existing evaluation-order defect and policy decision remain tracked under
`BUGS.md#subexpression-right-to-left-eval` and TODO.md; broader MIR coverage must
either follow the chosen emission policy or conservatively model its possible
orders before using such graphs for safety proofs.
