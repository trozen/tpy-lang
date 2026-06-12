# Move Semantics Design for TurboPython

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Liveness analysis (backward dataflow last-use) | Done |
| **Phase 2** | Auto-move emission (`std::move` at last use for Tier 1 locals and `Own[T]` params) | Done |
| **Phase 3** | Unnecessary `copy()` warnings at last use | Done |
| **Phase 4** | `@nocopy` types (deleted copy, consumption tracking, move-only) | Done |
| **Phase 5** | Alias-aware liveness (borrow safety, detach-on-reassign) | Done |
| **Phase 6** | `Box[T]` as library type (`unique_ptr` wrapper, `Deref[T]`) | Planned |
| **Phase 7** | Consuming iteration (`__iter__(self: Own[Self])`, `OwnIter[T]`) | Planned -- see `docs/CONSUMING_ITERATION_DESIGN.md` |

## Motivation

TurboPython's current ownership model uses explicit `copy()` with warnings: when a
non-value-type lvalue is assigned to owned storage (field, container, `Own[T]` parameter),
the compiler warns that a copy will occur. This works well but leaves performance on the
table -- many copies could be moves if the compiler knew the source variable is dead
after the use.

Move semantics adds two things:
1. **Auto-move at last use** -- the compiler silently emits `std::move()` when a variable
   is used for the last time, eliminating unnecessary copies. No user annotation needed.
2. **`@nocopy` types** -- types where copying is deleted (e.g., `Box[T]` wrapping
   `unique_ptr`). Values can only be moved, and the compiler tracks consumption.

## Auto-Move at Last Use

### What gets auto-moved

| Variable kind | Auto-move? | Rationale |
|---|---|---|
| Tier 1 local (`T` value, rvalue init) | Yes | Owned, no aliasing |
| `Own[T]` parameter | Yes | Caller gave up ownership |
| Tier 2 local (`T&` reference) | No | Alias to someone else's data |
| Tier 3 local (`T*` pointer) | No | Complex, marginal benefit |
| Regular parameter (not Own) | No | Borrowed, not owned |
| Field access (`self.x`) | No | Would leave object inconsistent |

### Liveness analysis

The compiler performs last-use analysis to answer: "after this use of variable `x`, is
there any execution path where `x` is read again without being reassigned first?"

**Linear code:**
```python
dog = Dog("Rex")
kennel.add(dog)      # last use -> std::move(dog)
print("done")
```

**Branching -- both branches are last use:**
```python
dog = Dog("Rex")
if cond:
    kennel1.add(dog)  # last use on this path -> move
else:
    kennel2.add(dog)  # last use on this path -> move
# dog not used after if/else
```

**Only one branch uses it:**
```python
dog = Dog("Rex")
if cond:
    kennel.add(dog)   # last use -> move
# dog not used after if/else (even though else-branch didn't use it)
```

**Loop -- conservative, don't move:**
```python
dog = Dog("Rex")
for i in range(10):
    process(dog)      # NOT last use -- next iteration reads dog
```

**Per-iteration variable -- safe to move:**
```python
for i in range(10):
    dog = Dog(str(i))
    process(dog)      # safe: dog reassigned at top of next iteration
```

The analysis is conservative: if unsure, don't move. This preserves correctness -- a
missed move opportunity is just a copy (same as today), not a bug.

### Generated code

Before (current):
```cpp
auto dog = Dog("Rex");
kennel.add(dog);           // copy
```

After (with auto-move):
```cpp
auto dog = Dog("Rex");
kennel.add(std::move(dog)); // move
```

For `Own[T]` parameters:
```cpp
void adopt(Dog dog) {                // Own[Dog] -> Dog by value
    kennel.add(std::move(dog));      // move at last use
}
```

### Unnecessary copy() warning

When liveness analysis shows a variable is dead after a `copy()` call, the compiler
can warn that the copy is unnecessary:

```python
dog = Dog("Rex")
kennel.add(copy(dog))    # warning: copy() unnecessary, 'dog' is not used after this
# could just be: kennel.add(dog)  -- auto-moved
```

This also applies to containers:
```python
items.append(copy(x))   # warning if x is dead after this
```

## `@nocopy` Types

### Declaration

Types where copying is deleted are annotated with `@nocopy`:

```python
from tpy import nocopy

@nocopy
class Box(Generic[T]):
    __native__: "std::unique_ptr<{T}>"

    def __deref__(self) -> T:
        ...
```

`@nocopy` is primarily for library/infrastructure types (std lib, native wrappers), not
regular user code. Most user-defined records remain copyable.

