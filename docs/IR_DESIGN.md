# Intermediate Representations -- Design

## Status

| Feature | Status |
|---------|--------|
| THIR node definitions (`tpyc/thir/nodes.py`) | Not started |
| AST + sema -> THIR lowering (`tpyc/thir/lower.py`) | Not started |
| `--dump-thir` debug output | Not started |
| THIR-backed codegen context | Not started |
| Codegen migration from analyzer/AST to THIR | Not started |
| MIR node definitions (`tpyc/mir/nodes.py`) | Not started |
| THIR -> MIR lowering (`tpyc/mir/lower.py`) | Not started |
| `--dump-mir` debug output | Not started |
| MIR liveness pass | Not started |
| MIR move/copy lowering and move optimization | Not started |
| MIR advisory loan checker (default mode) | Not started |
| MIR safe opt-in enforcement mode | Not started |
| MIR-backed codegen | Not started |
| Retirement of old sema/codegen ownership logic | Not started |

---

## Motivation

### The Problem

TPy's compiler currently uses a single representation: the AST from `parse/nodes.py`,
mutated in-place by sema with 50-70 optional annotation fields (`resolved_function_info`,
`inferred_type_args`, `bounds_safe`, `ptr_non_null`, etc.). Codegen reads the annotated
AST plus sema side tables (`expr_types`, `var_types`, fact dicts) via a direct reference
to the `SemanticAnalyzer`.

This works, but creates three concrete problems:

1. **Tight coupling.** Codegen cannot run without a live sema instance. Sema state is
   spread across AST node fields, `SemanticContext` dicts keyed by `id()`, and
   `BorrowTracker` string maps. There is no self-contained "this is what sema produced"
   artifact.

2. **Hard to debug.** There is no way to dump the fully-typed, fully-resolved program
   state between sema and codegen. Debugging requires mentally reconstructing what sema
   wrote into each node and side table.

3. **Borrow checking precision.** The current borrow checker operates on the AST with
   `freeze()`/`restore_from_frozen()` at branch points via `FlowFacts`, but merges
   borrow states conservatively at join points (union of borrows from both branches).
   This means a borrow active in either branch is assumed active in both -- so a move
   on one path can conflict with a borrow on a mutually exclusive path. The tracker
   also uses string-based storage keys with limited field-path support (`"self.items"`),
   and cannot split borrows by independent fields.
   A CFG-based IR is the standard solution for path-sensitive safety analysis.

4. **Agent boundary.** When LLM agents work on the compiler, the lack of clean phase
   boundaries makes it easy to accidentally couple new code to sema internals. A well-
   defined IR contract between phases prevents this class of errors.

### Why Two IRs

One IR is not enough because sema and borrow checking have different needs:

- **Type checking and overload resolution** work naturally on trees. Expressions have
  types, calls resolve to specific functions, generics are instantiated. A tree-shaped IR
  is the right fit.

- **Borrow checking, liveness, and move optimization** need path-sensitive analysis:
  "is this variable live on *every* path reaching this point?" This requires a CFG where
  each basic block has explicit predecessors and successors, and dataflow facts propagate
  along edges.

Trying to do both on the same representation forces either a tree that carries CFG
information (awkward) or a CFG that carries type-checking state (wasteful). Two IRs
let each phase use the right structure.

### What THIR/MIR unblocks (running ledger)

A growing list of concrete defects and duplication whose *clean* fix is gated on the
IR migration -- maintained so the migration's priority can be judged against accumulated
cost rather than asserted. Add entries here as they surface; cite the BUGS.md / TODO.md
source. Some entries are closeable pre-IR only as a *rejection* (loud diagnostic), not a
*fix*; those are the strongest signal, because the feature genuinely cannot be expressed
in the current model.

- **Borrow-form vs storage-form (Open Questions item 9 -- the largest cluster).** Tuple
  (and Optional/Union) C++ form is reconstructed per-site in codegen instead of being a
  type fact, so every new boundary shape needs another consumer-side dispatch patch:
  - *Tuple local with a durable reference member silently copies it at yield/return* --
    was **[MED, silent CPython divergence]** (TPy `5` vs CPython `99`). **FIXED pre-IR**
    by the tuple borrow-pointer unification (`unify-tuple-borrow-pointer-form`,
    `docs/TUPLE_BORROW_UNIFICATION_PLAN.md`): the bound local is a pointer-form tuple
    (`std::tuple<int, Box*>`), constructible and rebindable where a reference field is
    not, and it aliases correctly across suspensions -- disproving this item's earlier
    "THIR-gated" claim for the durable-share case. The cost was the consumer-side
    dispatch inventory now listed under Open Questions item 9, plus three adversarial
    audit waves closing provenance escapes -- the per-shape fact-propagation burden item
    11 is about.
  - Nested tuple where outer/inner forms disagree -- **[MED]** (BUGS.md).
  - Rvalue tuple-of-records into a ref/pointer-form slot -- **[MED/LOW]** (BUGS.md).
  - Generic `V | None` instantiated with `V = Ptr[T]` (double-pointer) (BUGS.md).
  - Bare-Optional yield missing the storage->pointer bridge (BUGS.md).
  Several smaller cases in this class *were* closed pre-IR by extending consumer-side
  predicates -- but each one touched another dispatch site, which is exactly the cost the
  IR fact removes. The same fact also dissolves the sema `Ref[T]` wrapper, which today
  co-exists with codegen's positional re-derivation as a second borrow-form oracle
  (Open Questions item 12).
- **Path-insensitive borrow checking (Motivation problem 3).** The AST borrow checker
  merges borrow states conservatively at join points (union over branches), so a move on
  one path conflicts with a borrow on a mutually-exclusive path -- false positives a
  CFG-based MIR resolves.
- **Hand-copied sema/codegen predicate mirrors.** Predicates duplicated across phases and
  kept in lockstep only by discipline: `directly_implements_dynamic` (sema mirror of
  codegen, now 4 call sites -- BUGS.md), the default-ctor predicate and the param-const
  verdict (TODO.md). A shared-IR contract removes the duplication class.
- **Eager per-shape local binding decisions (Open Questions item 11).** Non-value and
  pointer-repr-tuple locals pick their C++ shape eagerly at the binding site across ~7
  parallel mechanisms (ref binds, pointer-locals + rvalue slots, optional-locals,
  frame_slot fields, borrow-/storage-form tuple sets), each with its own
  init-deferral/rebind/alias rules -- the source of the optional brace-init corruption
  class, the tuple owning/alias rebind rejection (BUGS.md), and the per-shape
  provenance-fact propagation that three adversarial audit waves patched escape-by-escape.
  MIR's place/loan model with late representation selection + a mem2reg-style fold
  replaces all of it.
- **Generic str/bytes ABI perf split (Open Questions item 8).** **[perf, not
  correctness]** generic-`T`-over-`str` materializes `std::string` at each call site.
  Documented; low priority.
- **Joint generic inference: a pending-typed arg co-resolved by a sibling argument.**
  **[ergonomics, not correctness]** An untyped empty-container local (`heap = []` ->
  `PendingList[???]`) passed to a generic free function alongside an argument that fixes
  the type parameter is not resolved: for `heappush(heap, Entry(copy(src[i])))` against
  `heappush[X](heap: list[X], item: Own[X])`, `X = Entry[T]` is inferable from `item`, but
  `match_type_with_inference` is directional (param <- one arg at a time) with no shared
  unification variable tying `heap`'s pending element to `X`, so the local stays
  `PendingList[???]` and the call is rejected. Forward-from-usage deduction already covers
  the method-call shape (`xs.append(5)`) and the concrete expected-type shape (`f(x)` where
  `f` wants `Container[Int32]`) -- see `BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` Phase 3a --
  but the joint case (co-resolve a pending arg with a type param determined by a *sibling*
  arg, then write the result back onto the local) is the HM-style constraint-solving step
  that doc defers to "Phase 3+". Natural on MIR's unification-variable model; awkward to
  bolt onto the directional AST matcher. Workaround: annotate the local
  (`heap: list[Entry[T]] = []`). Surfaced reviewing the owned-storage-form inference fix.

---

## Prior Art

| Compiler | IRs | Notes |
|----------|-----|-------|
| Rust (rustc) | HIR -> MIR -> LLVM IR | MIR is where borrow checking, move analysis, and optimizations happen. Pre-monomorphization. |
| Swift (SIL) | AST -> raw SIL -> canonical SIL -> LLVM IR | SIL carries ownership and lifetime information. Two forms (raw/canonical) separate verification from optimization. |
| Go | AST -> SSA | Single IR, SSA-based. No borrow checking needed (GC). |
| C++ (Clang) | AST -> LLVM IR | No intermediate -- AST is heavily annotated, similar to TPy's current state. |

TPy's situation is closest to early Rust before MIR was introduced (2016). Rust had the
same problem: borrow checking on the tree-shaped HIR was imprecise and generated false
positives. MIR solved this.

---

## THIR Design

### Goal

Produce an **immutable, self-contained** representation of a fully-analyzed module that
codegen (and later MIR lowering) can consume without referencing the `SemanticAnalyzer`.

### What Changes

