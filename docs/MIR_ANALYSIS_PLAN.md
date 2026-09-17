# Analysis-only MIR: implementation plan

## Decision and boundary

On 2026-09-17 the user approved bringing analysis-only MIR forward before the
coupled callable contract, following `CALLABLE_CONTRACT_FEASIBILITY.md`.
That approves the sequence, not the proposed admission restrictions or every
implementation detail below. **M1's implementation scope was then approved and
is implemented in `tpyc/mir/`.** It is an internal API used by tests; normal
compilation, source acceptance and generated C++ are unchanged.

The first increment builds and verifies a real control-flow graph from a small
THIR subset. It does not check callable lifetimes yet. Later increments must
supply all six provisions in `CALLABLE_PROVENANCE_REQUIREMENTS.md` before that
consumer can become authoritative. Shared mutation stays legal; the eventual
conflict rule concerns invalidation of borrowed storage, not exclusive access.

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
The builder itself takes a `THIRFunction` and an explicit body identity/kind;
it does not take an analyzer or codegen context.

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
| `THIRParam` construction in `thir/lower/functions.py`; `_param_is_const` and finalized `FunctionInfo.const_borrow_params` | Project the admitted parameter's borrowed-reference category and access capability; do not parse its emitted signature |
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

MIR uses `RECORD_STORAGE` slots and `MIRConstruct`, `MIRCopy`, `MIRMove` and
`MIRBorrow` rvalues. A record-place write through `MIRDeref` preserves identity;
an OWN replacement initializes a distinct slot before rebinding its holder.
The verifier checks complete layouts, initialization, access, copy/move
eligibility and single-execution storage sites, including a linear CFG cycle
check. `test_owned.py` uses actual emitted THIR; `test_owned_validate.py` checks
malformed MIR. The shared bounded interpreter observes both the returned scalar
and mutations of distinct/shared storage. No compiler unit test reads case files.

## Scope matrix and remaining increments

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