### C++ codegen

`@nocopy` generates deleted copy operations:

```cpp
template<typename T>
struct Box {
    std::unique_ptr<T> __native;

    Box(const Box&) = delete;
    Box& operator=(const Box&) = delete;
    Box(Box&&) = default;
    Box& operator=(Box&&) = default;
};
```

### Consumption tracking

For `@nocopy` types, sema tracks whether a value has been consumed. Uses are classified:

**Consuming** (transfers ownership, value is gone after):
- Passing to `Own[T]` / by-value parameter
- Assigning to another variable: `other = box`
- Returning: `return box`

**Non-consuming** (borrows, value still valid):
- Method/field access via Deref: `box.speak()`, `box.name`
- Passing to non-Own parameter (by reference): `f(box)` where f takes `const T&`

```python
box = Box(Dog("Rex"))
box.speak()           # OK: non-consuming (deref)
print(box.name)       # OK: non-consuming (deref + field)
kennel.add(box)       # OK: consuming (first move)
kennel2.add(box)      # ERROR: 'box' already consumed on line N
```

### Interaction with Own[T]

For copyable types:
- `f(x)` where f takes `Own[T]`: copy-warning if lvalue, auto-move at last use
- `f(copy(x))`: explicit copy, no warning

For `@nocopy` types:
- `f(x)` where f takes `Own[T]`: auto-move (must be last consuming use)
- `f(copy(x))`: ERROR -- copy() on @nocopy type is a compile error
- `f(Box(Dog()))`: rvalue, direct move, no consumption tracking needed

### Reassignment of @nocopy types

Reassigned `@nocopy` locals use plain `T` with move-assignment (no pointer indirection):

```python
box = Box(Dog("Rex"))
box = Box(Dog("New"))     # move-assignment, old value destroyed
```

Generated C++:
```cpp
auto box = Box<Dog>(Dog("Rex"));
box = Box<Dog>(Dog("New"));       // move-assignment
```

This is a 4th variable tier in the variable model:

| Tier | Type | Condition | C++ |
|------|------|-----------|-----|
| 1 | `T` value | rvalue init, not reassigned | `auto x = expr;` |
| 2 | `T&` reference | lvalue init, not reassigned | `auto& x = expr;` |
| 3 | `T*` pointer | reassigned, copyable | `T* x = &slot;` |
| 4 | `T` move-local | reassigned, @nocopy | `auto x = expr;` (move-assign) |

The decision in `_needs_indirection()` adds: if the type is `@nocopy` and the variable
is reassigned, use Tier 4 (plain T, no indirection) instead of Tier 3 (pointer).

## Interaction with Existing Features

### Own[T]

`Own[T]` continues to mean "ownership transfer" in function signatures. Auto-move makes
it more ergonomic:

```python
# Before: copy warning on lvalue
def adopt(dog: Own[Dog]) -> None: ...
adopt(my_dog)           # warning: implicit copy (my_dog is lvalue)
adopt(copy(my_dog))     # OK: explicit copy

# After: auto-move when my_dog is dead
adopt(my_dog)           # no warning: auto-moved (if last use)
adopt(copy(my_dog))     # warning: unnecessary copy (if last use)
print(my_dog.name)
adopt(my_dog)           # warning: copy needed (my_dog used above, not last use)
```

### Deref (non-consuming)

Method/field access through `Deref[T]` is non-consuming -- it borrows the wrapper:

```python
box: Box[Dog] = Box(Dog("Rex"))
box.speak()       # non-consuming: auto-deref borrows
box.name          # non-consuming: auto-deref borrows
take_dog(box)     # consuming: move (if Own[Box[Dog]] param)
```

### copy() on @nocopy types

Calling `copy()` on a `@nocopy` type is a compile error:

```python
box = Box(Dog("Rex"))
other = copy(box)     # ERROR: cannot copy @nocopy type 'Box[Dog]'
```

### Containers

Appending a `@nocopy` value to a container moves it:

```python
boxes: list[Box[Dog]] = []
box = Box(Dog("Rex"))
boxes.append(box)     # move into container (box consumed)
```

For copyable types, `append` of a last-use variable auto-moves instead of copying.

## Implementation Phases

### Phase 1: Liveness analysis -- DONE

Backward dataflow last-use analysis in `tpyc/liveness.py`. Handles linear code,
branching (both/one branch), loops (conservative fixpoint), per-iteration variables.
Output: `set[int]` of TpyName node IDs at their last use, stored in `ctx.all_last_uses`.