| Today | After THIR |
|-------|------------|
| AST nodes have 50-70 optional annotation fields, mostly `None` after parsing | THIR nodes have required fields -- all types resolved, all overloads bound |
| `expr_types[id(node)]` side table | `THIRExpr.result_type: TpyType` on the node |
| `var_types[id(node)]` side table | `THIRVarDecl.resolved_type: TpyType` on the node |
| `ptr_deref_facts[(line, key)]` dict | `THIRDeref.non_null: bool` on the node |
| `subscript_bounds_facts` dict | `THIRSubscript.bounds_safe: bool` on the node |
| `all_last_uses: set[int]` + `movable_locals: set[str]` | `THIRName.is_last_use: bool` + `THIRName.is_movable: bool` on the node |
| `resolved_function_info` optional field | `THIRCall.target: ResolvedFunction` required field |
| Per-function analyzer dicts (`function_scan_results`, `function_hoisted_vars`, `function_movable_locals`, `function_move_through_vars`, `function_global_decls`) | `THIRFunction.layout` and `THIRFunction.declared_globals` |
| Module options from sema/context (`default_int_type`, `default_int_for_literal`) | `THIRModule` required fields |
| View/literal registries (`str_vars`, `bytes_vars`, `list_literals`, `dict_literals`, `set_literals`) | explicit `view_info` / `literal_info` on the relevant THIR nodes |
| Codegen holds `self.ctx.analyzer` reference | Codegen receives `THIRModule`, no analyzer reference |

### THIR Node Hierarchy

The THIR mirrors the AST structure but with all analysis results materialized:

```
THIRModule
  functions: list[THIRFunction]
  records: list[THIRRecord]
  protocols: list[THIRProtocol]
  enums: list[THIREnum]
  globals: list[THIRGlobal]
  top_level: list[THIRStmt]
  default_int_type: TpyType
  default_int_for_literal: TpyType
  type_registry: TypeRegistry          # shared, immutable after sema

THIRFunction
  name: str
  params: list[THIRParam]
  return_type: TpyType
  body: list[THIRStmt]
  layout: THIRFunctionLayout
  declared_globals: frozenset[str]
  mutated_params: frozenset[str]       # from Phase 2 propagation
  is_readonly: bool
  return_borrows_from: frozenset[int]  # param indices
  is_generic: bool
  type_params: list[TypeParam]
  overload_group: str | None
  generator: THIRGeneratorInfo | None

THIRFunctionLayout
  hoisted_locals: frozenset[str]
  movable_locals: frozenset[str]
  move_through_locals: frozenset[str]
  pointer_locals: frozenset[str]
  ref_locals: frozenset[str]
  reassigned_locals: frozenset[str]    # affects C++ declaration style / slot handling

THIRGeneratorInfo
  yield_type: TpyType
  states: list[THIRGeneratorState]
  frame_fields: list[THIRSyntheticField]
  strategy: GeneratorStrategy          # current codegen strategy, if any

GeneratorStrategy
  = current backend-defined enum matching generator lowering variants

THIRGeneratorState
  state_id: int
  resume_label: str

THIRSyntheticField
  name: str
  type: TpyType

THIRParam
  name: str
  type: TpyType                        # fully resolved (Own[T], readonly[T], etc.);
                                       # no Ref[T] -- dissolved into form facts
                                       # (Open Questions item 12)
  default: THIRExpr | None
  is_mutated: bool                     # from mutation analysis
```

#### Expressions

```
THIRExpr (base)
  result_type: TpyType                 # always present
  loc: SourceLocation | None

THIRName
  name: str
  result_type: TpyType
  is_last_use: bool                    # from liveness analysis
  is_movable: bool                     # in movable_locals

THIRCall
  target: ResolvedFunction             # fully resolved -- function, overload index, etc.
  args: list[THIRExpr]
  type_args: tuple[TpyType, ...]       # instantiated generics (empty if non-generic)
  result_type: TpyType

THIRMethodCall
  receiver: THIRExpr
  method: ResolvedFunction
  args: list[THIRExpr]
  type_args: tuple[TpyType, ...]
  deref_depth: int                     # Ptr auto-deref count
  result_type: TpyType

THIRFieldAccess
  receiver: THIRExpr
  field: str
  deref_depth: int
  non_null: bool                       # proven non-null at this deref
  result_type: TpyType

THIRSubscript
  container: THIRExpr
  index: THIRExpr
  bounds_safe: bool                    # proven in-bounds
  result_type: TpyType

THIRBinOp
  left: THIRExpr
  op: BinOpKind
  right: THIRExpr
  resolved: ResolvedBinop | None       # operator overload, if any
  divisor_non_zero: bool
  result_type: TpyType

THIRNamedExpr                            # walrus operator (:=)
  name: str
  value: THIRExpr
  result_type: TpyType

THIRCoerce
  expr: THIRExpr
  from_type: TpyType
  to_type: TpyType
  kind: CoercionKind                   # widening, own-strip, optional-wrap,
                                       # runtime-bigint, etc.

THIRLiteral
  value: int | float | str | bool | bytes | None
  result_type: TpyType

THIRListLiteral
  elements: list[THIRExpr]
  element_type: TpyType                # resolved element type
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRDictLiteral
  items: list[(THIRExpr, THIRExpr)]
  key_type: TpyType
  value_type: TpyType
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRSetLiteral
  elements: list[THIRExpr]
  element_type: TpyType
  literal_info: THIRLiteralInfo | None
  result_type: TpyType

THIRTupleLiteral
  elements: list[THIRExpr]
  result_type: TpyType

THIRTupleUnpack
  targets: list[THIRExpr]
  value: THIRExpr
  result_type: TpyType

THIRComprehension
  kind: AggregateKind
  element: THIRExpr
  clauses: list[THIRComprehensionClause]
  result_type: TpyType

THIRGeneratorExpr
  element: THIRExpr
  clauses: list[THIRComprehensionClause]
  result_type: TpyType

THIRComprehensionClause
  = For(target: THIRExpr, iterable: THIRExpr)
  | If(condition: THIRExpr)

THIRLiteralInfo
  needs_stable_storage: bool

THIRViewInfo
  source_kind: str                     # str / bytes / span / ptr / field / element
  source_expr: THIRExpr

# ... (array, f-string, lambda, etc.)
```

#### Statements

```
THIRVarDecl
  name: str
  resolved_type: TpyType              # always resolved
  init: THIRExpr | None
  is_hoisted: bool                     # escapes inner scope
  is_pointer_local: bool               # T* slot (non-value type local)
  view_info: THIRViewInfo | None
  narrowing_facts: dict[str, TpyType]  # from isinstance/assert on this decl

THIRAssign
  target: THIRExpr                     # name, field, subscript
  value: THIRExpr
  view_info: THIRViewInfo | None

THIRAugAssign
  target: THIRExpr                     # name, field, subscript
  op: BinOpKind                        # Add, Sub, etc.
  value: THIRExpr
  resolved_inplace: ResolvedFunction | None  # __iadd__ etc. overload

THIRDelItem
  target: THIRExpr                     # subscript expression (del obj[key])

THIRForEach
  var: str
  elem_type: TpyType
  iterable: THIRExpr
  body: list[THIRStmt]
  orelse: list[THIRStmt]               # for/else body (runs if no break)
  is_consuming: bool                   # consuming iteration selected
  is_native: bool                      # NativeIterable range-for

THIRWhile
  condition: THIRExpr
  body: list[THIRStmt]
  orelse: list[THIRStmt]               # while/else body
  narrowing_facts: dict[str, TpyType]  # condition narrowing in body

THIRIf
  condition: THIRExpr
  then_body: list[THIRStmt]
  else_body: list[THIRStmt]
  then_narrowing: dict[str, TpyType]   # type narrowing in then-branch
  else_narrowing: dict[str, TpyType]

THIRAssert
  condition: THIRExpr
  message: THIRExpr | None
  narrowing_facts: dict[str, TpyType]  # narrowing after assert passes

THIRMatch
  subject: THIRExpr
  arms: list[THIRMatchArm]

THIRMatchArm
  pattern: THIRPattern
  guard: THIRExpr | None
  body: list[THIRStmt]
  narrowing_facts: dict[str, TpyType]  # type facts for this arm

THIRReturn
  value: THIRExpr | None
  is_dangling: bool                    # if True, sema already reported error

THIRYield
  value: THIRExpr | None
  state_id: int                        # generator state machine ID

THIRTryExcept                          # @error_return(E) zero-cost error handling
  kind: TryKind                        # ErrorReturn or Throw
  error_local: str | None
  body: list[THIRStmt]
  handlers: list[THIRExceptHandler]

TryKind
  = ErrorReturn
  | Throw

THIRWith
  context: THIRExpr
  var: str | None
  body: list[THIRStmt]
```

### Lowering Pass: AST + Sema -> THIR

A new pass (`tpyc/thir/lower.py`) walks the annotated AST and sema side tables,
producing THIR nodes:

```python
def lower_module(ast: TpyModule, analyzer: SemanticAnalyzer) -> THIRModule:
    """Convert annotated AST + sema state into a self-contained THIR."""
    ...
```

This is where all `id()`-keyed lookups, optional field reads, and side table accesses
are resolved into concrete THIR fields. After lowering, the analyzer can be discarded.

Two requirements are important here:

1. **Implicit coercions must be materialized.** Every sema-selected conversion becomes
   an explicit `THIRCoerce` at the exact site where it applies: call arguments,
   assignments, returns, operator operands, literal elements, default arguments,
   `Own` stripping, optional wrapping, enum-from-value, `str -> StrView`,
   `bytes -> BytesView`, and the other coercion families currently scattered across
   sema. THIR lowering must not rely on codegen or MIR lowering to rediscover them.

2. **THIR must be codegen-complete.** If current codegen needs per-function layout
   facts, view provenance, literal lowering metadata, module integer defaults,
   generator frame shape, or declared globals, THIR must carry an explicit equivalent.
   The shape may improve over today's analyzer dicts, but the analyzer dependency must
   end after THIR lowering.

