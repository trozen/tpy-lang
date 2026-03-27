# Intermediate Representations -- Design

## Status

| Feature | Status |
|---------|--------|
| **Phase 1: THIR** -- typed, immutable IR between sema and codegen | Not started |
| **Phase 2: MIR** -- CFG-based IR for borrow checking and optimization | Not started |

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

## Phase 1: Typed High-Level IR (THIR)

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
  type_registry: TypeRegistry          # shared, immutable after sema

THIRFunction
  name: str
  params: list[THIRParam]
  return_type: TpyType
  body: list[THIRStmt]
  mutated_params: frozenset[str]       # from Phase 2 propagation
  is_readonly: bool
  return_borrows_from: frozenset[int]  # param indices
  is_generic: bool
  type_params: list[TypeParam]
  overload_group: str | None
  is_generator: bool                   # generator function (yield)
  generator_yield_type: TpyType | None # yield element type
  generator_states: list[int]          # yield state numbers

THIRParam
  name: str
  type: TpyType                        # fully resolved (Own[T], readonly[T], etc.)
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
  result_type: TpyType

# ... (dict, set, tuple, array, f-string, comprehension, lambda, etc.)
```

#### Statements

```
THIRVarDecl
  name: str
  resolved_type: TpyType              # always resolved
  init: THIRExpr | None
  is_hoisted: bool                     # escapes inner scope
  is_pointer_local: bool               # T* slot (non-value type local)
  narrowing_facts: dict[str, TpyType]  # from isinstance/assert on this decl

THIRAssign
  target: THIRExpr                     # name, field, subscript
  value: THIRExpr

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
  body: list[THIRStmt]
  handlers: list[THIRExceptHandler]

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

### Migration Strategy

Codegen currently reads from two sources: AST node annotations and `SemanticAnalyzer`
state. The migration is mechanical:

1. Define THIR node types in `tpyc/thir/nodes.py`
2. Implement `lower_module()` in `tpyc/thir/lower.py`
3. Add `--dump-thir` to CLI
4. Create a `THIRCodeGenContext` that reads from THIR instead of analyzer
5. Migrate codegen modules one at a time (expressions, statements, functions, records)
6. Remove `analyzer` reference from `CodeGenContext`
7. Run full test suite at each step -- output should not change

The lowering pass is the only new logic. Everything else is rewiring existing reads.

---

## Phase 2: Mid-Level IR (MIR)

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
  | Invoke(call: MIRRvalue, ok: BlockId, err: BlockId)  # @error_return try/except
  | Unreachable
```

`Yield` suspends the generator, returning a value to the caller. On resume, execution
continues at the `resume` block. This supports the existing state-machine codegen for
generator functions (`gen_generators.py`).

`Invoke` is used for `@error_return(E)` calls: if the callee returns an error via
`std::expected`, control flows to `err`; otherwise to `ok`. This replaces the current
goto-label-based `try`/`except` codegen.

#### Places

A `Place` identifies a memory location. This replaces the current string-based storage
keys in `BorrowTracker`:

```
Place
  = Local(name: str)                         # local variable
  | Field(base: Place, field: str)           # record field
  | Index(base: Place, index: MIROperand)    # container subscript
  | Deref(base: Place)                       # pointer dereference

# Examples:
# x           -> Local("x")
# x.items     -> Field(Local("x"), "items")
# x.items[i]  -> Index(Field(Local("x"), "items"), Local("i"))
# *ptr        -> Deref(Local("ptr"))
```

Places give the borrow checker precise knowledge of what is accessed. `Field(x, "a")`
and `Field(x, "b")` are distinct -- borrowing one does not conflict with mutating
the other.

#### Statements

```
MIRStmt
  = Assign(place: Place, rvalue: MIRRvalue)
  | StorageLive(local: str, type: TpyType, kind: LocalKind)
  | StorageDead(local: str)                   # explicit early destruction (del x)
  | Validate(kind: ValidateKind, place: Place)  # borrow check assertion

LocalKind
  = Value                    # T -- value type, stored directly
  | Pointer                  # T* -- pointer-local (non-value type, stack-allocated slot)
  | Ref                      # T& -- reference to another local (alias)
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
  | Borrow(place: Place, kind: BorrowKind)           # create reference
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

#### BorrowKind

```
BorrowKind
  = Alias                   # whole-container alias (safe through mutations)
  | Field                   # field-level reference
  | Element                 # reference to container element
  | Iterator                # for-loop iterator over container
  | Pointer                 # raw pointer (Ptr[T])
```

