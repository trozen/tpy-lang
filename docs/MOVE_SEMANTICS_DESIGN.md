# Move Semantics Design for TurboPython

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

### Phase 1: Liveness analysis

Add a last-use analysis pass. For each local variable / Own parameter, compute
at each use site whether it is the last use. This is a backward dataflow analysis
on the control flow -- no type information needed.

Output: a set of AST nodes (or source locations) that are last-use sites.

Can potentially be integrated with the existing `prescan.py` reassignment scan, since
both are pure AST walks. Alternatively, a separate pass that runs after prescan.

### Phase 2: Auto-move emission

In codegen, when emitting a variable reference at a last-use site:
- If the variable is Tier 1 local or Own parameter: emit `std::move(name)`
- Otherwise: emit as today

This gives immediate benefit -- existing code gets faster without any user changes.

### Phase 3: Unnecessary copy() warnings

When a `copy()` call wraps a variable at a last-use site, warn that the copy is
unnecessary (auto-move would suffice). Purely diagnostic, no codegen change.

### Phase 4: @nocopy types

- `@nocopy` decorator recognized by parser/sema
- Deleted copy constructor/assignment in codegen
- Consumption tracking in sema (error on double-consume)
- `copy()` on @nocopy type is a compile error
- Tier 4 variable model for reassigned @nocopy locals

### Phase 5: Box[T] as library type

- Define `Box[T]` in std lib (.tp.py file with native backing)
- `@nocopy`, implements `Deref[T]`
- Wraps `std::unique_ptr<T>`
- Integrates with dynamic dispatch (Phase 9 of protocol design)