### Debugging: `--dump-thir`

A human-readable text format for inspecting the THIR:

```
fn main() -> Void:
  %items: list[Int32] = list_literal([1, 2, 3])    # elem_type=Int32
  %total: Int32 = int_literal(0)
  for %x: Int32 in %items [consuming=false, native=false]:
    %total = binop(%total, Add, %x)                 # resolved=Int32.__add__
  call print(%total)                                 # target=builtins.print
```

This makes the resolved types, overloads, and optimization facts visible at a glance.

## Rollout Plan

### Migration Strategy

The migration should be incremental. The compiler currently has three concerns tangled
together:

- sema as the source of truth for typed program facts
- borrow/move analysis spread across sema and codegen
- codegen reading directly from analyzer internals

Those should be separated in phases. The key sequencing principle:

- **switch codegen to THIR before switching codegen to MIR**

THIR is structurally close to the current codegen input, so it is the right first
boundary. MIR should first become the analysis source of truth, and only later the
emission source of truth.

#### Migration Principles

1. **Preserve behavior first.** Early THIR and MIR work should not intentionally change
   generated code or diagnostics.
2. **Make phase boundaries explicit before changing semantics.** First remove analyzer
   coupling, then move borrow/move logic into MIR, then strengthen enforcement.
3. **Advisory first, safe mode later.** The MIR loan checker must initially preserve the
   current migration-friendly warning behavior. Safe opt-in enforcement is layered on
   after the analysis is stable.
4. **Keep explicit low-level tools.** `Ptr[T]` remains available in both default and safe
   mode; only pointer arithmetic / unchecked pointer fabrication stay in `tpy.unsafe`.
5. **Run old and new analyses in parallel during transition.** MIR diagnostics should be
   compared against existing sema behavior before MIR becomes authoritative.

#### Recommended Rollout

Before any codegen switch, the IR must first be complete enough to replace the current
analyzer coupling:

- **Before THIR-backed codegen**: THIR must cover per-function layout/scan facts,
  module options, explicit coercions, view/literal metadata, generator frame metadata,
  and declared globals.
- **Before MIR-backed codegen**: MIR must preserve narrowing, structured region tags
  for reconstructable control flow, and both return-tier and throw-tier error handling.

1. **Define THIR nodes** in `tpyc/thir/nodes.py`
2. **Implement `lower_module()`** in `tpyc/thir/lower.py`
3. **Add `--dump-thir`** to CLI
4. **Create a `THIRCodeGenContext`** that reads from THIR instead of analyzer
5. **Migrate codegen modules one at a time** (expressions, statements, functions, records)
6. **Remove analyzer references from codegen**
7. **Define MIR nodes** in `tpyc/mir/nodes.py`
8. **Lower THIR -> MIR** in `tpyc/mir/lower.py`
9. **Add `--dump-mir`**
10. **Implement MIR liveness + move/copy passes**
11. **Implement MIR advisory loan checker**
12. **Run MIR checker in parallel with existing sema borrow/move logic**
13. **Make MIR authoritative for ownership/borrow diagnostics**
14. **Add safe opt-in mode** on top of the same MIR analysis
15. **Switch codegen from THIR to MIR** once MIR carries enough information for readable,
    stable emission
16. **Retire old sema/codegen ownership logic**

The distinct-types-via-inheritance fix for the str/bytes generic param ABI
(Open Questions item 8) is **independent of this rollout** -- it operates on the
runtime type layer and the C++-template-keyed paths and does not require THIR/MIR
to land first. It can be scheduled separately whenever the team is ready.

#### Why THIR-Backed Codegen Comes First

Jumping directly from "analyzer-backed codegen" to "MIR-backed codegen" would mix four
independent risks:

- new IR design bugs
- new lowering bugs
- new borrow/move analysis bugs
- codegen porting bugs

Switching to THIR first isolates the representation migration from the ownership-model
migration. MIR can then mature as an analysis artifact before it becomes the executable
source.

#### Behavior Expectations By Stage

- **THIR stages**: no intentional behavior change; output should stay identical
- **Early MIR stages**: analysis/debug only; codegen still reads THIR
- **MIR advisory stages**: diagnostics may be compared or duplicated, but default
  severity stays warning-level for migration-friendliness
- **Safe mode stages**: selected MIR violations become errors only under explicit opt-in
- **MIR-backed codegen**: ownership and control-flow decisions now come from MIR, not
  sema/codegen heuristics

Run the full test suite at each stage. THIR migration should be behavior-preserving;
later MIR stages may intentionally alter diagnostics or move/copy decisions, but only
when the corresponding phase is made authoritative.

---

## MIR Design

### Goal

A **CFG-based IR** with explicit control flow, typed places, and explicit move/borrow
operations. Enables path-sensitive borrow checking, precise liveness, and composable
optimization passes.

### Design Principles

- **Not SSA.** Variables are mutable places, like Rust's MIR. SSA would add phi-node
  complexity without proportional benefit given TPy's ownership model.
- **Pre-monomorphization.** Generic functions remain generic in MIR. C++ templates
  handle instantiation. This keeps the generated C++ readable and interoperable.
- **RAII for drops.** No explicit `Drop` instructions for normal scope exits -- C++
  destructors handle cleanup. `del` statements lower to explicit `StorageDead`.
- **Preserves source structure.** MIR is lowered from THIR but retains enough
  information (variable names, source locations, type annotations) for readable
  C++ emission.

### Core Concepts

#### Basic Blocks

```
BasicBlock
  id: BlockId
  statements: list[MIRStmt]
  terminator: Terminator               # goto, branch, return, panic, switch
```

A function is a list of basic blocks. The first block is the entry point. Control
flow is explicit via terminators:

```
Terminator
  = Goto(target: BlockId)
  | Branch(cond: MIROperand, then_: BlockId, else_: BlockId)
  | Return(value: MIROperand | None)
  | Panic(message: str)
  | Switch(operand: MIROperand, arms: list[(Pattern, BlockId)], default: BlockId)
  | Yield(value: MIROperand, resume: BlockId)     # generator yield point
  | Invoke(call: MIRRvalue, ok: BlockId, err: BlockId, err_local: str | None)
                                               # return-tier @error_return handling
  | Unreachable
```

`Yield` suspends the generator, returning a value to the caller. On resume, execution
continues at the `resume` block. This supports the existing state-machine codegen for
generator functions (`gen_generators.py`).

`Invoke` is used for return-tier `@error_return(E)` calls: if the callee returns an
error via `std::expected`, control flows to `err`; otherwise to `ok`. `err_local`
captures the `__err_opt_N`-style temporary when the surrounding `except` block needs
to read the error payload.

Throw-tier `try`/`except` still needs explicit region metadata in MIR. The lowering
must retain enough structured information to represent nested `try` regions and
exception handlers even after CFG flattening. The exact encoding can be block metadata
or explicit handler tables, but MIR-backed codegen cannot assume "return-tier only".

#### Places

A `Place` identifies a logical storage location. This replaces the current string-based
storage keys in `BorrowTracker`.

Important: places model ownership-relevant storage, not literal C++ object layout. For
example, `list[T]` is backed by `std::vector<T>`, so the element storage is not inline in
the vector object itself. MIR should still model:

- the container object
- the container structure (operations like `append`, `insert`, `del` may replace or shift
  the owned backing storage)
- the element storage region borrowed by `items[i]`, `Span[T]`, iterators, etc.

This lets the borrow checker express "element/view borrow of `items`" without caring
whether the runtime representation is inline storage, heap storage, or a view.

The minimal place set should therefore include both direct places and summarized storage
regions:

```
Place
  = Local(name: str)                         # local variable / local owner slot
  | Global(name: str)                        # module/global storage
  | Capture(name: str)                       # captured outer-scope variable
  | Field(base: Place, field: str)           # record field
  | Index(base: Place, index: MIROperand)    # precise container subscript
  | Struct(base: Place)                      # container structural identity
  | Elements(base: Place)                    # container element storage region
  | Deref(base: Place)                       # pointer dereference

# Examples:
# x           -> Local("x")
# G           -> Global("G")
# x from outer -> Capture("x")
# x.items     -> Field(Local("x"), "items")
# x.items[i]  -> Index(Field(Local("x"), "items"), Local("i"))
# items[*]    -> Elements(Local("items"))
# append(items, v) mutates Struct(Local("items"))
# *ptr        -> Deref(Local("ptr"))
```

Places give the borrow checker precise knowledge of what is accessed. `Field(x, "a")`
and `Field(x, "b")` are distinct -- borrowing one does not conflict with mutating
the other.

`Struct(base)` and `Elements(base)` are intentionally coarser than exact indices. They
match the current TPy safety needs well:

- `items[i]` can borrow from `Elements(items)`
- `Span(items)` / `items[a:b]` borrow from `Elements(items)`
- `append`, `insert`, `del`, slice assignment mutate `Struct(items)` and may invalidate
  loans on `Elements(items)`

This is a good first step even if the compiler later grows exact per-element reasoning.

#### Statements

```
MIRStmt
  = Assign(place: Place, rvalue: MIRRvalue)
  | StorageLive(local: str, type: TpyType, kind: LocalKind)
  | StorageDead(local: str)                   # explicit early destruction (del x)
  | Narrow(local: str, narrowed_type: TpyType, source: NarrowSource)
  | Validate(kind: ValidateKind, place: Place)  # borrow check assertion

LocalKind
  = Value                    # T -- value type, stored directly
  | Pointer                  # T* -- pointer-local (non-value type, stack-allocated slot)
  | Ref                      # T& -- reference to another local (alias)

NarrowSource
  = IsInstance
  | Assert
  | MatchArm
  | NonNull
  | PatternGuard

ValidateKind
  = ActiveLoanConflict
  | UseAfterMove
  | StructuralMutationDuringLoan
  | DanglingBorrowReturn
  | InvalidPtrProvenance
```