These correspond to the existing `BorrowKind` enum in `sema/context.py` (`ALIAS`,
`FIELD`, `ITER`, `ELEMENT`, `PTR`). The key semantic distinction: `Alias` borrows
are safe through container mutations (whole-object reference, not invalidated by
reallocation), while `Element` and `Iterator` borrows are invalidated by structural
mutations (append, insert, del). `Field` borrows are invalidated when the parent
object is reassigned but not by sibling field mutations.

In the current compiler, borrows are side-state in `BorrowTracker`. In MIR they
become explicit `Borrow` instructions, making conflicts visible in the IR.

### THIR -> MIR Lowering

The lowering pass (`tpyc/mir/lower.py`) converts THIR to MIR:

1. **Control flow desugaring.** `if`/`else` -> `Branch` terminators, `for` -> loop
   blocks with `Goto`/`Branch`, `match` -> `Switch`, `while` -> loop with `Branch`.
   `for/else` and `while/else` desugar to a boolean flag + `Branch` after the loop
   (flag is set on `break`, checked after loop exit). `try`/`except` for
   `@error_return` desugars to `Invoke` terminators. Chained comparisons (`a < b < c`)
   desugar to short-circuit `Branch` chains during lowering.

2. **Place construction.** Each lvalue expression becomes a `Place`. Field accesses,
   subscripts, and derefs nest naturally.

3. **Initial Move/Copy assignment.** The lowering pass inserts `Move` for last-use
   sites (from THIR's `is_last_use` flags) and `Copy` elsewhere. The optimization
   pass may upgrade `Copy` -> `Move` later.

4. **Borrow creation.** Alias assignments (`y = x` for non-value types) become
   `Borrow(Local("x"), Shared)` instructions. Element access on containers becomes
   `Borrow(place, Element)`.

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
  active_borrows: dict[Place, set[BorrowInfo]]
  moved_places: set[Place]
```

At each statement:
- `Borrow(place, Mutable)` -- check no other borrows on `place` or parent/child places
- `Borrow(place, Shared)` -- check no mutable borrows on `place`
- `Move(place)` -- check no active borrows on `place`, mark as moved
- `Assign(place, ...)` -- invalidate borrows on child places (field/element borrows)
- Calls with mutated params -- check no conflicting borrows on arguments

At branch join points, merge borrow states conservatively (union of active borrows).

This replaces the current `BorrowTracker` in `sema/context.py` with path-sensitive
analysis. The key improvement: an `if` branch that moves a variable does not conflict
with an `else` branch that borrows it, because they are on different paths.

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

TPy's ownership model is a **copy-avoidance heuristic**, not an affine type system
(see `docs/CONSUMING_ITERATION_DESIGN.md`). The MIR respects this:

**Move/Copy is advisory, not enforced linearly.** `Move` means "the compiler believes
this is the last use and can transfer ownership." It does not mean "use-after-move is
a compile error." Use-after-move checking is a separate validation pass that can be
strict for `@nocopy` types and advisory (warning) for regular types.

**`Own[T]` lowers to `Move`.** When a function parameter is `Own[T]`, the caller's
argument is lowered as `Move(arg)`. When a variable is assigned to owned storage
(field, container), it is lowered as `Copy(arg)` with a copy warning -- or `Move(arg)`
if it is a last use.

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
| `Borrow(x, Alias)` | (variable is `T*` or `T&` -- whole-object reference) |
| `Borrow(x, Field)` | (variable points to `parent.field`) |
| `Borrow(x, Element)` | (variable points to `container[i]`) |
| `StorageLive(x, T, Value)` | `T x;` or `T x = ...;` |
| `StorageLive(x, T, Pointer)` | `T __slot_x; auto* x = &__slot_x;` |
| `StorageDead(x)` | `{ /* end scope for x */ }` or explicit destruction |
| `Goto(bb)` | fall-through or `goto` (structured emission avoids goto where possible) |
| `Branch(c, t, f)` | `if (c) { ... } else { ... }` |
| `Switch(...)` | `switch` or `if`/`else if` chain |
| `Invoke(call, ok, err)` | `auto __res = call; if (!__res) goto err;` |
| `Yield(val, resume)` | state-machine `switch` dispatch |

The codegen reconstructs structured control flow from the CFG where possible (if/else,
while, for) to keep the C++ readable. This is a well-studied problem (structural
analysis / region detection).

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