### Phase 2: Auto-move emission -- DONE

Codegen emits `std::move(name)` at last-use sites for Tier 1 locals and `Own[T]`
params. Generic `Own[T]` with type param T uses `std::forward<T>()`. Sema relaxes
the lvalue-to-Own[T] error when the arg is at last use. Return sites allow
`return p` without `copy()` when p is at last use (C++ NRVO/implicit move handles it).

### Phase 3: Unnecessary copy() warnings -- DONE

Warns when `copy(x)` is used at function call args or return sites where x is at
last use. Uses `resolved_function_info.is_builtin_function` + `name == "copy"` to
identify tpy.copy (handles aliases, ignores user-defined shadowing).

Note: builtin type method args (e.g. `list.append(copy(x))`) don't yet trigger
the warning since they bypass CallAnalyzer.

### Phase 4: @nocopy types -- DONE

`@nocopy` decorator on records deletes copy ctor/assignment and defaults move ops.
Consumption model uses existing liveness analysis: consuming uses (Own[T] param, return)
at last use are auto-moved; not at last use produces a @nocopy-specific error.
`copy()` on @nocopy type is a compile error. T& aliases work for borrowing but can't
be consumed. Tier 3 (T* pointer-local) works for reassigned @nocopy since it uses
`std::optional::emplace()` (no copy needed). No Tier 4 needed.

### Phase 5: Alias-aware liveness (borrow safety) -- DONE

Alias map built at prescan time: `alias_name -> source_name` for lvalue-initialized,
non-reassigned variables with simple `TpyName` init. Transitive chains resolved
(`a -> h, b -> a` => both alias `h`). Passed to `analyze_last_uses()` which checks:
when marking a variable as last-use, also verify no T& alias is still in the live set.
If an alias is live, auto-move is suppressed. For copyable types this falls back to the
implicit-copy warning; for @nocopy types the @nocopy-specific error fires.

Detach-on-reassign: when the source variable is reassigned (e.g. `alias = h; h = new()`),
the alias points to old storage (separate slot in C++) and no longer constrains moves of
the new h. Implemented via per-alias position tracking: a forward pre-scan finds both
the first reassignment position for each source and the creation position of each alias.
An alias is "detached" only if it was created BEFORE the source's first reassignment --
aliases created after the reassignment track the current value and still constrain moves.
The backward pass re-activates detached aliases at the reassignment point so code before
it still checks. Only handles top-level reassignments in the same block (not inside
nested if/for); conditional reassignments conservatively keep alias checking active.

The liveness alias map tracks name-to-name aliases plus field/subscript-chain
aliases (`alias = h.field`, `n = xs[0]` -- recorded against the chain's root
name in `prescan.chain_alias_sources`), all with the detach-on-reassign and
dead-alias precision above. Borrows the map cannot represent syntactically --
call results borrowing an argument (`return_borrows_from`), generator objects
storing their iterable by reference -- are handled at the consume site
instead: `compat.is_auto_move_use` consults the BorrowTracker and demotes the
move to the copy path (retracting the last-use mark so codegen agrees) when
such a borrower exists. Invariant shared by the three analyses: every
BorrowTracker borrower name must be either a prescan-map alias (liveness
models it), a liveness-invisible borrower (gates the move), or an explicitly
excluded sentinel like `"__for_iter"` -- a new borrower naming convention
must pick its bucket consciously or it silently widens/narrows the gate.
Known residuals (forward-referenced callees whose borrow facts are not yet
analyzed, borrows hidden inside nested call arguments, loop-var aliases
outliving their loop) are tracked in BUGS.md "Safety / borrow checker".

### Phase 6: Box[T] as library type

- Define `Box[T]` in std lib (.py file with native backing)
- `@nocopy`, implements `Deref[T]`
- Wraps `std::unique_ptr<T>`
- Integrates with dynamic dispatch (Phase 9 of protocol design)

## Future Optimizations

### Move-through for lvalue assignment at last use

Currently `alias = h` (lvalue-init, non-reassigned) always creates a T& reference.
When `h` is at its last use, the compiler could instead move `h` into `alias`, making
`alias` a Tier 1 owned value. This enables chained moves:

```python
def transfer() -> Own[Handle]:
    h = Handle()
    alias = h       # last use of h -- could move instead of T& ref
    return alias    # last use of alias -- NRVO
```

Today this errors for @nocopy types (alias is T& ref, can't be consumed) and produces
an unnecessary copy for copyable types. With move-through, both cases would be optimal.

See test: `tests/cases/auto_move/error_nocopy_return_alias/`