`LocalKind` reflects TPy's pointer-variable model (see `OWNERSHIP_DESIGN.md`):
non-value-type locals are `T*` pointing to stack-allocated storage, while value-type
locals are plain `T`. This distinction affects codegen (slot allocation) and borrow
checking (pointer-locals create implicit borrows on their backing storage).

`StorageDead` is emitted for explicit `del x` statements (early variable destruction).
Normal scope exits rely on C++ RAII. Note: `del obj[key]` (container deletion) is a
`Call` to `__delitem__`, not `StorageDead`.

#### Rvalues

```
MIRRvalue
  = Use(operand: MIROperand)                        # plain read
  | Move(operand: MIROperand)                        # move (source dead after)
  | Copy(operand: MIROperand)                        # explicit copy
  | Borrow(place: Place,
           mode: BorrowMode,
           provenance: BorrowKind)                   # create reference
  | Call(target: ResolvedFunction,
         args: list[MIROperand],
         type_args: tuple[TpyType, ...])
  | BinOp(left: MIROperand, op: BinOpKind, right: MIROperand)
  | UnaryOp(op: UnaryOpKind, operand: MIROperand)
  | Literal(value: int | float | str | bool | None)
  | Construct(type: TpyType, fields: list[MIROperand])
  | Aggregate(kind: AggregateKind, elements: list[MIROperand])
  | Coerce(operand: MIROperand, from_type: TpyType, to_type: TpyType)
```

The critical distinction is `Move` vs `Copy` vs `Use`:
- `Use` reads without ownership transfer (value types, references)
- `Move` transfers ownership -- the source place is dead after
- `Copy` creates an independent copy of a non-value type

In the current compiler, this decision is made at codegen time via `_maybe_move()`.
In MIR, it is an explicit instruction decided by the move optimization pass.

Conceptually, `Move` is an ownership-transfer request on a place, not "the variable's
type changed to `Own[T]`". A local binding keeps its base type `T`; MIR decides whether
a particular use site becomes `Use(x)`, `Copy(x)`, or `Move(x)` based on liveness,
uniqueness, and active loans on the underlying place.

#### Borrows vs Loans

A useful distinction:

