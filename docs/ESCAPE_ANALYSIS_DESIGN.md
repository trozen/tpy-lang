# Escape Analysis, Borrow Checking & Value Provenance -- Design

## Implementation Roadmap

### Phase 1: Quick Wins (no borrow infrastructure needed)

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| 5 | Ptr `is not None` narrowing | S | Done | [5](#5-ptr-narrowing-after-is-not-none) |
| 7a | `@pure` annotation (trusted, no enforcement) | S | Done | [7](#7-pure-annotation) |
| 10a | Send/Sync auto-derivation (markers only) | S | Done | [10](#10-thread-safety-sendsync) |
| 4a | Match arm liveness (`analyze_last_uses` for `match` statements) | S | Done | [4](#4-liveness-analysis) |

### Phase 2: Intra-Function Borrow Checking

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| -- | FlowFacts refactor (unified flow state class) | M | Done | -- |
| 6a | Container mutation during iteration | S-M | Done | [6a](#6a-container-mutation-during-iteration) |
| 6.1 | Borrow map infrastructure (migrate loop_borrowed_vars) | S | Done | [6](#6-intra-function-borrow-checking) |
| 6.2 | Assignment borrows (`y = x` non-value alias) | M | Done | [6](#6-intra-function-borrow-checking) |
| 6.3 | Ptr/subscript/field borrows | M | Done | [6](#6-intra-function-borrow-checking) |
| 6.4 | Conflict detection at all mutation points | M | Done | [6](#6-intra-function-borrow-checking) |
| 6.5 | Function parameter mutation detection | S-M | Done | [6](#6-intra-function-borrow-checking) |
| 6b | For-loop const-ref binding | S | Done | [6b](#6b-for-loop-const-ref-binding) |
| 6c | String view extension (Array, records) | M | Done | [6c](#6c-string-view-extension-to-containers) |
| -- | Extract BorrowTracker class from SemanticContext | S | Done | -- |
| 11 | Integer range tracking (loop patterns) | M | Done | [11](#11-integer-range-tracking) |
| 11a | Bounds check elision | S | Done | [11a](#11a-bounds-check-elision) |

### Phase 3: Cross-Function Analysis

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| 6.6 | Span/StrView slice borrow tracking | S | Done | [6](#6-intra-function-borrow-checking) |
| 8a | Parameter mutation inference | M | Done | [8](#8-cross-function-borrow-inference) |
| 8a.2 | Track address-taking (`Ptr(param)`) in Phase 1 -- enables `const T&` for record params | S | Done | [8](#8-cross-function-borrow-inference) |
| 8a.3 | Track tuple ref packing (`return (x, param)` where tuple slot is `T&`) in Phase 1 -- same goal as 8a.2 | S | Done | [8](#8-cross-function-borrow-inference) |
| 8a.4 | `const T&` for protocol-typed params: thread `mutated_params` through `gen_params_with_protocols` | S | Done | [8](#8-cross-function-borrow-inference) |
| 8a.5 | Precise element-ref mutation: defer marking source container until borrower is actually written (currently conservative -- any element ref marks container as mutated) | M | Done | [8](#8-cross-function-borrow-inference) |
| 8b | Return-value borrow contracts | L | Done | [8](#8-cross-function-borrow-inference) |
| 7b | `@pure` enforcement -- or drop `@pure` (see [note](#7-pure-annotation)) | M | Deferred | [7](#7-pure-annotation) |
| 6c+ | String view extension (list, dict) | M | Done | [6c](#6c-string-view-extension-to-containers) |
| 11+ | Integer range tracking (general) | L | Not started | [11](#11-integer-range-tracking) |
| 11+a | Augmented-assignment range shifting (`i += 1` in while-loops) | S-M | Done | [11](#11-integer-range-tracking) |
| 11b | Safe unsigned cast after assertion | S | Done | [11b](#11b-safe-unsigned-cast) |
| 11d | Bounds elision for user types (`__getitem_unchecked__`) | M | Not started | [11d](#11d-bounds-elision-for-user-types-future) |

### Phase 4: Thread Safety

| # | Feature | Effort | Status | Section |
|---|---------|--------|--------|---------|
| 10b | Send/Sync enforcement | M | Not started | [10](#10-thread-safety-sendsync) |
| 10c | Data race detection (shared mutable state) | L | Not started | [10](#10-thread-safety-sendsync) |

Phases are not strictly sequential -- items from different phases can be interleaved.
Integer range tracking (11) can proceed in parallel with borrow checking (6).

### Future Extensions (not in any phase)

| Feature | Notes | Section |
|---------|-------|---------|
| Borrow metadata export | Not needed while compiler has source; design keeps contracts serializable for future use | [9](#9-borrow-metadata-export) |
| View type borrow tracking | Generalize hardcoded Span/Ptr/StrView borrow tracking to user types. Two approaches: (1) field-level annotation marking which field borrows from the outside -- lets the compiler trace borrow flow through constructors; (2) class-level marker protocol (`View`) -- simpler but less precise. Field-level is more useful (closer to Rust's lifetime-on-field) without requiring full lifetime machinery. Prior art: C++ `[[gsl::Pointer]]`, Rust lifetimes. Will likely come up when designing tpy standard library types. | -- |
| 8b-A: `return_borrows_from` Phase 2 propagation | Rule 3 (transitive return inference) silently skips forward-defined callees. Fix: record `ReturnCallEdge`s in Phase 1 and propagate `return_borrows_from` in Phase 2 alongside `mutated_params`. Fixes cross-module correctness and exported metadata. | [Future Extensions](#future-extensions) |
| 8b-B: Deferred call-site borrow registration | Same-module callers miss the borrow registration when the wrapper's contract is a forward ref. Requires a second Phase 1 pass or pending-registration records replayed post-Phase 2. High complexity for a narrow pattern. Depends on 8b-A. | [Future Extensions](#future-extensions) |
| `@may_reallocate` / declarative borrow contracts | Currently the compiler hardcodes which built-in methods are structural (append, insert, del, ...) vs in-place (subscript write). Moving built-in types to `.py` files requires a declarative annotation -- `@may_reallocate` on methods that can invalidate element references, `@return_borrows_from` on methods that return element refs. Unannotated mutating methods default to conservative (structural). For user types, both annotations are inferred transitively (same Phase 1/2 propagation as `mutated_params`) -- no explicit annotation needed on well-structured wrappers. Requires field-level borrow flow tracking (View type item) for the transitive case. Prior art: C++ iterator invalidation rules (prose only, unenforced); Rust makes all `&mut self` methods invalidate borrows (simpler but more restrictive). | [Future Extensions](#future-extensions) |

### Already Done

| Feature | Section |
|---------|---------|
| Dangling return detection (locals, temporaries) | [1](#1-dangling-return-detection) |
| Scope escape detection (loop-local hoisting) | [1](#1-dangling-return-detection) |
| Parameter provenance tracking | [1](#1-dangling-return-detection) |
| Ptr non-null provenance elision | [2](#2-ptr-non-null-provenance-elision) |
| Ptr `is not None` / assert / while narrowing | [5](#5-ptr-narrowing-after-is-not-none) |
| String view safety (`is_view_compatible_source`) | [3](#3-string-view-safety) |
| Liveness analysis (auto-move at last use) | [4](#4-liveness-analysis) |

---

## Context

TPy targets a 100k+ LOC multi-threaded HFT codebase. This sets the bar higher than a
typical POC:

- **Memory safety is non-negotiable.** A dangling pointer in a trading algorithm is a
  wrong trade, not a crash report.
- **Thread safety is non-negotiable.** Data races in concurrent hot paths are
  catastrophic and nearly impossible to reproduce.
- **Zero-cost safety.** Runtime checks (`deref_check`, `normalize_index`) add latency.
  The compiler should elide them when it can prove safety statically, especially in
  `@noalloc` hot paths.
- **No annotation burden.** The language must stay Pythonic. Rust-style lifetime
  parameters are not acceptable. The compiler infers everything it can, and only
  requires annotations at library boundaries (if at all).

The existing ad-hoc safety features (dangling detection, non-null tracking, string view
gating) are all partial solutions to the same underlying problem: **tracking which
references are live and whether the storage they point to can be mutated or
invalidated.** A unified borrow system replaces these with a single, composable model.

---

## Part I: Existing Safety Features (Done)

### 1. Dangling Return Detection

**Files**: `sema/compatibility.py` (`is_dangling_return`, `check_dangling_reference`),
`sema/scope_tracker.py` (`check_escape`, `is_scope_escape`)

Two complementary checks:

**Return-local detection** (`is_dangling_return`): When a function returns a reference
type (`T`, `Ptr[T]`, `StrView`, `Optional[T]`, protocol types), the return expression
is checked recursively. Dangerous sources:

- Local variables (not parameters, not globals, not `self`)
- Constructor calls (create temporaries)
- Array/dict/list literals (create temporaries)
- Unary/binary ops (may create temporaries)
- Ternary where either branch dangles

Safe sources:

- Parameters (caller owns the storage)
- Globals (static lifetime)
- `self` (receiver, caller manages lifetime)
- `param_provenance_vars` (derived from parameter via assignment chain)
- Field access / subscript on safe object (recursive check)
- `Ptr(safe_expr)` (pointer to safe storage)

**Scope escape detection** (`check_escape`): When assigning to a variable at scope
depth D from a source at scope depth D+N, the source may be freed before the target.
The tracker compares `var_scope_depth` entries. Two outcomes:

- **Hoistable**: source is an rvalue-initialized variable (owns its storage, not a
  loop iteration variable). Storage is hoisted to function scope with a warning.
- **Error**: source is a loop variable or lvalue-initialized (aliases other storage).
  Hard error requiring `copy()`.

**Provenance tracking** (`param_provenance_vars`): When `x = param` or `x = param.field`,
`x` is added to `param_provenance_vars`. This makes `return x` safe even though `x`
is technically a local -- its storage derives from the caller. Propagates through
simple assignment chains but not through function calls or complex expressions.

### 2. Ptr Non-Null Provenance Elision

**Files**: `sema/context.py` (`non_null_ptr_vars`), `sema/expressions.py`,
`sema/methods.py`, `sema/init_tracker.py`, `codegen_cpp/expressions.py`

See `docs/DEREF_DESIGN.md` Stage 8 for full details.

When a `Ptr[T]` variable is constructed from a local (`p = Ptr(x)`), the pointer is
provably non-null. The sema tracks this in `non_null_ptr_vars` and sets
`ptr_non_null = True` on field access / method call AST nodes. Codegen emits direct
`ptr->field` instead of `tpy::deref_check(ptr).field`.

**Branch merging**: Uses intersection -- a pointer is only non-null after a branch if
it was non-null at the end of all branches. Managed by `InitTracker.merge_branches()`.

**Clearing**: Reassignment from unknown source (function return, null constructor)
clears the variable from the set.

**Scope**: Field access and method calls only. Deref coercion paths
(`DEREF_COERCION`) always remain null-checked. Function parameters have unknown
provenance (caller might pass null).

### 3. String View Safety

**Files**: `sema/local_deduction.py` (`is_view_compatible_source`,
`_resolve_pending_str_types`), `sema/context.py` (`StrVarInfo`)

The `PendingStrType` system defers the `str` -> `std::string` vs `std::string_view`
decision until after usage analysis. `is_view_compatible_source()` determines which
initialization sources are safe for `string_view` (no risk of dangling):

| Source | Safe? | Reason |
|--------|-------|--------|
| String literal `"hello"` | Yes | Static lifetime |
| `str` parameter | Yes | C++ already passes as `string_view` |
| Another `PendingStrType` / `StrViewType` local | Yes | Same lifetime tier |
| `Final[str]` global | Yes | `constexpr string_view` |
| Function returning `StrView` | Yes | Caller knows it's a view |
| Tuple subscript `t[0]` (lvalue) | Yes | Immutable, stable storage |
| Array subscript `arr[0]` | **No** | Mutation could invalidate |
| Record field `obj.name` | **No** | Mutation could invalidate |
| List/dict subscript | **No** | Mutation + reallocation could invalidate |

A two-pass resolution then finalizes types:
1. Direct usage flags (`+=` forces `std::string`, passed-to-string-param forces it, etc.)
2. Alias promotion: if a variable's source resolved to `std::string`, the alias is
   also promoted (prevents dangling view of a reallocating string).

### 4. Liveness Analysis

**Files**: `liveness.py` (`analyze_last_uses`)

Backward dataflow analysis that identifies last-use sites for auto-move optimization.
Walks statements in reverse, maintaining a live set of variable names. A `TpyName`
node is marked as "last use" if the variable is not read again on any subsequent path.

Key features relevant to borrow checking:

- **Alias awareness**: When `alias = obj` creates a `T&` reference, auto-move of `obj`
  is suppressed while `alias` is live. Detach-on-reassign: if `obj` is reassigned,
  the old alias no longer constrains moves.
- **Fixpoint iteration**: Loops use fixpoint to stabilize the live set across
  iterations (up to 4 passes).
- **Branch merging**: If/else uses union (conservative -- live if used in either path).
- **Termination detection**: `return`/`break`/`raise` clear the live set.
- **Match arms**: Each arm is treated as an independent branch (like if/else).
  A variable used in only one arm is recognized as last-use within that arm.

This analysis is a foundation for borrow checking -- it already answers "is variable X
alive at program point P?" and handles aliasing. The borrow checker extends this with
"is variable X borrowed (shared or mutable) at program point P?"

---

## Part II: Borrow Checking

### Design Philosophy

The borrow system follows a **mutable-XOR-shared** discipline, similar to Rust but
with key differences:

1. **Fully inferred within functions.** No lifetime annotations. The compiler sees the
   full function body and deduces borrow scopes from liveness analysis.
2. **Inferred across functions (Phase 2).** The compiler analyzes callee bodies to
   determine which parameters are borrowed by the return value. For precompiled
   libraries, this metadata is exported (see [Section 9](#9-borrow-metadata-export)).
3. **Soft by default, strict opt-in.** Violations are warnings in the default profile,
   errors in `@noalloc` / `@safe` contexts. This lets existing code compile while
   nudging toward safety.
4. **Escape hatch.** `# tpy: unchecked-borrows` or `unsafe` blocks for code where the
   programmer knows better.

### Core Rule

**While any shared reference to storage X exists, X cannot be mutated. While a mutable
reference to X exists, no other reference (shared or mutable) can exist.**

In TPy's pointer-variable model:

| Operation | Borrow created |
|-----------|---------------|
| `y = x` (non-value type) | Shared borrow of `x`'s storage by `y` |
| `p = Ptr(x)` | Shared borrow of `x`'s storage by `p` |
| `for item in items` | Shared borrow of `items` for the loop body |
| `v = obj.field` (non-value ref) | Shared borrow of `obj`'s storage by `v` |
| `v = items[i]` (non-value ref) | Shared borrow of `items`'s storage by `v` |
| `x.field = val` | Mutable access to `x`'s storage |
| `items.append(val)` | Mutable access to `items`'s storage |
| `items[i] = val` | Mutable access to `items`'s storage |

A **borrow conflict** occurs when:
- A mutable access happens while a shared borrow is live
- A shared or mutable borrow is created while a mutable borrow is live

**Value types** (`Int32`, `bool`, `float`, `Char`, `Float32`) are exempt -- they copy
on assignment, so no aliasing occurs.

### What Falls Out of This

The borrow rule is a **unifying principle** that replaces several ad-hoc analyses:

| Problem | Current approach | Borrow-based approach |
|---------|-----------------|----------------------|
| Container mutation during iteration | Nothing | `for x in items` borrows `items` shared; `items.append()` is mutable access -> conflict |
| String view dangling from containers | Allowlist in `is_view_compatible_source` | `v = arr[0]` borrows `arr` shared; `arr[0] = "new"` is mutable access -> conflict; so view is safe while borrow is live |
| For-loop const-ref binding | Always copies | If loop var borrow is shared (no mutation), codegen emits `const auto&` |
| Ptr dangling after container realloc | Nothing | `p = Ptr(items[0])` borrows `items`; `items.append()` -> conflict |
| Iterator invalidation | Nothing | Same as container mutation |
| Use-after-move | `consumed_vars` set | Move ends the borrow; subsequent use is a conflict |

### 5. Ptr Narrowing After `is not None`

**Status**: Done.

When a `Ptr[T]` or `Ptr[readonly[T]]` variable is guarded by an `is not None` check,
the compiler adds it to `non_null_ptr_vars`, skipping `deref_check()` inside the
guarded scope. Works at three narrowing sites:

- **`if p is not None:`** -- non-null in the then-branch
- **`if p is None: return`** -- non-null after the early return (via branch merge
  with terminated then-branch)
- **`assert p is not None`** -- non-null for the rest of the function
- **`while p is not None:`** -- non-null inside the loop body

Supports negation (`not (p is not None)`) and `and`/`or` composition. Branch
merging uses intersection (conservative -- non-null only if all paths agree).

```python
def process(p: Ptr[Point]) -> Int32:
    if p is not None:
        return p.x      # skip deref_check (p proven non-null)
    return Int32(0)

def with_assert(p: Ptr[Point]) -> Int32:
    assert p is not None
    return p.x           # skip deref_check
```

**Files**: `sema/narrowing.py` (`condition_ptr_null_facts`, `_ptr_null_facts`),
`sema/statements.py` (if/while/assert application sites).

### 6. Intra-Function Borrow Checking

**Effort**: L

This is the core of the borrow system. For each function body, the compiler tracks
which variables hold borrows of which storage, and flags conflicts.

**Representation**: A general borrow map replaces the ad-hoc `loop_borrowed_vars`.
The mutable version on `AnalysisContext` is `dict[str, set[str]]` (storage_name ->
set of borrower names). The immutable version in `FlowFacts` is
`frozenset[tuple[str, str]]` (storage, borrower) pairs. A special borrower name
`__for_iter` represents the implicit for-loop iterator borrow (no named variable).

**Merge policy**: UNION -- a borrow exists after a branch if it exists in either
path (conservative, same as `loop_borrowed_vars` today).

**Borrow lifetime**: A borrow `(storage, borrower)` is removed when:
- `borrower` is reassigned (no longer aliases storage)
- `storage` is reassigned (old storage gone -- borrowers now dangle, separate check)
- Branch merge via `FlowFacts.restore()` (borrows from the analyzed branch are
  discarded; only borrows saved before the branch survive unless UNION merge
  re-introduces them)

Note: explicit scope-exit cleanup is not implemented. Python has no block scoping,
so a variable declared inside an `if` block is visible after it. The `FlowFacts`
save/restore mechanism covers branch boundaries, which handles the practical cases.

**Implementation steps**:

**Step 6.1: Borrow map infrastructure.** Replace `loop_borrowed_vars` with the
general borrow map. For-loop iteration becomes `borrows["items"].add("__for_iter")`.
All existing 6a conflict detection keeps working, just uses the new structure.
Existing tests pass, no new behavior.

**Step 6.2: Assignment borrows.** Track `y = x` where `x` is non-value type and `y`
is a lvalue alias (from prescan's `alias_sources`). Creates borrow
`("x", "y")`. Removed on reassignment of `y`. This is the core new tracking.

**Step 6.3: Ptr/subscript/field borrows.** Track `p = Ptr(x)` as `("x", "p")`,
`v = items[i]` as `("items", "v")`, and `v = obj.field` as `("obj", "v")` for
non-value types.

**Step 6.4: Conflict detection at all mutation points.** Extend conflict checks
beyond method calls and `del` (6a) to: field assignment (`obj.field = val`) and
augmented assignment on borrowed storage. Subscript assignment (`items[i] = val`)
is in-place and does not reallocate, so it does not conflict with element borrows.

**Step 6.5: Function parameter mutation detection.** Detect passing borrowed storage
to non-`@readonly` / non-`@pure` function parameters.

**Step 6.6: Span/StrView slice borrow tracking.** Slicing a container (`items[1:3]`)
produces a `Span[T]` or `StrView` -- value types that are semantically borrows of
the source. These are now registered as `BorrowKind.ELEMENT` on the source container,
so structural mutations (append, del, etc.) trigger warnings. Subscript write is
in-place and does not reallocate, so it does not conflict. Currently hardcoded for
built-in view types; user-defined view types would need a `@view` marker or similar
mechanism to opt in.

**Step 6b/6c: Codegen benefits.** When the borrow checker proves no mutation of a
container during a borrow scope, codegen can use `const auto&` (for-loops),
`string_view` (container subscripts), and skip `deref_check` (Ptr borrows).

**Key design questions**:

**Q: How to determine if a function call mutates a borrowed variable?**

Three categories:
1. **Method on the borrowed object** (`items.append()`): mutating unless the method
   has `@readonly` on self AND is `@pure` (no side effects). `@readonly` alone is
   insufficient -- a `@readonly` method could stash a reference that is later used to
   mutate.
2. **Free function receiving the object** (`process(items)`): mutating unless the
   parameter is `@readonly` AND the function is `@pure`.
3. **Any other call** (`do_something()`): could mutate anything it has access to
   (globals, closures). Only `@pure` functions are provably safe.

The pessimistic default (any non-`@pure` call that *could* reach the storage is a
potential mutation) is sound but noisy. In practice, we start with the common
patterns:

- Direct method calls on borrowed objects (most important -- catches iteration
  mutation)
- Direct subscript/field writes on borrowed objects
- Passing borrowed objects to non-`@readonly` parameters

Indirect mutation through globals/closures is deferred to `@pure` enforcement.

**Q: What about `self` in methods?**

`self` is always a mutable reference. Fields accessed through `self` create borrows
of `self`'s storage. `self.items.append()` is a mutable access through self. This
is consistent -- the caller holds the borrow of the object, and the method operates
within that borrow scope.

**Q: What about nested borrows?**

`v = items[0]; v.field` borrows `items`, then accesses through the borrow. Nested
borrows are tracked transitively: `v` borrows `items`, and structural mutation of
`items` (append, del, etc.) conflicts with `v`'s borrow. Subscript write
(`items[0] = ...`) is in-place and does not conflict.

#### 6a. Container Mutation During Iteration

First concrete application of the borrow rule:

```python
for item in items:           # shared borrow of `items`
    print(item)              # ok: read through borrow
    items.append(item * 2)   # ERROR: mutable access while borrowed
    process(items)           # ERROR: items passed to non-@pure function
    len(items)               # ok: len is @pure
```

The `for` loop creates a shared borrow of the iterable for the duration of the loop
body. Any operation that could mutate the iterable is a conflict:

- Method calls on the iterable that aren't `@pure`
- Subscript assignment on the iterable
- Passing the iterable to a function where the parameter isn't `@readonly` on a
  `@pure` function
- `del iterable[k]`

**Scope**: Start with direct variable names (the iterable is a `TpyName`). Field-path
iterables (`self.items`) and aliased mutation are Phase 2 (cross-function inference).

**Current limitations** (6a implementation):

- Only method calls known to invalidate iterators (`LIST_ITER_INVALIDATING`,
  `DICT_MUTATION_METHODS`) and `del` are detected. Subscript assignment
  (`d[k] = v`) is not warned -- it is element replacement for sequences but may
  insert new keys for mappings. Distinguishing structural mutation from element
  replacement generically (without hardcoding types) requires the general borrow
  infrastructure (Phase 2).
- Passing a borrowed iterable to a non-`@pure` function is not yet detected.
- Only simple `TpyName` iterables are tracked (not `self.items`, `obj.field`,
  or aliased names).

#### 6b. For-Loop Const-Ref Binding

When the loop variable's borrow is provably shared (no mutation, no escape):

```python
for x in items:          # x borrows each element (shared)
    total += x.value     # read-only access
    # x is never mutated, never escapes -> const auto&
```

Codegen emits `const auto& x` instead of `T x`. Only for non-value types where the
copy is expensive (`BigInt`, records, `std::string`).

The borrow checker already tracks whether `x` is mutated (field write, passed to
mutating function). If not, the binding is `const auto&`.

#### 6c. String View Extension to Containers

With borrow tracking, `is_view_compatible_source()` can be extended:

```python
names: list[str] = ["alice", "bob"]
x = names[0]             # shared borrow of `names` by `x`
print(x)                 # ok: read
# names is not mutated while x is live -> x can be string_view
```

If the borrow checker proves `names` is not mutated between the assignment and the
last use of `x`, the string can be a `string_view` instead of a copy.

Incremental rollout:
- **Phase A (Done)**: `Array[T, N]` subscript -- no reallocation risk, only element mutation
- **Phase B (Done)**: Record field access -- no reallocation risk
- **Phase C (Done)**: `list[T]` / `dict[K, V]` -- source-mutation tracking falls back to `std::string` if the source is mutated after the borrow (see 6c+)

**Implementation**: Since `PendingStrType.is_value_type()` returns True, the main borrow
system does not track string variables. A separate `str_source_borrows` dict on
`SemanticContext` maps storage variable names to `str_var_id` sets. When the source
storage is mutated (subscript/field assignment, aug-assign, del, variable reassignment,
or passed to a non-readonly function), `mark_str_borrowers_mutated()` sets the
`source_mutated` flag on `StrVarInfo`, causing resolution to fall back to `std::string`.

### 7. `@pure` Annotation

**Effort**: S-M

A function marked `@pure` has no observable side effects: no mutation of non-local
state, no I/O, deterministic output for the same inputs. `@pure` subsumes `@readonly`
(which only promises no mutation of `self`). Heap allocation is permitted -- it is
not considered an observable side effect since the returned object is fresh and owned.

```python
@pure
def square(x: Int32) -> Int32:
    return x * x
```

**Why the borrow checker needs it**: Without `@pure`, any function call is a potential
mutation of any reachable state. The borrow checker must assume the worst. `@pure`
gives the checker a sound escape: "this call doesn't mutate anything."

**Builtins**: Many builtins are naturally `@pure`: `len`, `range`, `min`, `max`,
`abs`, `hash`, `str`, `int`, `bool`, `float`, `isinstance`, `type`, `chr`, `ord`.
These can be marked `@pure` in their module definitions.

**Enforcement**: In Phase 1, `@pure` is trusted (no enforcement -- like `@readonly`
before enforcement was added). Phase 2 adds verification: the function body is checked
for mutation of non-local state, I/O calls, and calls to non-`@pure` functions.

**Open question**: `@pure` may be unnecessary. The borrow checker currently uses it
identically to `@readonly` (the only check site is `_check_borrow_arg_conflicts`).
The stronger guarantee (no non-local mutation) matters for cross-function aliasing,
but step 8 (cross-function inference) can infer this automatically since the compiler
always has source. An unenforced `@pure` is a liability (allows bugs); enforcing it
is complex for little gain if inference can replace it. **Decision**: either implement
7b enforcement or drop `@pure` entirely in favor of automatic inference from step 8.

**Interaction with effects**: `@pure` = `@readonly` + no I/O + no mutation of non-local
state. `@noalloc` is orthogonal -- `@pure @noalloc` gives the strongest guarantee
(pure + no heap allocation, suitable for hot paths). See `FEATURE_ROADMAP.md`
Section IV for the general effect system.

### 8. Cross-Function Borrow Inference

This section is split into two sub-steps:

#### 8a. Parameter Mutation Inference

**Effort**: M &nbsp; **Status**: Phase 1 (direct mutations) working; Phase 2 (graph propagation) planned.

**Problem**: When a function is called while a borrow is active, the borrow checker
needs to know whether the callee mutates the borrowed argument. Without this
information, it must warn conservatively -- producing false positives on safe code:

```python
def sum_items(items: list[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

def caller() -> None:
    data: list[Int32] = [1, 2, 3]
    for x in data:
        print(sum_items(data))  # false positive: sum_items doesn't mutate data
```

**Key insight**: Mutation detection within a function body is already solved by the
`readonly[T]` enforcement infrastructure. The compiler already detects field writes,
subscript writes, non-readonly method calls, augmented assignments, and deletes on
any expression. We just need to **collect** which of those targets are parameters
and **propagate** the facts across function boundaries.

**Implementation: two-phase per-module approach**

Phase 1 runs during sema; Phase 2 runs once after the module's sema completes.

**Phase 1 -- Local fact collection (during sema)**

During body analysis, collect two kinds of facts per function (no transitive
propagation):

1. **Direct mutations** (`direct_mutated_params: set[int]`): Parameter indices
   directly mutated in the function body. Mutation is detected at the same sites
   the `readonly[T]` enforcement already checks:
   - Field writes: `param.field = ...` (root is param)
   - Subscript writes: `param[i] = ...` (root is param)
   - Non-readonly method calls: `param.append(...)` (already resolved during sema)
   - Augmented assignments: `param.field += ...` (root is param)
   - Deletes: `del param[i]` (root is param)
   - Plain name rebinding (`param = x`) is NOT mutation -- it just rebinds the local.

   **Additional patterns that require mutable ref (all now tracked)**:
   - **Address-taking**: `Ptr(param)` and `Ptr(param.field)` / `Ptr(items[i])` require
     `T&` because `&param` yields `T*`, not `const T*`. (8a.2 -- Done)
   - **Tuple ref packing**: `return (x, param)` where the tuple slot type is `T&`
     (e.g. `tuple[int, Point]` with a reference element) requires `T&` at the
     source. (8a.3 -- Done)
   - **`T -> Optional[T]` coercion** for pointer-repr optionals: codegen takes `&(param)`,
     so `param` must be `T&`.
   - **Mutable Span coercion**: `Array -> Span[T]` calls `as_mut_span()` requiring
     non-const source.
   All are tracked in Phase 1 via `addr_taken_roots()` (see `context.py`).
   - **For-loop iteration over protocol/TypeParamRef params**: `tpy::iter_adapt(T& iter)`
     requires `T&`, so any param iterated with `for x in param` where `param` has a
     protocol or TypeParamRef type is marked mutated. (8a.4 -- Done, part of for-loop
     mutation tracking in `sema/statements.py`)

2. **Call edges** (`call_edges: list[CallEdge]`): For each call to a user function
   or method, record which caller parameter flows into which callee parameter:
   ```python
   @dataclass
   class CallEdge:
       callee_fi: FunctionInfo
       param_map: dict[int, int]   # callee_param_idx -> caller_param_idx
   ```
   Only non-value-type arguments rooted in a parameter name are recorded (value
   types are copies and can't propagate mutation).

After each function body is analyzed, store `direct_mutated_params` and
`call_edges` on the function's metadata. No `mutated_params` is resolved yet.

**Phase 2 -- Call graph propagation (post-sema, per module)**

After all functions in the module are analyzed:

1. **Build call graph**: Nodes are functions/methods in the module. Edges come from
   the collected `call_edges`. Imported callees are leaf nodes with already-resolved
   `mutated_params` (from dependency-order compilation).

2. **Topological sort** (ignoring back edges): Process callees before callers where
   possible.

3. **Propagate**: For each function in topological order, compute:
   ```
   mutated_params = direct_mutated_params
     U { param_map[j] | for each call_edge where callee.mutated_params includes j }
   ```
   Imported callees with resolved `mutated_params` are ground truth. Callees with
   `mutated_params = None` (unresolved, e.g. from cross-module cycles) trigger
   conservative treatment -- assume all non-value-type params are mutated.

4. **Cycles (back edges)**: The `mutated_params` lattice is monotone (sets grow by
   union, never shrink), so fixed-point iteration is guaranteed to converge. Iterate
   until no new params are added to any set in the cycle. As a safety bound, stop
   after `max(total_params_in_cycle, 3)` iterations and conservatively mark remaining
   cycle participants.

5. **Store results**: Set `fi.mutated_params` on each function's `FunctionInfo`.

**Edge cases to handle**:

- **Alias chains**: `alias = param; foo(alias)` -- when recording call edges, resolve
  argument roots through the borrow tracker's alias chain (`effective_storage`) to
  trace back to the original parameter name.
- **Param rebinding**: `param = new_value; param.append(...)` -- once a parameter
  name is rebound, subsequent mutations through that name target the new local value,
  not the caller's argument. Track rebound param names and exclude them from
  `direct_mutated_params` after the rebinding point.
- **Field sub-objects**: `foo(param.field)` where `foo` mutates its argument -- this
  mutates a sub-object of `param`, not `param`'s structure (no element invalidation).
  Currently treated conservatively (marks `param` as mutated). Future refinement could
  distinguish structural mutation (append, del) from sub-object mutation.

**Consumers** (checked after Phase 2):

- `_check_borrow_arg_conflicts`: If `fi.mutated_params` is resolved and the specific
  parameter is not in the set, the call is safe -- no borrow warning.
- `_check_loop_var_arg_mutation`: Same check -- if the callee is known not to mutate
  the parameter, passing a loop-iterated container there doesn't force mutable binding.
- `gen_params_with_protocols` (8a.4 -- Done): Protocol-typed (static and `@dynamic`) and
  TypeParamRef params not in `mutated_params` emit `const T_x&` / `const Base&`.

**Why per-module, not global**: A global call graph across all modules would handle
cross-module cycles more precisely, but it forces a synchronization barrier (all
modules must finish sema before any can proceed to warnings). This prevents parallel
compilation. Per-module propagation lets each module complete independently, only
blocking on its direct imports. Cross-module cycles are rare and are a code smell --
conservative treatment is acceptable.

**No new user-facing syntax**: Fully automatic, invisible to the programmer. No
`@pure`, `@readonly`, `Mut[T]`, or lifetime annotations needed on user functions.
Builtins and `@native` functions declare mutation facts in their module definitions
(existing `is_readonly` mechanism).

**8a.5 -- Precise element-ref mutation (Done)**:

Previously, `v = items[i]` immediately marked `items` as mutated (conservative:
`std::vector<T>& items`), even when `v` was never written through.

With 8a.5:
- `v = items[i]` records the ELEMENT borrow in the tracker but defers
  `mark_param_mutated`. The source container is not immediately marked.
- When `v` is written through (field write `v.field = x`, subscript write `v[i] = x`,
  or passed to a mutating callee), `mark_param_mutated(v)` traces back through the
  borrow chain via `effective_storage_through_borrows(v)` to find `items` and marks it.
- Call-edge recording (`_record_mutation_call_edges`) also uses
  `effective_storage_through_borrows` so that `mutating_callee(v)` where
  `v = items[i]` records a Phase 2 call edge mapping back to `items`.
- Codegen for element borrow locals emits explicit `const T& v` or `T& v` based on
  whether the source container is in `const_ref_params` (set by `FunctionGenerator`
  before each body gen from the function's `mutated_params`). Const propagates
  transitively: if `v` is `const T&`, an alias `w = v` also gets `const T&`.
- Any ALIAS/FIELD borrow chain rooted at an ELEMENT borrow is also deferred
  (`w = v`, `x = w`, ... where `v = items[i]`): `BorrowTracker.is_deferred_borrow`
  follows the chain recursively so arbitrarily deep read-only alias chains preserve
  `const T&` for the source container.

#### 8b. Return-Value Borrow Contracts

**Effort**: L
**Status**: Done.

When a function returns a reference derived from a parameter, the compiler needs to
know this at the call site to maintain the borrow chain:

```python
def get_first(items: list[Point]) -> Point:
    return items[0]     # return borrows from `items`

def caller() -> None:
    data = [Point(1, 2), Point(3, 4)]
    first = get_first(data)   # `first` borrows `data` (transitively)
    data.append(Point(5, 6))  # conflict: `data` is borrowed by `first`
```

**Inference approach**: The compiler analyzes `get_first`'s body and determines that
the return value borrows from parameter `items`. This is encoded as a **borrow
contract** on the function signature:

```
get_first: return borrows param[0]
```

At the call site, the compiler translates this: `first = get_first(data)` means
`first` borrows `data`.

**Inference rules** (inferred from bodies, no user annotations):

1. If the return is a parameter or derived from a parameter (field access, subscript,
   Ptr construction) -> return borrows that parameter.
2. If the return is an `Own[T]` / rvalue -> no borrow (fresh value).
3. If the return is from a nested call -> recursively apply that call's contract.
4. If ambiguous (multiple parameters could be the source) -> conservative: return
   borrows all reference-type parameters.

**When bodies aren't available** (precompiled libraries, builtins): borrow contracts
must be declared explicitly or loaded from metadata. See
[Section 9](#9-borrow-metadata-export).

**Implementation (done)**:

- `FunctionInfo.return_borrows_from: Optional[frozenset[int]]` -- param indices
  whose storage the return value borrows from. `-1` = self (methods). `None` = not
  yet analyzed (stubs, forward declarations).
- Inference: set during body analysis in `sema/analyzer.py` alongside
  `mutated_params`. `return` statements call `ctx.mark_param_returned(root)` for
  each root in `addr_taken_roots(return_expr)` (rules 1/4). Rule 3 (transitive):
  when returning the result of a call whose `return_borrows_from` is known, the
  source args are also marked via `mark_param_returned`.
- Call-site registration: in `TpyVarDecl` handler in `sema/statements.py` for both
  first declarations and reassignments, and in the for-each handler when the iterable
  is a call. When `fi.return_borrows_from` is non-empty, the result is registered as
  a `BorrowKind.ELEMENT` borrower of the source arg. Enables `effective_storage`
  chain resolution and conflict detection for structural mutations.
- `FunctionInfo.structural_mutated_params: Optional[frozenset[int]]` -- separates
  structural mutations (append/insert/clear/del/etc.) from element-ref taking
  (`a = items[0]`). Phase 1 collects `direct_structural_mutated_params`, Phase 2
  (`mutation_propagation.py`) propagates transitively. The borrow conflict checker
  (`_check_borrow_arg_conflicts` in `calls.py`) uses `structural_mutated_params`
  when available, falling back to `mutated_params` for external functions. This
  allows 8b borrows to use `BorrowKind.ELEMENT` without false positives.

### 9. Borrow Metadata Export

**Status**: Not planned near-term. Documented here to avoid painting into a corner.

Separate compilation is not planned for the foreseeable future -- the compiler always
has source available and can infer borrow contracts by analyzing function bodies
directly. However, the design should not preclude exporting borrow metadata later
(e.g., for precompiled libraries or a future package ecosystem).

**What would need exporting** (per function/method):

```
function get_first(items: list[Point]) -> Point
  mutated_params: {}
  return_borrows_from: {0}       # return borrows param[0]

function add_item(items: list[Int32], val: Int32) -> None
  mutated_params: {0}            # mutates param 'items'
  return_borrows_from: {}
```

- **`mutated_params`**: Indices of parameters that may be mutated.
- **`return_borrows_from`**: Which parameter indices the return value borrows from.

**Design constraint**: The internal representation of borrow contracts (however they're
stored during compilation) should be serializable. As long as the inferred contracts
are structured data rather than implicit in the analysis pass, exporting them to a
`.tpyi` sidecar file or embedded metadata is straightforward when the need arises.

**For builtins and C++ interop**: Borrow contracts are declared in the module
definitions (`tpyc/modules/*.py`). Example: `list.__getitem__` returns a borrow
of `self`. This is already implicitly true (the sema knows `list[i]` returns a
reference) but needs to be formalized as structured data.

**Key principle**: Within a project (the expected use case), everything is inferred
from source. Explicit annotations or metadata export only matter if/when precompiled
library distribution becomes a goal.

### 10. Thread Safety (Send/Sync)

**Effort**: S (markers), M (enforcement)

Types are automatically classified based on their fields:

- **Send**: Safe to transfer ownership across threads. All value types are Send.
  Records are Send if all fields are Send. `Ptr[T]` is not Send (raw pointer).
  `Box[T]` is Send if `T` is Send.
- **Sync**: Safe to share references across threads. Immutable types are Sync.
  `Ptr[readonly[T]]` is Sync if `T` is Sync. Mutable containers (`list`, `dict`)
  are not Sync.

**Why this relates to borrows**: The borrow system ensures that within a single
thread, mutable and shared references don't coexist. Send/Sync extends this across
threads: Send means "safe to move to another thread" (no dangling references left
behind), Sync means "safe to share a reference across threads" (no data races).

**Implementation plan**:

1. Add `is_send()` / `is_sync()` methods to `TpyType` (mirrors existing
   `is_value_type()`).
2. Auto-derive from fields: a record is Send/Sync if all its fields are.
3. Store markers on type registration (like `is_value_type`, `is_copyable`).
4. Enforcement comes with concurrency features (channels, shared references).
   Types passed through `Channel[T]` must be Send. References shared via
   `Arc[T]` or similar must be Sync.

**Early adoption**: Even before concurrency, Send/Sync markers enable warnings like
"this type contains a raw Ptr; it won't be usable in concurrent code." This helps
users design thread-safe types from the start, avoiding Rust's Sendable retrofit.

---

## Part III: Value Provenance

Value provenance tracks constraints on scalar values (integers, floats) through the
program. It shares flow-analysis infrastructure with the borrow checker (branch-aware
tracking, save/restore/merge) but operates on value ranges rather than
reference liveness.

### 11. Integer Range Tracking

**Effort**: M (loop-bounded patterns), L (general interval arithmetic)

Track provable `[lo, hi]` ranges for integer variables. Sources of range information:

| Source | Range |
|--------|-------|
| `x: Int32 = 0` | `[0, 0]` |
| `for i in range(n)` | `[0, n-1]` (if n > 0) |
| `assert i > 0` | `[1, INT_MAX]` |
| `assert i >= 0` | `[0, INT_MAX]` |
| `if i < len(arr)` (true branch) | `[lo, len(arr)-1]` |
| `i = len(arr)` | `[0, INT_MAX]` (len is non-negative) |
| `i += 1` | `[lo+1, hi+1]` (with overflow check) |
| `i & 0xFF` | `[0, 255]` |

**Branch merging**: Union of ranges at merge points (`[min(lo1,lo2), max(hi1,hi2)]`).
Loop widening: if a range grows across iterations, widen to `[lo, +inf]` to ensure
convergence.

#### 11a. Bounds Check Elision

The primary consumer. When `arr[i]` is accessed and `i` is provably in
`[0, len(arr))`, skip `normalize_index` and use direct access:

```python
for i in range(len(arr)):
    x = arr[i]              # i in [0, len(arr)-1] -> skip bounds check

i: Int32 = 0
while i < len(arr):
    x = arr[i]              # i in [0, len(arr)-1] -> skip bounds check
    i += 1
```

These are the two most common patterns in hot loops. The `range(len(arr))` pattern
is especially common in HFT code.

**Codegen**: When bounds-safe, emit direct `arr[i]` in C++ (bypassing `normalize_index`).

**Implementation notes** (Done):
- `ValueRange` class in `sema/value_range.py`: frozen dataclass with `[lo, hi]` +
  `non_zero` flag + symbolic `hi_len_of` (container name for `len(x)-1` bound).
- `value_ranges: dict[str, ValueRange]` on `SemanticContext`, saved/restored in
  `FlowFacts` and `InitTracker`.
- `condition_range_facts()` on `NarrowingTracker` extracts facts from comparisons,
  `!=`, assert, etc. Applied at if/while/assert branch points.
- `for i in range(len(arr))` detected in `statements.py`, sets
  `ValueRange(lo=0, hi_len_of="arr")` for the loop variable.
- `bounds_safe` flag on `TpySubscript` and `divisor_non_zero` flag on `TpyBinOp`
  (set by sema, read by codegen).
- Division elision: `div_floor<T>` / `mod_floor<T>` runtime functions skip zero-check
  but keep Python floor-division/modulo semantics and overflow check.
- Test annotations: `# tpyc: bounds_safe(arr)` / `# tpyc: bounds_checked(arr)` for
  subscripts; `# tpyc: div_safe(b)` / `# tpyc: div_checked(b)` for division/modulo.
- Literal range tracking: `i: Int32 = 0` sets `ValueRange.from_literal(0)`, enabling
  while-loop bounds elision. Combined with `while i < len(arr)` condition facts, gives
  `ValueRange(lo=0, hi_len_of="arr")` which satisfies `bounds_safe`.
- Augmented assignment (`i += 1`) invalidates range facts for soundness -- prevents
  stale bounds after the increment within the loop body.

#### 11b. Safe Unsigned Cast

When `assert i > 0` or a comparison proves `i >= 0`, casting to an unsigned type
(`UInt32`, `UInt64`) can skip the negative-value check:

```python
assert offset >= 0
ptr = base.offset(UInt64(offset))  # safe: offset proven non-negative
```

**Implementation**: When a cast to unsigned is requested and the source range has
`lo >= 0`, skip the sign check. Otherwise, emit a runtime check (current behavior).

**Done.** Implemented in `calls.py:_check_cast_safe()`. When a signed->unsigned
cast has the source proven non-negative (via range tracking) and the target is at
least as wide as the source (no narrowing risk), sema replaces the `FunctionInfo`
template with `static_cast<T>()` instead of `tpy::int_cast_check<T>()`. Test
annotations: `# tpyc: cast_safe(UInt32)` / `# tpyc: cast_checked(UInt32)`.

#### 11c. Condition and Assert-Derived Range Facts

Range information enters the system through boolean conditions at branch points and
assertions. This reuses the existing `NarrowingTracker` infrastructure, which already
handles `and`/`or` composition and branch merging for type facts. Range facts use the
same structure:

- **Comparisons**: `if x > 0:` establishes `x in [1, MAX]` in the true branch
- **Logical composition**: `if x > 0 and x < 100:` establishes `x in [1, 99]`
- **Assertions**: `assert x >= 0` establishes `x in [0, MAX]` for the rest of the
  function (or until `x` is reassigned)

The existing `NarrowingTracker` already handles type narrowing from `isinstance` and
`is None` checks (done). Extending it to carry range facts alongside type facts is
a natural addition -- same branch merging, same save/restore, same invalidation on
reassignment.

#### 11d. Bounds Elision for User Types (Future)

Currently bounds elision only applies to built-in containers (`Array`, `list`, `Span`)
where the compiler knows `0 <= i < len(c)` guarantees safe access. For user-defined
types with `__getitem__`, the compiler cannot make this assumption -- the method body
contains its own bounds check that the compiler cannot see through.

**Proposed approach: dual `__getitem__`**

The user provides an explicit unchecked fast path:

```python
class RingBuffer:
    def __getitem__(self, i: Int32) -> Int32:
        return self._data[i]  # checked (default)

    def __getitem_unchecked__(self, i: Int32) -> Int32:
        return self._data.unsafe_get(i)  # no bounds check
```

When the compiler proves `0 <= i < len(buf)`, it calls `__getitem_unchecked__` instead
of `__getitem__`. No magic inlining, no fragile optimizer dependency -- the user
controls exactly what the fast path does.

If `__getitem_unchecked__` is not defined, the compiler always uses the checked path
(current behavior, safe default).

---

## Interaction with Existing Features

### `@readonly`

`@readonly` on a method means "doesn't mutate self." In the borrow system, a
`@readonly` method on a borrowed object is allowed (it doesn't create a mutable
access). However, `@readonly` alone is NOT sufficient to prove the call is safe
for borrowed storage -- a `@readonly` method could stash a reference to self in a
global, and another call could mutate through that global. Only `@pure` is sufficient.

For practical purposes, `@readonly` + no arguments that alias the borrowed storage
is treated as safe in Phase 2. Full soundness requires `@pure`.

### `@noalloc`

Borrow checking makes `@noalloc` more useful. In `@noalloc` functions:
- Borrow violations are always errors (not warnings)
- All borrows are guaranteed to be stack-based (no heap allocation for borrow tracking)
- Bounds check elision is more aggressive (runtime checks cost latency)

### `Own[T]`

`Own[T]` in a return position means "this is a fresh value, not a borrow." The borrow
checker uses this: `return Own[T](...)` creates no borrow relationship at the call
site. `return x` (without Own) might borrow from a parameter.

`Own[T]` parameters use `T&&` in C++ (rvalue reference) for non-value types, enabling
zero-cost ownership transfer without move constructor overhead. Value types use `T` by
value (copy == move). Generic `Own[T]` with function-level type params uses
`std::type_identity_t<T>&&` to prevent forwarding-reference deduction; class-level
type params use plain `T&&`.

The compiler warns when an `Own[T]` param is never consumed (not stored in a field,
forwarded to another `Own[T]`, or returned). This is tracked flow-sensitively:
consumption must occur on all non-terminated paths through if/else branches, match
arms, and loops.

### Move Semantics

A move ends the source variable's borrow scope. After `y = std::move(x)`, `x` is
consumed and any borrows through `x` are invalidated. The borrow checker and
`consumed_vars` tracking work together.

---

## Open Design Questions

**Q: Should borrow violations be errors or warnings?**

Default profile: warnings (migration-friendly). `@safe` / `@noalloc`: errors. This
avoids breaking existing code while enabling strict mode for production HFT paths.
Over time, the default could tighten as the ecosystem matures.

**Q: How deep should nested borrow tracking go?**

`v = items[0].name` borrows `items` and `items[0]`. If `items[0]` is reassigned, `v`
dangles. Tracking all transitive borrows is sound but expensive. Start with one level
(direct container borrows) and extend as needed.

**Q: How to handle closures (when implemented)?**

Closures capture variables by reference. A closure that captures `x` creates a borrow
of `x` that lives as long as the closure. If the closure escapes (returned, stored),
this is a long-lived borrow that constrains mutation. This interacts heavily with
closure design (D1 in FEATURE_ROADMAP.md) and should be co-designed.

**Q: What about CPython compatibility?**

Borrow rules are a compile-time-only concept. They don't affect the generated Python
for CPython compatibility tests. A program that passes borrow checking produces the
same runtime behavior in both TPy and CPython.

---

## Test Coverage

Existing escape/safety tests:

| Area | Test Location | Count |
|------|---------------|-------|
| Pointer escape/dangling | `tests/cases/pointers/error_ptr_*`, `escape_hoist_*` | ~20 |
| Ptr non-null elision | `tests/cases/pointers/ptr_non_null_*` | 2 |
| Null deref panics | `tests/cases/pointers/panic_ptr_deref_*` | 3 |
| String dangling | `tests/cases/str/error_strview_dangling_*` | 2 |
| Tuple ref dangling | `tests/cases/tuple/error_tuple_ref_dangling` | 1 |
| Container dangling | `tests/cases/list/error_dangling_*`, `dict/error_dict_return_*` | 3 |
| Control flow dangling | `tests/cases/control_flow/error_ternary_dangling` | 1 |

New test areas needed:

- **Borrow conflicts**: mutation while borrowed (containers, records, pointers)
- **Iteration safety**: mutating iterable in loop body (various container types)
- **Const-ref binding**: snapshot tests showing `const auto&` for immutable loop vars
- **String view from containers**: string_view for array/record/list subscripts
- **`@pure` annotation**: marking functions pure, borrow checker trusting them
- **Integer ranges**: bounds check elision in for/while patterns
- **Send/Sync**: auto-derivation, type rejection at channel/sharing boundaries

---

## Future Extensions

### Return Borrow Contract Propagation for Forward References

Currently `return_borrows_from` is inferred during Phase 1 (body analysis). Two gaps
remain when the inner callee is a forward reference (defined after the outer function):

**Extension A -- Phase 2 contract propagation** (`return_borrows_from` via call graph):
When `get_first_wrapper` returns `get_first(items)` and `get_first` is defined after it,
Phase 1 sees `get_first.return_borrows_from = None` and leaves `get_first_wrapper`'s
contract empty. Phase 2 could propagate this after resolving all local contracts:

- Add `ReturnCallEdge(callee_fi, param_map)` recorded in Phase 1 when Rule 3 fires but
  the callee's contract is not yet known.
- Add a `return_borrows_from` propagation loop to `mutation_propagation.py`, parallel
  to the existing `mutated_params` / `structural_mutated_params` loops.
- After propagation, `get_first_wrapper.return_borrows_from = {0}` is correct for
  cross-module callers and exported metadata (Section 9).

Benefit: cross-module correctness and accurate metadata. Same-module call-site warnings
are unaffected (see Extension B).

**Extension B -- Deferred call-site borrow registration**:
Even with Extension A, a same-module caller that processes `x = forward_wrapper(items)`
during Phase 1 does not register the borrow -- the contract isn't known yet, and the
borrow tracker is discarded after Phase 1. The subsequent `items.append(...)` in the
same function then goes unchecked.

Fixing this requires either:
- A second Phase 1 analysis pass after Phase 2 resolves contracts (expensive), OR
- Storing pending borrow-registration records (analogous to `pending_borrow_checks` for
  mutation conflicts) that can be replayed after Phase 2 -- but this requires preserving
  enough per-call-site state to retroactively add borrows and re-run the conflict check.

Both approaches add significant complexity. Extension B is only needed for the narrow
pattern where a caller both uses a forward-wrapper's result AND structurally mutates the
source in the same function body. In practice, forward wrappers are library/utility code
and their callers are defined later, so Extension A alone provides correctness for the
common case.

### Declarative Borrow Contracts (`@may_reallocate`, `@return_borrows_from`)

Currently the compiler hardcodes which built-in container methods are *structural*
(can invalidate element references by reallocating or shifting storage) vs *in-place*
(write to an existing slot, no reallocation). This knowledge lives in name-based
lookup tables (`LIST_ITER_INVALIDATING`, etc.) inside `sema/methods.py`.

Moving built-in types to `.py` source files (Option B in the builtin migration
discussion) requires replacing these tables with declarative annotations:

```python
class list[T]:
    @may_reallocate  # invalidates all element refs -- conflicts with active borrows
    def append(self, item: T) -> None: ...

    @may_reallocate
    def insert(self, index: Int32, item: T) -> None: ...

    @may_reallocate
    def __delitem__(self, index: Int32) -> None: ...

    # no annotation = in-place, no reallocation, element borrows survive
    def __setitem__(self, index: Int32, value: T) -> None: ...

    @return_borrows_from(-1)  # return value borrows from self
    def __getitem__(self, index: Int32) -> T: ...
```

**Default for unannotated mutating methods**: conservative (treated as structural) --
annotate the safe cases explicitly, not the dangerous ones.

**Prior art**:
- **C++**: iterator invalidation rules are standard-prose only, entirely unenforced at
  compile time. No general annotation mechanism.
- **Rust**: does not distinguish -- any `&mut self` method invalidates all borrows.
  Simpler but more restrictive; the pattern `v = &items[0]; items[0] = x` is illegal.
- **No mainstream language** has a general user-facing annotation for structural vs
  in-place mutation. This is a gap TPy could fill usefully.

**Inference for user types**: `@may_reallocate` and `@return_borrows_from` on built-in
methods act as ground truth. User methods are inferred transitively using the same
Phase 1/2 propagation as `mutated_params` -- no explicit annotation needed on
well-structured wrappers:

```python
class Container:
    _items: list[Point]

    def add(self, p: Point) -> None:
        self._items.append(p)  # append is @may_reallocate
                               # -> inferred: add is @may_reallocate on self

    def first(self) -> Point:
        return self._items[0]  # __getitem__ has @return_borrows_from(-1)
                               # -> inferred: first has @return_borrows_from(-1)
```

**Dependency**: inferring that `add` is `@may_reallocate` on `self` (not just on
`_items`) requires the compiler to know that borrows of `self[0]` ultimately borrow
from `self._items`. This is the field-level borrow flow problem -- the same one
addressed by the View type borrow tracking item above. For a flat single-field wrapper
the inference is straightforward; for complex layouts (multiple backing fields,
conditional storage) it needs the full field-level annotation system.

Dependency chain:
```
@may_reallocate inference for user types
    -> field-level borrow flow tracking (View type borrow tracking)
    -> @return_borrows_from on __getitem__ (already designed, 8b)
```

**Enables**: user-defined containers (e.g. `tplib` types) to participate in borrow
checking with the same precision as built-ins, without compiler special-casing.
Prerequisite for the builtin-to-`.py` migration (Option B).

