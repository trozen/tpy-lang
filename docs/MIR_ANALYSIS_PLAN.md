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
and the coverage tool also render), so a MIR exception fails the case, and a case pins a
body's verdict with `# tpyc: mir(...)` / `mir_summary(...)` on its `def` line.
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
Direct readonly parameter extraction is included. The existing source path
copying a readonly record union into a local currently emits a mutable pointer
union and fails C++ compilation (`BUGS.md#readonly-record-union-local-copy`);
exclude it until fixed separately. Readonly
construction/copy directions are covered by internal IR tests. Scalar-union parameter
reseats also currently emit writes through a const reference
(`BUGS.md#scalar-union-parameter-reassign-const`) and remain excluded.
Both defects are recorded for separate fixes with user approval.
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

An existing producer defect also bounds coverage: auto copies from inferred or
whole-tuple readonly record parameters can lose readonly in their local payload
fact, including alias chains and method/constructor siblings. C++ auto copies
preserve constness; MIR rejects the inconsistent fact. This is tracked as
`BUGS.md#readonly-auto-tuple-copy-fact`. Mutable and correctly annotated mixed
payload copies remain covered. The defect's existing const table also feeds
rendering/admission, so changing it or adding parallel name-state is deferred.

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
- **B2, second half: views as places.** str/bytes/Span views carry a loan
  on their source, starting with parameter, local and field roots
  (container-element views need B3); owned-leaf record fields and
  constructor initializers; the first str conflicts (`view_return_escape`,
  `view_source_mutation`, `temporary_borrow`). str and bytes follow one
  rule. The classification still UNKNOWN comes here: `TupleType.
  is_value_type()` is always true; `Own` is not position-transparent (a
  value type becomes a value, a reference type `T&&`); `Ptr` is
  value-typed; `readonly` limits access, not lifetime.
- **B3: containers.** Holders with element places, element views and
  iterator loans. From here on each step builds the call-effect contracts it
  needs -- retention, invalidation, result origins, exceptional behavior --
  where `call_contract.py` today excludes globals and requires empty
  invalidation and retention.
- **Cleanup:** exceptional exits and destruction.
- **B4: generator and async frames.** Frame placement and lifecycle facts
  published by THIR (close, cancellation, cleanup), not only suspend/resume
  edges.
- **B5: remaining general call summaries** (pending callee facts, globals,
  callbacks) proceed alongside B1-B4 as each step needs them.
- **B6: advisory checker and authority transition.** Whether authority moves
  per diagnostic family or in one switch is decided here; this supersedes
  M5's single switch.

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
  `PAYLOAD_ALIAS`). Records are recognized by their TypeDef
  (`scalar_leaves.record_type`), never as "not a leaf"; a type with no
  TypeDef still reads as a record (failing closed there is filed in
  TODO.md, MIR entry). The validator re-checks every SCALAR slot and
  wrapper/container member at its representation, and the dependency pass's
  "no leaves" answer keys on that verified class, failing closed otherwise.
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
  leaf. A view result is refused (second half). The TypeDef declares it:
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
  `BORROWED` slot with an external referent; a local, temporary,
  by-value parameter (entry-initialized) or result is `OWNED` with
  a duration; every write to owned storage carries a `MIRRecordWrite`
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
  (`string_to_str`: at a view parameter the C++ passes a view of the
  `String`) refuses at every sink, owning sinks included, until the views
  half models it as a borrow (`THIRCoerce.conversion_refusal` names the
  reason); a promotion that is an argument-less method of one operand's
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
- **Call rule.** `MIRCallSummary.parameters` are `MIRParameterBinding(type,
  passing, readonly, borrowed_record)`; an unpublished passing fails
  closed, and the caller cross-checks `THIRCallableSignature.passings`
  against them. Both passings read `TpyType.param_passing` at a const
  verdict: a resolved signature's from `fi.const_borrow_params` (a stub's
  from its declaration, `_stub_param_consts`), a definition's
  `THIRParam.passing` from `_param_is_const`; the validator
  (`call_contract.summary_problem`, and the caller's check at lowering)
  enforces that the two agree. An owned-leaf
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
- **Deferred (not covered), second half or later.** StrView / BytesView
  locals and their sources, slices, view returns and their origins, view
  fields; owned-leaf record fields and constructor initializers (a callee's
  field write can invalidate a forwarded borrow, so they land with the
  conflict check); owned leaves in tuple / Optional / union members and
  container elements (B3); `bytearray`; owned-leaf global writes; `String`
  in-place methods beyond `+=`; f-strings; method calls on these types;
  `THIRMethodCall` stub contracts; the cannot-raise stub fact so
  `may_raise` can be False; a `String` bound as `str` (a passthrough
  conversion, a view at a parameter and a copy at an owning sink) refuses
  until the views half models the parameter case as a borrow; the
  conversion certificate keys freshness on the render's `wrap` where the
  declared `Coercion.builds_fresh_value` should decide it (the rows that
  would need the flag are read by sema too, so that is its own zero-churn
  unit).
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