- **borrow**: the source-language semantic relation ("this value refers to someone
  else's storage")
- **loan**: the MIR borrow checker's active tracked record of that borrow over a place

Example: `y = x` for a non-value type creates a borrow of `x`'s place. The checker then
tracks an active loan on that place while `y` is live. A later `Move(x)` conflicts with
that active loan unless analysis proves `y` is dead.

#### Derived Lifetimes and Provenance

TPy should not expose Rust-style explicit lifetime parameters in ordinary source code.
Instead, lifetimes are derived from MIR loan liveness and carried internally as:

- the place being borrowed
- the CFG region where the loan is live
- the provenance of any derived view / pointer / borrowed return

Conceptually:

```text
LoanInfo
  id: LoanId
  place: Place
  mode: BorrowMode
  kind: BorrowKind
  origin: StmtId | ExprId
  holder: LocalName | TempId | ReturnValue | FieldSink
  live_blocks: set[BlockId]
  provenance: Provenance
```

Where provenance captures where a non-owning value came from:

```text
Provenance
  = FromPlace(place: Place)
  | FromParam(index: int)
  | FromGlobal(name: str)
  | FromCapture(name: str)
  | FromUnknown
  | Join(sources: list[Provenance])
```

Examples:

- `span = items[a:b]` -> provenance from `Elements(Local("items"))`
- `p = ptr(x)` -> provenance from `Local("x")`
- `return self.field` -> provenance from `Field(Local("self"), "field")`
- borrowed value returned from a wrapper -> provenance joined from the source params

This is the internal lifetime model for safe-mode checks. A move, mutation, return, or
escape is legal only if no conflicting live loan reaches that program point and the
provenance proves the source outlives the use.

#### Borrow Kinds and Modes

```
BorrowMode
  = Shared
  | Mutable

BorrowKind
  = Alias                   # whole-container alias (safe through mutations)
  | Field                   # field-level reference
  | Element                 # reference to container element
  | Iterator                # for-loop iterator over container
  | Pointer                 # Ptr[T]
  | View                    # StrView / BytesView / Span-like view
```

These correspond to the existing `BorrowKind` enum in `sema/context.py` (`ALIAS`,
`FIELD`, `ITER`, `ELEMENT`, `PTR`). The key semantic distinction: `Alias` borrows
are safe through container mutations (whole-object reference, not invalidated by
reallocation), while `Element` and `Iterator` borrows are invalidated by structural
mutations (append, insert, del). `Field` borrows are invalidated when the parent
object is reassigned but not by sibling field mutations.

In the current compiler, borrows are side-state in `BorrowTracker`. In MIR they
become explicit `Borrow` instructions, making conflicts visible in the IR. `BorrowMode`
captures whether the use requires shared or mutable access; `BorrowKind` captures where
the borrow came from and what invalidates it.

`Pointer` deserves special treatment: `Ptr[T]` is not "arbitrary raw pointer" in the
language design. It is primarily an explicit nullable reference form. Pointer arithmetic
and unchecked pointer manipulation remain in `tpy.unsafe`; plain `Ptr[T]` operations can
still participate in normal provenance / lifetime analysis.

#### Function Lifetime / Effect Contracts

For ordinary TPy functions, many facts can be inferred and materialized into THIR / MIR:

- `return_borrows_from = {0, ...}`
- `mutated_params = {...}`
- structural invalidation facts for container-like methods
- whether a returned `Ptr[T]` / `Span[T]` / `StrView` is derived from an input place

For native functions implemented in C++, these contracts should usually be explicit,
because the compiler cannot reliably infer them from the definition body. The IR design
therefore needs room for native summaries such as:

- `return_borrows_from`
- `returns_ptr_to`
- `mutates`
- `may_invalidate`
- `readonly`
- `opaque_effects`

These contracts are especially important for core-library functions that construct or
return views (`Span`, `StrView`, `BytesView`), explicit nullable references (`Ptr[T]`),
or iterator/pointer-like adapters.

Absent an explicit contract, native code should be treated conservatively:

- returned provenance may be `FromUnknown`
- mutation / invalidation may be assumed
- advisory mode may warn and reduce optimization
- safe mode may reject lifetime-sensitive uses unless the call is behind an explicit
  escape hatch

### THIR -> MIR Lowering

The lowering pass (`tpyc/mir/lower.py`) converts THIR to MIR:

1. **Control flow desugaring.** `if`/`else` -> `Branch` terminators, `for` -> loop
   blocks with `Goto`/`Branch`, `match` -> `Switch`, `while` -> loop with `Branch`.
   `for/else` and `while/else` desugar to a boolean flag + `Branch` after the loop
   (flag is set on `break`, checked after loop exit). `try`/`except` for
   `@error_return` desugars to `Invoke` terminators for return-tier handling, while
   throw-tier `try` / `except` must preserve enclosing region / handler metadata.
   Chained comparisons (`a < b < c`) desugar to short-circuit `Branch` chains during
   lowering.

2. **Place construction.** Each lvalue expression becomes a `Place`. Field accesses,
   subscripts, and derefs nest naturally.

   Type narrowing must also survive lowering. Branches and match arms that narrow a
   name's type insert explicit `Narrow(local, narrowed_type, source)` statements on the
   dominated path. Later MIR passes and MIR-backed codegen consult these statements to
   build block-local type environments. This avoids losing facts like "in this block,
   `x` is known to be `Foo`" after flattening THIR control flow into basic blocks.

3. **Initial Move/Copy assignment.** The lowering pass inserts `Move` for last-use
   sites (from THIR's `is_last_use` flags) and `Copy` elsewhere. The optimization
   pass may upgrade `Copy` -> `Move` later.

4. **Borrow creation.** Alias assignments (`y = x` for non-value types) become
   borrows of the underlying owner place. Element access and view creation should lower
   to summarized element-storage borrows:

   - `y = x` -> borrow of `Local("x")` (or the owner place behind it)
   - `v = items[i]` -> borrow of `Elements(Local("items"))`
   - `span = items[a:b]` -> view borrow of `Elements(Local("items"))`
   - `p = take_ptr(x)` -> pointer borrow of `Local("x")`

   Exact `Index(base, i)` borrows can be added later for more precision, but the initial
   MIR should support the summarized `Elements(base)` form because it matches the current
   TPy invalidation rules.

5. **StorageLive/StorageDead.** `StorageLive` at variable declaration, `StorageDead`
   at explicit `del` statements.

### MIR Passes

Each pass is an independent function `pass(mir: MIRFunction) -> MIRFunction` or
`pass(mir: MIRFunction) -> list[Diagnostic]`:

#### Pass 1: Liveness Analysis

Standard backward dataflow on the CFG. For each basic block, compute which variables
are live at entry and exit. This replaces `tpyc/liveness.py` with a principled
algorithm that handles branches, loops, and join points correctly.

Result: `LivenessInfo` mapping each statement to the set of live variables after it.

#### Pass 2: Move Optimization

Using liveness info, upgrade `Copy` -> `Move` where the source is dead after:

```
Before:  _tmp = Copy(x)       # x is dead after this point
After:   _tmp = Move(x)       # ownership transferred
```

This replaces the current `_maybe_move()` / `movable_locals` / `all_last_uses`
machinery with a single, clean pass.

#### Pass 3: Borrow Checking

Walk the CFG forward, maintaining per-block borrow state:

```python
BorrowState:
  active_loans: dict[Place, set[LoanInfo]]
  moved_places: set[Place]
```

At each statement:
- `Borrow(place, mode=Mutable, ...)` -- check no conflicting live loans on `place` or overlapping
  parent/child places
- `Borrow(place, mode=Shared, ...)` -- check no live mutable / move-conflicting loans on `place`
- `Move(place)` -- check no active loans that still reach `place`, mark as moved
- `Assign(place, ...)` -- invalidate or conflict with child-place loans as appropriate
- `Assign(Struct(base), ...)` / structural mutation calls -- conflict with loans on
  `Elements(base)` and views derived from them
- Calls with mutated params -- check no conflicting loans on argument places
- `Ptr[T]` creation / use -- treat as explicit nullable-reference loans, not as a fully
  unchecked bypass; pointer arithmetic remains outside this pass in `tpy.unsafe`

For summarized container places, the critical rules are:

- loans on `Elements(base)` represent element refs, spans, iterators, and other views
- mutating `Struct(base)` may invalidate `Elements(base)` loans
- sibling field loans (`Field(x, "a")` vs `Field(x, "b")`) do not conflict unless a
  parent-place operation invalidates both

At branch join points, merge loan states conservatively across reachable predecessors.
The key win over the current AST-based checker is that the analysis is attached to CFG
edges and explicit places rather than string roots and ad hoc freeze/restore snapshots.

Loop headers are merge nodes with pre-loop and back-edge predecessors. Monotone
kill-facts (pointer non-null, parameter provenance, trusted-call-return, type
narrowing) must be meet-merged at the header rather than restored from the pre-loop
snapshot: a fact that the body clears must not re-appear after loop exit. The
current AST-based checker applies a single-pass intersection for all four sets at
loop exit (`tpyc/sema/init_tracker.py::apply_loop_exit_facts`), which is sound for
post-loop uses but remains optimistic for mid-body uses (body analysis starts from
the pre-loop snapshot). In MIR this falls out of standard forward dataflow at the
header and should become a hard correctness requirement for Pass 3, with no
mid-body approximation.

This replaces the current `BorrowTracker` in `sema/context.py` with path-sensitive
analysis. The key improvement: an `if` branch that moves a variable does not conflict
with an `else` branch that borrows it, because they are on different paths.

The same pass can produce different severities depending on enforcement mode:

- **advisory/default**: emit warnings, keep lowering
- **safe opt-in**: elevate selected violations (dangling borrowed return, structural
  mutation while `Elements(base)` is loaned, move with live aliases, invalid `Ptr`
  provenance) to hard errors

The `Validate` statement family exists so MIR lowering and early analysis passes can
materialize the checks that later become diagnostics or hard errors:

- `Validate(ActiveLoanConflict, place)` -- use/mutation conflicts with a live loan
- `Validate(UseAfterMove, place)` -- moved place used again
- `Validate(StructuralMutationDuringLoan, place)` -- structural mutation invalidates
  element/view loans
- `Validate(DanglingBorrowReturn, place)` -- borrowed return escapes owner lifetime
- `Validate(InvalidPtrProvenance, place)` -- `Ptr[T]` escapes or aliases invalidly

#### Pass 4: Value Range Propagation

Forward dataflow tracking integer ranges `[lo, hi]` through the CFG. This replaces
`tpyc/sema/value_range.py` with a CFG-based version that naturally handles loop
induction variables and branch conditions.

Result: at each subscript/deref, whether bounds check / null check can be elided.

#### Pass 5: Dead Code Elimination

Remove statements whose results are never used (no live variables depend on them).
Standard backward pass on the CFG.

### Debugging: `--dump-mir`

```
fn main() -> Void:
  bb0:
    StorageLive(items, list[Int32])
    items = Aggregate(List, [Literal(1), Literal(2), Literal(3)])
    StorageLive(total, Int32)
    total = Use(Literal(0))
    goto -> bb1

  bb1:                                  // loop header
    _iter_has_next = Call(iter.__next__, [_iter])
    branch(_iter_has_next) -> bb2, bb3

  bb2:                                  // loop body
    x = Use(_iter_current)
    total = Call(Int32.__add__, [total, x])
    goto -> bb1

  bb3:                                  // after loop
    Call(print, [Move(total)])
    return
```

### Interaction with Ownership Model

TPy's ownership model is advisory by default. Existing codebases must continue to
compile, so the MIR needs to support two enforcement levels over the same core place /
loan analysis:

- **Default mode (advisory)**: emit warnings, drive move/copy optimization, preserve
  current migration-friendly behavior
- **Safe opt-in mode**: treat a selected subset of ownership / lifetime violations as
  hard errors, with explicit escape hatches still available

TPy is therefore not a globally affine type system (see
`docs/CONSUMING_ITERATION_DESIGN.md`). The MIR should model ownership strongly enough to
support an enforcing mode later, but its default interpretation remains advisory.

**Move/Copy is a place-level decision.** `Move` means "transfer ownership of the
underlying place". In advisory mode, a failed move check may become a warning or may be
lowered back to `Copy` / `Use` depending on the operation. In safe mode, the same check
can be a hard error.

**`Own[T]` requests transfer, it does not make names affine.** When a function parameter
is `Own[T]`, the caller's argument is lowered as a request to `Move(arg_place)`. This is
legal only when the owner place is unique enough at that program point. The local binding
itself does not permanently change type from `Ref[T]` to `Own[T]`; the access mode is
chosen per use site.

**`Ptr[T]` remains available even in safe mode.** The intended meaning of `Ptr[T]` is
"explicit nullable reference", not unrestricted raw pointer. In safe mode:

- plain creation / passing / returning / dereferencing of `Ptr[T]` can remain allowed
- provenance and lifetime of the pointee place are checked
- `Ptr[readonly[T]]` participates as an explicit readonly borrow
- pointer arithmetic, unchecked casts, and arbitrary address fabrication stay in
  `tpy.unsafe` as escape hatches outside the safety guarantee

This preserves migration viability for existing low-level code while still allowing a
stronger safety story for ordinary non-pointer borrows.

**Consuming iteration lowers naturally.** A consuming `for` loop:

```python
for x in items:    # items is last use, consuming __iter__ selected
    process(x)     # x is Own[T], movable
```

Lowers to:

```
_iter = Call(__iter__, [Move(items)])     // consuming overload, items moved
bb_loop_body:
  x = Move(_iter_current)                // element moved out of iterator
  Call(process, [Move(x)])               // x moved into process
```

The THIR's `is_consuming` flag drives the selection of `Move` vs `Use` for the
iterable, and the element variable is naturally movable.

**`del` has two forms.** `del x` (variable destruction) lowers to `StorageDead(x)` in
MIR, enabling early resource release. `del obj[key]` (container element deletion)
lowers to `Call(__delitem__, [obj, key])`. Normal scope-exit destruction is handled by
C++ RAII -- the MIR does not insert drops at scope boundaries.

**`@nocopy` types.** For `@nocopy` types, the move optimization pass can verify that
no `Copy` instructions exist for that type -- any remaining `Copy` is a compile error.
This is cleaner than the current approach of checking during type coercion in sema.

**Borrow checking respects TPy's permissive aliasing.** Unlike Rust, TPy allows
multiple mutable references to the same object (matching Python semantics). The borrow
checker focuses on:
- Iterator invalidation (mutation during iteration)
- Element reference invalidation (structural mutation while element is borrowed)
- Pointer invalidation (reallocation while `Ptr[T]` is outstanding)
- Use-after-move for `@nocopy` types

It does **not** enforce exclusive mutable access (no "aliasing XOR mutability" rule).

### MIR -> C++ Codegen

The codegen backend reads MIR instead of THIR:

| MIR construct | C++ emission |
|---------------|-------------|
| `Move(x)` | `std::move(x)` |
| `Copy(x)` | `x` (C++ copy constructor) |
| `Use(x)` | `x` |
| `Borrow(x, Shared, Alias)` | (variable is `T*` or `T&` -- whole-object reference) |
| `Borrow(x, Shared, Field)` | (variable points to `parent.field`) |
| `Borrow(x, Shared, Element)` | (variable points to `container[i]`) |
| `StorageLive(x, T, Value)` | `T x;` or `T x = ...;` |
| `StorageLive(x, T, Pointer)` | `T __slot_x; auto* x = &__slot_x;` |
| `StorageDead(x)` | `{ /* end scope for x */ }` or explicit destruction |
| `Goto(bb)` | fall-through or `goto` (structured emission avoids goto where possible) |
| `Branch(c, t, f)` | `if (c) { ... } else { ... }` |
| `Switch(...)` | `switch` or `if`/`else if` chain |
| `Invoke(call, ok, err, err_local)` | `auto __res = call; if (!__res) { err_local = __res.error(); goto err; }` |
| `Yield(val, resume)` | state-machine `switch` dispatch |

The codegen reconstructs structured control flow from the CFG where possible (if/else,
while, for) to keep the C++ readable. This is a well-studied problem (structural
analysis / region detection), but it is also one of the biggest migration risks. MIR-
backed codegen should therefore require explicit structured-region tags from lowering:
loop headers/latches/exits, `for/else` and `while/else` regions, `with` guards,
return-tier and throw-tier `try` regions, generator dispatch roots, and short-circuit
comparison regions. "Recover structure from raw CFG alone" is not a realistic
implementation requirement for the first MIR-backed codegen pass.

---

## What Does NOT Change

- **Type checking stays tree-based.** Overload resolution, generic instantiation,
  protocol conformance, type inference -- all remain in sema, operating on the AST.
  These are naturally tree-shaped operations.

- **C++ templates for generics (C++ backend).** No monomorphization in the TPy
  compiler for the C++ backend. Generic functions in MIR carry type parameters, and
  codegen emits `template<typename T>`. An LLVM backend would add a monomorphization
  pass (see Future: LLVM Backend).

- **Parser unchanged.** The parser produces the same AST. THIR lowering is a new
  pass after sema, not a parser change.

- **Test structure unchanged.** Snapshot tests compare generated C++ output. Since
  codegen still produces C++, the test infrastructure works as-is. New snapshot tests
  can be added for THIR and MIR dumps.

- **Mutation propagation stays in sema.** The Phase 2 call-graph fixpoint (transitive
  mutation inference) runs after sema and before THIR lowering. Its results are
  materialized into THIR nodes (`mutated_params`, `is_readonly`). MIR borrow checking
  consumes these facts but does not recompute them.

---

## Phasing and Dependencies

```
Phase 1 (THIR):
  1.1  Define THIR node types                             tpyc/thir/nodes.py
  1.2  Implement THIR lowering pass                       tpyc/thir/lower.py
  1.3  Add --dump-thir CLI flag                           tpyc/cli.py
  1.4  Create THIRCodeGenContext                           tpyc/codegen_cpp/context.py
  1.5  Migrate codegen to read from THIR                  tpyc/codegen_cpp/*.py
  1.6  Remove analyzer reference from codegen             tpyc/codegen_cpp/context.py
  1.7  Add THIR snapshot tests                            tests/

Phase 2 (MIR):
  2.1  Define MIR types (Block, Place, Stmt, Rvalue)      tpyc/mir/nodes.py
  2.2  Implement THIR -> MIR lowering                     tpyc/mir/lower.py
  2.3  Implement liveness pass                            tpyc/mir/liveness.py
  2.4  Implement move optimization pass                   tpyc/mir/move_opt.py
  2.5  Implement borrow checking pass                     tpyc/mir/borrow_check.py
  2.6  Implement value range pass                         tpyc/mir/value_range.py
  2.7  Add --dump-mir CLI flag                            tpyc/cli.py
  2.8  Migrate codegen to read from MIR                   tpyc/codegen_cpp/*.py
  2.9  Remove old liveness.py, BorrowTracker,             tpyc/liveness.py,
       value_range.py                                     tpyc/sema/context.py,
                                                          tpyc/sema/value_range.py
  2.10 Add MIR snapshot tests                             tests/
```

Phase 1 is a prerequisite for Phase 2. Within each phase, steps are sequential except
that snapshot tests (1.7, 2.10) can be added incrementally alongside each step.

---

## Future: LLVM Backend

The MIR design intentionally keeps the door open for an LLVM backend. This section
documents what that would require and how C++ interop is preserved.

### Pipeline

The MIR stays backend-agnostic. The backend choice determines which lowering runs
after the shared analysis passes:

```
                        ┌─> C++ codegen (structured C++ emission)
THIR -> MIR -> passes ──┤
                        └─> LLVM lowering (future)
                              ├─ monomorphization pass
                              ├─ drop insertion pass
                              └─ LLVM IR emission
```

Passes 1-5 (liveness, move optimization, borrow checking, value range, dead code)
are shared. The backends diverge only at the final emission stage.

### What LLVM Requires Beyond C++

| Concern | C++ backend | LLVM backend |
|---------|-------------|-------------|
| **Generics** | C++ templates | Monomorphization pass: stamp out concrete versions of each generic function for every used type combination |
| **Drops** | C++ RAII (implicit) | Explicit drop insertion pass: compute drop points at scope exits, `Move` sites, and early `StorageDead` |
| **STL types** | Direct use (`std::vector`, `std::string`, etc.) | Link against libstdc++/libc++ and call through C-ABI wrappers, or provide a TPy runtime library |
| **Name mangling** | C++ compiler handles it | Emit mangled names following the platform ABI (Itanium/MSVC) |
| **Exceptions** | C++ exceptions / `std::expected` | LLVM `invoke`/`landingpad` for unwinding, or keep `std::expected` via C-ABI calls |

The **monomorphization pass** is the largest addition. It runs on MIR before LLVM
lowering, replacing generic type parameters with concrete types and duplicating
function bodies. This is the same approach Rust takes (monomorphize on MIR, then
lower to LLVM IR). The C++ backend skips this pass entirely.

The **drop insertion pass** walks the CFG and inserts destructor calls at every point
where a variable goes out of scope or is moved. The C++ backend skips this because
C++ RAII handles it implicitly. For LLVM, drops are explicit `Call` instructions to
destructor functions.

### C++ Interop Without Generating C++

Interop is an **ABI contract**, not a source-level dependency. LLVM-generated machine
code can interoperate with C++ code because both follow the same platform ABI.

| Direction | Mechanism |
|-----------|-----------|
| **TPy calls C++** | `@native` declarations provide the C++ function signature. The LLVM backend emits a call using the platform's C++ ABI (same calling convention, name mangling). The C++ library is linked at link time. |
| **C++ calls TPy** | The TPy compiler generates a C++ header (`.hpp`) declaring the TPy-compiled functions with proper mangling. C++ code `#include`s the header and links against the TPy-compiled object files. |
| **Shared types** | Types like `std::vector<int32_t>` have a fixed ABI layout. LLVM-generated code can construct/read/write them if it knows the layout. Alternatively, C-ABI wrapper functions handle type construction/access. |

This is proven by prior art:
- **Rust** interops with C++ via `cxx`/`bindgen` without generating C++ source
- **Swift** interops with ObjC/C++ through ABI compatibility
- **Clang itself** compiles C++ to LLVM IR -- so LLVM IR is inherently ABI-compatible
  with C++ compiled by Clang

### Runtime Library Strategy

The current C++ backend relies on the C++ standard library (`std::vector`,
`std::string`, `std::optional`, `tpy::ordered_map`, etc.) plus TPy's runtime headers
in `runtime/cpp/include/tpy/`. For an LLVM backend, two viable strategies:

1. **Link against the C++ runtime.** Compile `runtime/cpp/` with a C++ compiler into
   a static/shared library. LLVM-generated code calls into it via C-ABI wrapper
   functions. This reuses all existing runtime code. The wrappers are thin: `vec_push`
   calls `std::vector::push_back`, `str_len` calls `std::string::size()`, etc.

2. **Native TPy runtime (long-term).** Rewrite performance-critical runtime components
   (vector, string, hash map) in TPy itself or in C with LLVM-friendly layouts. This
   eliminates the C++ stdlib dependency but is a large effort. Practical only if/when
   TPy is self-hosting.

Strategy 1 is the pragmatic starting point. The C-ABI wrappers can be auto-generated
from the existing runtime headers.

### MIR Design Implications

The MIR as currently designed requires **no structural changes** for LLVM support.
The key decisions that keep it backend-agnostic:

- **Not SSA**: LLVM IR is SSA, but LLVM's `mem2reg` pass converts alloca-based code
  to SSA automatically. MIR places lower to allocas, and LLVM optimizes from there.
- **Explicit Move/Copy/Borrow**: these map to LLVM operations regardless of backend.
  `Move` -> load + store + drop source. `Copy` -> load + store (or memcpy).
- **Typed places with `LocalKind`**: `Value` locals -> alloca. `Pointer` locals ->
  alloca holding a pointer. Natural LLVM lowering.
- **Backend-specific passes**: monomorphization and drop insertion are additional
  passes in the LLVM pipeline, not changes to the shared MIR.

### Not a Near-Term Goal

The LLVM backend is a future possibility, not a current priority. The C++ backend
remains the primary target because:
- Readable C++ output is valuable for debugging, auditing, and interop
- C++ templates avoid the complexity of compiler-side monomorphization
- The C++ ecosystem (build systems, sanitizers, profilers) is directly usable
- The runtime library is already written in C++ headers

The IR design simply ensures that this path is not closed off. If/when the LLVM
backend becomes desirable (e.g., for faster compilation, LTO across TPy modules,
or eliminating the C++ compiler dependency), the MIR is ready.

---

## Open Questions

### Implementation Notes

- **Dynamic / opaque values.** Define how `Any`, dynamic `__getattr__` / `__setattr__`,
  namespace-style objects, and opaque native objects lower to coarse summarized places
  and how they degrade analysis precision.
- **Native contract surface.** Decide the exact user-facing annotation/decorator syntax
  for native lifetime/effect summaries such as `return_borrows_from`, `returns_ptr_to`,
  `mutates`, and `may_invalidate`.
- **Safe-mode boundary.** Spell out which operations are inside the safety guarantee and
  which remain explicit escape hatches (`tpy.unsafe`, pointer arithmetic, unchecked
  casts, opaque native code without contracts).
- **Rebind vs mutate.** Make the rule explicit during implementation that rebinding a
  name creates a new owner/place binding, while mutation changes an existing place.
- **Worked examples.** Add a few focused examples once implementation starts,
  especially for borrowed returns, `Ptr[T]`, views, captures, and structural
  invalidation.
- **Structured MIR metadata.** Decide the exact representation of the region tags needed
  for readable MIR-backed C++ emission.
- **Native default conservatism.** Define the default behavior when a native function
  lacks an explicit lifetime/effect contract in advisory mode vs safe mode.

1. **THIR granularity for match/case.** Match arms have complex pattern-matching
   logic. Should THIR preserve the high-level `THIRMatch` with structured arms, or
   desugar patterns into explicit comparisons? Recommendation: keep structured -- the
   `match` codegen already handles this well, and desugaring loses readability.

2. **MIR for top-level statements.** Module-level code (globals, top-level expressions)
   uses a different variable model (pointer slots). Should this go through MIR, or
   should MIR only cover function bodies? Recommendation: MIR for function bodies
   only, at least initially. Top-level code has simpler control flow and less need
   for path-sensitive analysis.

3. **CFG reconstruction for codegen.** Emitting readable C++ from a CFG requires
   reconstructing structured control flow. This is non-trivial: `break`/`continue`
   with labels, `for/else`/`while/else`, `with` statement guards, `try`/`except`
   error-return patterns, and generator state machines all have structured C++
   emission patterns that must be recovered from the CFG. Since all MIR is lowered
   from structured Python, the CFG is always reducible -- but the reconstruction
   still needs careful handling of each pattern. Recommendation: tag MIR blocks with
   their source-level structure during lowering (loop headers, if-then/else, with
   guards) to simplify reconstruction, rather than recovering structure purely from
   the CFG topology.

4. **Incremental adoption.** Should codegen support both THIR and AST input during
   migration, or is a big-bang switch acceptable? Recommendation: dual-mode during
   migration -- each codegen module can be switched independently, verified by running
   the full test suite.

5. **Separate THIR and MIR codegen backends.** During Phase 2, codegen switches from
   THIR to MIR. Should both backends coexist permanently (e.g., THIR backend for fast
   debug builds, MIR backend for optimized builds)? Recommendation: single MIR backend
   once Phase 2 is complete. The MIR pass pipeline can be shortened for debug builds
   (skip optimization passes).

6. **Generator functions in MIR.** Generator functions are currently lowered to state-
   machine structs in codegen (`gen_generators.py`). Should this transformation happen
   during THIR -> MIR lowering (generators become explicit state machines in MIR), or
   should MIR represent generators with `Yield` terminators and defer the state-machine
   transform to MIR -> C++ codegen? Recommendation: `Yield` terminators in MIR --
   this keeps MIR closer to the source semantics and lets the state-machine transform
   remain a codegen concern. But this means MIR passes (liveness, borrow checking) must
   understand that `Yield` suspends and resumes, which complicates dataflow analysis.

7. **String/bytes view borrow tracking.** Sema currently tracks `StrView`/`BytesView`
   borrows separately from `BorrowTracker` (via `str_source_borrows`,
   `bytes_source_borrows`). Should MIR unify these with the general `Borrow`
   instruction, or keep them separate? Recommendation: unify -- a `StrView` borrowing
   from a `str` variable is conceptually the same as any other borrow. The `BorrowKind`
   may need a `View` variant to capture the "invalidated by any mutation of source"
   semantics.

8. **Generic param ABI for TPy types with storage/param split.** Several TPy types
   have a storage/param C++ split: `str` (storage `std::string`, param
   `std::string_view`), `String` (storage `std::string`, param `const std::string&`),
   `bytes` (storage `std::vector<uint8_t>`, param `std::span<const uint8_t>`),
   `bytearray` (storage `std::vector<uint8_t>`, param mutable ref). Non-generic
   codegen handles the split position-aware (param positions emit the param
   formatter, storage positions emit the storage formatter). Generic codegen uses
   the runtime trait `param_val_or_ref_t<T>` keyed on the C++ storage type, which
   cannot distinguish `str` from `String` (both `std::string`) or `bytes` from
   `bytearray` (both `std::vector<uint8_t>`). Net effect today: generic-T-over-str
   pays a `std::string` materialization at every call site (SSO covers short
   literals; long literals heap-allocate once per call). C++ template instantiation
   erases TPy-type identity by the time it sees `T`; no runtime trait keyed on the
   C++ type can recover it.

   No fix is unambiguously best. The honest design landscape:

   | Approach | Idiomatic C++ | `vector<str>` interop | Perf gap closed | Cost |
   |----------|---------------|-----------------------|-----------------|------|
   | **Current state (accept asymmetry)** | yes | preserved | no (small gap) | none |
   | **Distinct C++ types** (`auto_string : public std::string`) | yes | **broken** -- `vector<auto_string>` is not `vector<std::string>` | yes | medium runtime + audit churn |
   | **Codegen monomorphization** (per-call function emission, no template) | yes (output-wise) | preserved | yes | heavy compiler internals (instantiation registry, cross-module emission) |
   | **Descriptor template parameter** (`template<class TDesc>` with `TDesc::storage`, `TDesc::param`) | **no** -- compromises readable C++ output goal | preserved | yes | medium codegen churn but readers must learn descriptor pattern |
   | **Drop the split entirely** | yes | preserved | no (bigger gap, applies to non-generic too) | none |
   | **Trait specialization on shared C++ types** | yes | preserved | yes | unsound -- conflates `str`/`String` and `bytes`/`bytearray`; rejected |
   | **Auto-downgrade `T=str` to `T=StrView` for literals** | no | preserved | yes | unsound -- signature-level safety check can't cover body-side dangling cases; rejected |

   The three idiomatic options each pay a distinct cost. There is no row that wins
   all three of `idiomatic / vector interop / perf gap closed` without paying a
   real cost somewhere.

   **Codegen monomorphization** is the cleanest long-term path *if* the perf gap
   ever becomes worth solving. Compiler emits one C++ function per `(generic, TPy
   type args)` instantiation instead of a single template. Each emitted function
   uses normal C++ types (no descriptors, no traits, no `auto_string` wrapper) --
   `inline std::string echo_str(std::string_view x) { return std::string(x); }`
   reads the way C++ developers expect. Vector interop preserved because storage
   types stay unchanged (`list[str]` still `std::vector<std::string>`). Cost is
   compiler-internal: instantiation registry, cross-module emission rules, header
   placement for inline functions, generic methods/classes/`Fn[..., T]`/protocol
   integration. Estimate: 2-4 weeks of focused work.

   **Distinct C++ types** (auto_string approach) is the lighter-touch idiomatic
   option but pays its cost user-visible: existing user code that does `@native`
   interop with `std::vector<std::string>` against TPy `list[str]` would have to
   migrate to `list[String]` (which stays `std::vector<std::string>`). Mechanical
   migration but real surface change. Estimate: 1-2 weeks.

   **Current state** is the pragmatic answer. The perf gap is small in practice
   (SSO covers the common case of short literals; longer literals through pure
   pass-through generics is a rare pattern); users who hit a real hot path can
   write `def f(s: StrView)` explicitly. Aligns with TPy's "opt-in constraints
   for hot paths" philosophy: the perf gap is the cost of *not* opting in to
   compiler complexity. Recommendation: stay here unless measured workloads
   justify the upgrade.

   **Descriptor template parameter** -- documented for completeness, but the
   non-idiomatic generated C++ output (`f<tpy::str_desc>(...)` instead of
   `f<std::string>(...)`) compromises a stated TPy goal: readable C++ output for
   debugging, auditing, and interop. Demoted to "considered but compromises
   primary goal." Could still serve as a fallback for future TPy types whose
   semantics genuinely cannot be expressed via distinct C++ types, but for the
   str/bytes case the output cost outweighs the benefits.

   **Drop the split** is the simplification answer. Single representation per
   TPy type (`str` always `std::string`, `bytes` always `std::vector<uint8_t>`).
   `StrView`/`BytesView` remain as explicit opt-in for view semantics. Predictable,
   uniform, no special machinery. Pays the materialization cost at every str
   param boundary (SSO covers it for short literals). Worth considering if/when
   the architectural simplification becomes more valuable than the optimization.

   **Scheduling**: no work planned. The current state is the safety floor. If the
   perf gap becomes worth fixing (driven by measured workloads, not preemptive
   optimization), the recommended target is codegen monomorphization. That work
   does *not* require THIR/MIR to land first but probably benefits from being
   done concurrently with the THIR codegen migration to avoid double-churn.

9. **Tuple form as a first-class type fact.** Today `TupleType` is a single sema type
   whose C++ representation depends on context -- borrow form (`tuple<T*,...>`,
   one pointer-element shape for every non-value element since the tuple
   borrow-pointer unification; generic elements via `val_or_ptr_t<T>`) at
   param/return/local/frame boundaries, storage form (`tuple<optional<T>,...>`,
   `tuple<T,...>`) at field/container/`Own[]` boundaries. (See
   `LANGUAGE_FEATURES.md` "Borrow Form vs Storage Form" for the canonical definition
   of these forms; this item is specifically about elevating the distinction to a
   first-class IR fact.) Codegen reconstructs which form is needed at each site and
   inserts conversions (`tuple_to_storage[_move]` with per-element dest-shape
   dispatch, `tuple_to_pointer`, `tuple_value_to_borrow`, `to_storage_elem`,
   `to_pointer_form`, `to_val_or_ptr`). The unification fixed the silent-copy
   bug class (a param-/local-/call-rooted reference-member tuple now ALIASES at
   yield/return, matching CPython; the borrow lives in a pointer-holding frame
   field, which the resumable-frame model CAN express -- the prior assessment
   that the share fix was THIR-gated proved wrong), but it did so by adding
   more consumer-side dispatch: every site that READS a tuple element as a
   value re-derives the form. The consumer-site inventory THIR must subsume
   with structural conversion/access nodes:
   - subscript read (`_gen_subscript` tuple branch: raw pointer vs
     `tuple_elem_ref` for generic slots vs `optional_to_ptr` lift),
   - field access / method call on a subscript (`->` vs `.` via
     `_tuple_subscript_yields_borrow_ptr`),
   - value contexts (`gen_expr_deref` deref of borrow subscripts),
   - unpack binding (`unwrap_ref(tuple_elem_ref(std::get<i>(...)))`),
   - print/repr and hash (runtime `print_element` / `__hash__` `T*` deref
     overloads),
   - comparison (`tpy::tuple_eq` / `tpy::tuple_lt` routing in the binop
     emitter, plus the `in`-needle storage lift),
   - construction slots (`_tuple_literal_slot_info` + address-of /
     `tuple_value_to_borrow` / `to_val_or_ptr<Dest>` value rendering),
   - boundary wraps (`_maybe_wrap_tuple_to_pointer` / `_to_storage`, the
     call-arg bridge, field writes, return/yield conversion, the await-arg
     lift).
   Remaining open exhibits of the bug class: nested tuples where outer/inner
   forms disagree (BUGS.md nested-tuple entries), rvalue tuple-of-records into
   borrow-form slots (BUGS.md rvalue address-of entry), the rvalue GENERIC
   tuple element gap, and the recursive-union-wrapper durable member (excluded
   from the `T*` form). Each was/is handled by touching consumer-side dispatch
   sites; the IR fact replaces all of it with explicit conversion nodes. THIR
   should make form an explicit type fact (either two distinct tuple types, or
   a form tag on one), so conversion sites become visible in the IR rather
   than reconstructed in codegen.
   Recommendation: form tag on `THIRTupleType` with conversions emitted as explicit
   THIR nodes during lowering -- analogous to how borrows are explicit in MIR.

10. **Covariant return for polymorphic-owner types.** TPy lowers `Own[T]` to
    `std::unique_ptr<T>`, and C++ does not support covariant return on
    `unique_ptr` (only on raw `T*` / `T&` -- a deliberate, repeatedly-reaffirmed
    C++ language restriction, see P0670's rejection). This blocks the natural
    pattern of a method overriding a `@dynamic`-protocol slot with a narrower
    return type (e.g. `Dog.replicate(self) -> Own[Dog]` refining
    `Cloneable.replicate(self) -> Own[Cloneable]`, or
    `BaseException.clone(self) -> Own[BaseException]` refining
    `Throwable.clone(self) -> Own[Throwable]`). An attempt to support it on the
    C++ backend (a sema covariant-return acceptance rule + a
    `tpy::narrowing_cast<>` codegen bridge that emits the vtable slot at the
    parent's wider signature and downcasts at concrete-typed call sites) worked
    but introduced a divergence between the TPy declaration and the emitted C++
    signature, plus a cluster of edge cases (multi-protocol ambiguity, overload
    matching, the narrowing-cast hardening). It was dropped: the cost/benefit
    (mostly a naming preference -- `Box[BaseException]` vs the existing
    `Box[Throwable]` convention) did not justify the machinery, and the existing
    `Box[Throwable]` + virtual-`__raise__` convention (Phase 20) already handles
    polymorphic exception storage and dynamic-type recovery (`raise stored /
    except ConcreteType`). A backend that controls codegen below the C++ language
    layer (LLVM IR, or the MIR -> C++-or-LLVM split here) has no covariant-return
    restriction: raw pointers in vtable slots + a smart-pointer wrap at the call
    boundary (the standard pre-2011 C++ idiom, and what LLVM-targeting languages
    like Rust/Swift do by choice) makes this a clean codegen rule. Revisit when
    the backend question opens up; until then, declare polymorphic-protocol method
    returns at the protocol's own type (`Own[Cloneable]`, `Own[Throwable]`).
    Recommendation: handle at MIR -> backend lowering, not as a C++-backend
    sema/codegen feature.

11. **Uniform local model: every non-value local as slot + alias, with late
    representation folding.** Today the C++ shape of a non-value (or
    pointer-repr-tuple) local is decided EAGERLY at the binding site, by a
    zoo of per-shape mechanisms: `T&` ref binds and `auto&&` tuple aliases
    (single-assignment borrows), `T*` pointer-locals + hoisted
    `std::optional<T>` rvalue slots (`rebind_slots`, reassigned borrows),
    `std::optional<T>` optional-locals (deferred init, sync), the walrus
    variants of each, `tpy::frame_slot<T>` (resumable frames),
    `std::tuple<..., T*>` borrow-form tuple locals, and storage-form tuple
    locals -- tracked across `LocalCppForm` (now including `BORROW_TUPLE`)
    plus side sets that remain the classifier's backing store. Each
    mechanism re-implements init-deferral, rebinding, and aliasing slightly
    differently (operator= vs emplace vs lift), which is where the
    `optional` brace-init corruption class, the default-construct-before-
    assign waste, and the tuple owning/alias rebind rejection all came
    from. The MIR-native model dissolves this: every local is a PLACE (a
    slot owning storage, or a borrow of another place); binding kinds are
    explicit (own-init, alias, rebind); representation selection (direct
    `T`, `T&`, `T*` + slot, `optional<T>` / `frame_slot<T>`,
    pointer-element tuple) becomes a LATE per-place decision driven by
    facts the place already carries -- rebound? crosses a suspension?
    address escapes? null state needed? -- followed by a mem2reg-style
    FOLD that collapses single-binding straight-line places back to plain
    direct bindings so generated C++ stays readable and the hot paths
    (param borrows, loop vars) pay nothing. Provenance/escape soundness
    also unifies: the per-name fact sets sema accumulates today
    (`owns_fresh`, `owning_storage`, `ephemeral_borrow_vars`,
    `safe_to_return_vars`) become properties of the place's loans,
    compositional through aliases, ternaries, and walrus by construction
    instead of per-shape propagation rules. Sub-question: whether the C++
    backend should emit ONE deferred-storage primitive everywhere
    (`frame_slot<T>` in sync bodies too, with the state-aware-destruction
    TODO removing its alive bool) or keep `optional<T>` for sync --
    uniformity favors the former; decide when the fold pass exists so the
    choice is measurable. Pre-IR stopgaps this item subsumes: the tuple
    rvalue-slot design + owning/alias mix (landed pre-IR -- borrow-form
    slot + flow-correct owning fact + `BORROW_TUPLE`, with the side sets
    still the backing store rather than a unified place model), and the
    eager per-site binding decisions in `_gen_var_decl_code` /
    `_gen_named_expr` / the loop binders that it leaves scattered.
    Recommendation: make places-with-late-representation the MIR
    locals model (the natural reading of `Place`/`LoanInfo` above), and
    treat the C++ emission of each representation as a small backend menu
    the fold pass picks from. Sequencing (agreed): the representation
    model + fold are IR-ONLY -- building places/CFG/liveness against the
    AST would be writing MIR badly, twice. The one piece worth pulling
    forward pre-IR if the migration is not imminent is the sema-side
    provenance consolidation (one BindingProvenance record replacing the
    four per-name fact sets; TODO.md entry carries the decision rule).

    Scope extension: str/bytes VIEW locals belong under this umbrella too,
    even though they are value types lowered by a separate mechanism today
    (the `str_vars`/`bytes_vars` view-tracking facts + a binary
    view-XOR-owned-per-variable decision, e.g. `mark_view_reassigned_from_owned`
    promoting the whole local to `std::string`). The place/slot model says
    the variable stays the borrow form (`string_view`) and an owned-source
    assignment lands in a storage slot bound to it, with the fold collapsing
    to plain `std::string` when the slot is the only source (today's
    always-owned behavior). The win over the current binary choice is the
    MIXED case -- a local fed by a literal/param in one branch and an owned
    temporary in another no longer materializes the borrowed branches into
    `std::string`. As with the rest of item 11 this is fold-dependent (the
    fold must collapse the common always-view and always-owned cases or both
    regress to two C++ variables), so it is IR-era, not a pre-IR change. The
    current binary mechanism is sound (extra copy in mixed cases, never a
    dangle), so this is a quality/uniformity gain, not a correctness fix.

12. **Fate of the sema `Ref[T]` wrapper.** `RefType` is the sema-level
    "borrowed, not owned" marker: auto-inserted by `make_ref` on
    function/method params and returns, field/subscript access results, and
    iterator elements; never user-written. Production is centralized and
    disciplined, but consumption is split between two oracles: codegen
    strips the wrapper at function entry (`var_types` is built via
    `unwrap_ref_type`) and re-derives borrow-ness positionally from
    `is_value_type()`, while compatibility treats `Ref[T] ~ T` in both
    directions and inference canonicalizes it away per position
    (`to_owned_storage_form` for owned slots, `to_bare_slot_form` for
    bare-T slots, both in `sema/type_ops.py`). The strip-to-consume ratio
    across the compiler is roughly 8:1. What genuinely rides on the wrapper
    today: copy-into-storage detection (warning when a borrow is silently
    copied into a field/container), generic reference preservation
    (`U=Ref[Point]` -> `val_or_ref<Point>` for iterator combinators and
    `map(identity, ...)`), and lambda trailing return types (`-> T&`).
    Decision: keep `RefType` until THIR, but treat it as FROZEN -- do not
    extend it to new positions (each one adds strip sites and
    inference-leak surface); new borrow-form facts go on AST nodes per the
    migration rules in CLAUDE.md. At THIR lowering, `Ref[T]` dissolves into
    the explicit form fact of items 9 and 11: the borrow-vs-storage form
    tag plus explicit conversion nodes carries everything the wrapper
    encodes, THIR types do not contain `RefType`, and the stripping fabric
    disappears with it.
