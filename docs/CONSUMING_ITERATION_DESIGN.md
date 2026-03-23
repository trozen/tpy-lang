# Consuming Iteration Design

## Status

| Feature | Status |
|---------|--------|
| Copy warning: `Iterable[Own[T]]` param coercion with Own-stripping | Done (transitional) |
| `copy_iter(x)` -- explicit element-by-element copy acknowledgment | Done (CopyIter codegen; sema type is transitional `Own[Container]`, see known gaps) |
| `OwnIter[T]` runtime type (list drain) | Done (vector only) |
| `CopyIter[T]` runtime type (element-by-element copy adapter) | Done |
| `__iter__(self: Own[Self]) -> Iterator[Own[T]]` overload for list | Done (container-level move; element-level move pending loop var binding fix) |
| Container-level move for-loop (list at last use) | Done (moves vector into OwnIter; loop var still binds as const ref) |
| Consuming `__iter__` overload dispatch (method calls) | Done (overload resolution + codegen for native_function methods) |
| `auto_own[Self]` / `auto_own[T]` -- auto-generate consuming overloads | Done (parser cloning + ownership propagation through fields) |
| Ownership propagation through fields (`Own[S].field` -> `Own[T]`) | Done |
| `Iterable[T]` -> `Iterator[T]` auto-coercion (calls `__iter__`) | Planned |
| `auto_own` on `__iter__` for user collections | Blocked (needs `iter(Own[T])` generic builtin unwrap + protocol return type in for-loops) |
| Set/dict consuming `__iter__` | Planned (needs proper drain iterators, not vector-copy) |
| User-defined drain iterators (`ArrayListDrainIter`) | Planned |
| Element-level move in consuming for-loops | Planned (loop var binding fix: `auto` instead of `const auto&`) |
| `CopyIter[T]` as concrete sema type | Planned (currently modeled as `Own[Container]`, blocks standalone use) |
| Borrow tracking for view-type drain iterators | Planned (depends on view type tracking) |

---

## Motivation

In CPython, list elements are references -- iterating, extending, or slice-assigning
copies references cheaply. In TurboPython, lists store values, so these operations
copy the values themselves, which can be expensive for large record types.

The copy warning system (`Own[T]` parameter coercion) already surfaces this for
`append()` and direct element assignment. Bulk operations like `extend()` and
container constructors need the same treatment:

```python
b: list[Node] = [...]
a.extend(b)       # copies all Node values -- should warn if b is still alive
```

The root cause: `Iterator[T]` cannot express ownership intent, so the coercion
machinery has no signal to warn on.

---

## Design

### Core idea: `__iter__` overload on `Own[Self]`

`__iter__` gains a second overload that fires when `self` is owned (consumed):

```python
def __iter__(self) -> Iterator[T]: ...              # borrowing: copies elements
def __iter__(self: Own[Self]) -> Iterator[Own[T]]:  # consuming: moves elements
```

The auto-move system already promotes last-use variables to `Own[T]`. This means
overload dispatch selects the consuming overload automatically at last use -- no
new syntax or annotation needed at the call site.

`self: Own[Self]` syntax already exists in the language (e.g. `Box.take()`).

### Rust analogy: `IntoIterator`

This design parallels Rust's `IntoIterator` trait:

| Rust | TPy | Meaning |
|------|-----|---------|
| `impl IntoIterator<Item = T>` (by value) | `__iter__(self: Own[Self]) -> Iterator[Own[T]]` | Consuming: yields owned elements |
| `impl IntoIterator<Item = &T>` (by ref) | `__iter__(self) -> Iterator[T]` | Borrowing: yields references/copies |
| `vec.into_iter()` (consumes vec) | `iter(b)` at last use (auto-move) | Container consumed, elements moved |
| `vec.iter().cloned()` | `copy_iter(b)` | Explicit element-by-element copy |
| Compile error on mismatch | Warning on mismatch | Soft vs hard enforcement |

### Method signatures

Functions that consume elements use `Iterator[Own[T]]`:

```python
# list built-in
def extend(self, other: Iterator[Own[T]]) -> None: ...

# ArrayList (tplib)
def extend(self, items: Iterator[Own[T]]) -> None: ...
```

When the caller passes an `Iterable` (e.g. a list) to a parameter expecting
`Iterator`, the compiler auto-coerces by calling `__iter__()`. If the source is
at its last use, auto-move selects the consuming `__iter__` overload; otherwise
the borrowing overload is used and a warning is emitted.

### `copy_iter()` -- explicit copy acknowledgment

`copy_iter()` wraps a borrowing iterator and copies each element, yielding
`Own[T]` values. This is Rust's `.cloned()` / `.copied()` equivalent:

```python
def copy_iter(iterable: Iterable[T]) -> Iterator[Own[T]]: ...
```

At C++ level: wraps the borrowing iterator and copies each element directly into
the destination. No intermediate container copy -- N copies total, the minimum.

```python
a.extend(copy_iter(b))           # N copies directly into a. b untouched.
a.extend(copy_iter(d.values()))  # works for any iterable
```

### Warning rules (Framing A: semantic divergence)

The warning fires when TPy **behaves differently** from CPython -- i.e., when
CPython would create shared references but TPy creates independent copies, and
the difference is **observable** (the source is still alive after the operation).

**Warn** when all of:
1. Parameter expects `Iterator[Own[T]]` (or `Iterable[Own[T]]` transitionally)
2. Element type is non-value (copy is meaningful)
3. Argument is lvalue, not `copy_iter()` / `copy()`, not at last use

**Don't warn** when any of:
- Value-type elements -- copy is semantically invisible
- `copy_iter()` or `copy()` -- user explicitly acknowledged
- Rvalue / temporary / literal -- no live alias exists
- Last use -- source is dead after this, no observable aliasing divergence

### User experience

```python
a.extend(b)              # b at last use -> auto-move, 0 copies, no warn
a.extend(b)              # b NOT at last use -> warn: copies Node elements
a.extend(copy_iter(b))   # explicit copy ack, N copies, no warn
a.extend([Node(1)])      # rvalue -> auto-move from temporary, no warn
```

### Iterable -> Iterator auto-coercion

When an `Iterable[T]` is passed to a parameter expecting `Iterator[T]` (or
`Iterator[Own[T]]`), the compiler auto-inserts a call to `__iter__()`:

```python
def extend(self, items: Iterator[Own[T]]) -> None: ...

a.extend(b)  # b: list[T] -> auto-coerce -> iter(b) or own iter at last use
```

This coercion selects the appropriate `__iter__` overload based on ownership:
- `b` at last use -> `Own[list[T]]` -> consuming `__iter__` -> `Iterator[Own[T]]`
- `b` not at last use -> `list[T]` -> borrowing `__iter__` -> `Iterator[T]` -> warn

### Slice assignment

Slice assignment already uses `OwnType(list[T])` as the coercion target (introduced
with the slice assignment implementation), which triggers the same copy warning.

### Iterator[Own[T]] -> Iterator[T] coercion

`Iterator[Own[T]]` coerces to `Iterator[T]` via the existing `Own[T] -> T` coercion
on each element. This allows passing a consuming iterator to a function that only
expects a borrowing one.

---

## `auto_own` -- auto-generating consuming overloads

Defining both borrowing and consuming `__iter__` overloads on every collection
would be verbose. The `auto_own` mechanism (mirroring the existing `auto_readonly`
pattern) auto-generates both variants from a single definition.

### Syntax: `auto_own[Self]` on the self parameter

```python
class MyStack[T]:
    _items: list[T]

    def __iter__(self: auto_own[Self]) -> Iterator[auto_own[T]]:
        return iter(self._items)
```

The compiler clones this into two variants at parse time:

```python
# Borrowing clone:
def __iter__(self) -> Iterator[T]:
    return iter(self._items)        # self._items: list[T] -> Iterator[T]

# Consuming clone:
def __iter__(self: Own[Self]) -> Iterator[Own[T]]:
    return iter(self._items)        # self._items: Own[list[T]] -> Iterator[Own[T]]
```

The body is identical -- ownership propagation through fields makes `self._items`
owned in the consuming clone, which causes `iter()` to dispatch to the consuming
`__iter__` overload on list.

### `auto_readonly` parallel

| Feature | `auto_readonly` | `auto_own` |
|---------|----------------|------------|
| Decorator/annotation | `@auto_readonly` decorator | `auto_own[Self]` on self param |
| Type modifier | `auto_readonly[T]` | `auto_own[T]` |
| Clone 1 | Mutable self, `T` return | Borrowed self, `T` return |
| Clone 2 | Readonly self, `readonly[T]` return | `Own[Self]`, `Own[T]` return |
| Propagation | Constness through return type | Ownership through fields and return type |

### Ownership propagation through fields

For `auto_own` to work, the compiler must support:

```
Own[MyStack[T]]._items  -->  Own[list[T]]
```

When `self: Own[Self]`, field access on self produces an owned field type. This
is analogous to Rust's destructuring of owned structs -- if you own the struct,
you own its fields.

This rule is general and useful beyond `auto_own`: any context where a struct is
owned should allow its fields to be treated as owned.

### Builtin containers

For builtins (list, set, dict), the consuming `__iter__` is defined directly in
lib code with explicit `self: Own[Self]`:

```python
# lib/tpy/builtins/_list.py
class list[T]:
    @readonly
    def __iter__(self) -> Iterator[T]: ...
    def __iter__(self: Own[Self]) -> Iterator[Own[T]]: ...
```

User collections use `auto_own` for convenience:

```python
class MyStack[T]:
    _items: list[T]

    def __iter__(self: auto_own[Self]) -> Iterator[auto_own[T]]:
        return iter(self._items)
```

---

## Concrete types

### `OwnIter[T]` -- for heap-backed containers (list[T])

`OwnIter[T]` is the drain iterator for `list[T]`. It owns a moved `std::vector<T>`
and exposes move iterators over it.

```cpp
template<typename T>
struct OwnIter {
    std::vector<T> data;
    std::size_t pos = 0;

    auto begin() { return std::make_move_iterator(data.begin()); }
    auto end()   { return std::make_move_iterator(data.end()); }

    std::expected<T, StopIteration> __next__() {
        if (pos >= data.size()) return std::unexpected(StopIteration{});
        return std::move(data[pos++]);
    }
};
```

- Freely returnable: owns heap storage, no dangling risk.
- O(1) construction: `std::vector` move is pointer swap.
- Unconsumed elements are destructed normally when `OwnIter` is dropped.

### `CopyIter[T]` -- for `copy_iter()` function

`CopyIter[T]` wraps a borrowing iterator and copies each element:

```cpp
template<typename T, typename Inner>
struct CopyIter {
    Inner inner;

    std::expected<T, StopIteration> __next__() {
        auto r = inner.__next__();
        if (!r.has_value()) return std::unexpected(r.error());
        return T(*r);  // copy element directly into expected (destination)
    }
};
```

Each element is copied exactly once, directly into the destination. No
intermediate container copy.

### User-defined drain iterators (e.g. `ArrayListDrainIter[T]`)

Stack-backed containers (e.g. `ArrayList[T]` backed by `UninitArrayStorage<T, N>`)
cannot cheaply transfer ownership of inline storage -- moving it copies N * sizeof(T)
bytes. Instead, their drain iterator **borrows** the inline storage via pointer:

```cpp
template<typename T>
struct ArrayListDrainIter {
    T* data;        // pointer into ArrayList's inline storage
    std::size_t pos;
    std::size_t size;

    std::expected<T, StopIteration> __next__() {
        if (pos >= size) return std::unexpected(StopIteration{});
        return std::move(data[pos++]);
    }
};
```

`ArrayList.__iter__(self: Own[Self])` produces an `ArrayListDrainIter[T]` that
borrows the inline storage. This is safe because `Own[Self]` guarantees no other
access to the ArrayList after this point.

`ArrayListDrainIter[T]` is a **view type**: it must not outlive the ArrayList it
borrows from, subject to the same escape analysis as `Span[T]`. Returning an
`ArrayListDrainIter[T]` from a function is an error.

Unconsumed elements: when `ArrayListDrainIter` is dropped before exhaustion, the
remaining moved-from elements are in a valid destructible state (standard C++ move
semantics guarantee). No special cleanup needed.

---

## Lifetime safety

| Type | Ownership | Returnable | Borrow tracked |
|------|-----------|------------|----------------|
| `OwnIter[T]` | Owns `std::vector<T>` | Yes | No |
| `CopyIter[T]` | Borrows source iterator | Depends on inner | Yes |
| `ArrayListDrainIter[T]` | Borrows inline storage | No | Yes |

Borrow tracking for view-type drain iterators depends on the general "view type
borrow tracking for user types" feature (currently only built-in view types --
`Span[T]`, `Ptr[T]` -- are tracked). See `TODO.md` Safety section.

---

## Coercion summary

| From | To | Condition | Action |
|------|----|-----------|--------|
| `Iterable[T]` | `Iterator[T]` | always | auto-call `__iter__()` |
| `Own[Iterable[T]]` | `Iterator[Own[T]]` | last use (auto-move) | auto-call consuming `__iter__(Own[Self])` |
| `Iterable[T]` | `Iterator[Own[T]]` | not last use | borrowing `__iter__` -> `Iterator[T]`, warn (mismatch) |
| `copy_iter(x)` | `Iterator[Own[T]]` | explicit | wraps borrowing iter, copies elements, no warn |
| `Iterator[Own[T]]` | `Iterator[T]` | always | strip `Own`, no copy |

---

## For-loop semantics

The consuming overload changes for-loop element semantics:

```python
b: list[Node] = [...]

for x in b:      # b used after: x is Node (copy each iteration)
    use(x)

for x in b:      # b last use: x is Own[Node] (move each iteration)
    use(x)
```

This is implicit -- the element type of the loop variable changes based on liveness.
Users who always want borrowing semantics regardless of liveness should pass a span:

```python
for x in span(b):    # always borrows, x is Node reference
    use(x)
```

---

## Current state and known gaps

### What works

- **Copy warnings**: `extend`, `list()`, `set()`, `dict()` signatures use
  `Iterable[Own[T]]` as a sema-level marker. The compatibility checker strips
  `Own` before protocol conformance, then warns when the source is a non-value
  lvalue that isn't at last use and isn't wrapped in `copy()` or `copy_iter()`.
- **`copy_iter()`**: Generates real `CopyIter` C++ adapter for element-by-element
  copy. Works inline in `extend(copy_iter(b))`. Borrow-tracked via
  `return_borrows_from`.
- **Container-level move for-loops**: `for x in b` at last use on `list[T]` moves
  the vector into `OwnIter` (source freed early). Overload dispatch selects
  consuming `__iter__` based on liveness.
- **`auto_own` parser cloning**: Generates borrowing + consuming method overloads.
  Ownership propagation through fields works (`self.field` yields `Own[T]` in
  consuming methods).
- **Consuming `__iter__` explicit dispatch**: `b.__iter__()` at last use selects
  the consuming overload and generates `own_iter(std::move(b))`.

### Known gaps

- **Element-level move not yet implemented**: Consuming for-loops move the
  container into OwnIter but the loop variable still binds as `const auto&`,
  so individual elements are referenced in-place rather than moved. Fix:
  override loop var binding to `auto` for consuming loops.
- **`copy_iter` sema type mismatch**: Sema models `copy_iter(b)` as
  `Own[list[T]]` but codegen emits `CopyIter<T, Inner>`. Works for inline
  use but breaks standalone (storing in a variable, passing as Iterator).
  Fix: register `CopyIter[T]` as a concrete sema type.
- **`auto_own` on `__iter__` blocked for user collections**: `iter(Own[list[T]])`
  fails because generic builtin type inference doesn't unwrap `Own[T]`.
  Also, protocol return types from user `__iter__` can't be iterated in
  for-loops.
- **Set/dict consuming `__iter__` not implemented**: Removed because the
  generic drain approach (copy into vector) defeats the purpose. Needs
  proper drain iterators for each container type.
- **`auto_own_basic` test misleading**: `test_consuming` doesn't trigger
  the consuming overload (liveness sees borrow through field return).
- **Diagnostics say "use copy()"**: Should mention `copy_iter()` too.

---

## Open questions

- **`Array[T, N]` drain**: should `Array[T, N]` also support `__iter__(Own[Self])`?
  It is stack-allocated like `UninitArrayStorage`, so a drain iterator over it would
  also be a borrowing view type.

- **Generic user types and view tracking**: a user type `Stack[T]` with
  `__iter__(self: Own[Self]) -> MyDrainIter[T]` needs `MyDrainIter[T]` registered
  as a view type for borrow tracking to apply. Until view type annotations are
  implemented, the safety guarantee is best-effort.

- **Partial consumption and drop**: for `ArrayListDrainIter`, unconsumed elements
  are moved-from but not explicitly destructed by the iterator. Their destructors
  will run when the ArrayList goes out of scope (the ArrayList's destructor still
  owns the storage). This works naturally: TurboPython objects carry a `__tpy_owned`
  field that tracks whether they have been moved out, so the ArrayList destructor
  skips already-consumed elements without any special handling.

- **`auto_own` for non-`__iter__` methods**: the `auto_own` mechanism is general
  but the primary use case is `__iter__`. Are there other methods that benefit from
  ownership-propagating overloads?

---

## Known implementation challenges

These are non-trivial integration problems that need resolved before implementation.

### 1. Overload dispatch timing

Last-use liveness is computed by `liveness.py` after sema. Method overload selection
happens *during* sema. This is currently fine for warnings (`needs_copy_warning`
consults `all_last_uses` as a side effect), but selecting a different overload based
on last-use *changes the return type* (`Iterator[T]` vs `Iterator[Own[T]]`), which
affects downstream type inference in the same sema pass.

Options:
- Run liveness as a pre-pass before sema (requires two-pass architecture)
- Make the `Own[Self]` overload selection lazy: resolve at codegen time, not sema
  (requires sema to produce an "ambiguous" node resolved later)
- Restrict `Own[Self]` overloads to call sites only (not for-loops), where the
  caller explicitly annotates intent

### 2. `Own[T]` in generic type argument position

`Iterator[Own[T]]` requires `Own` to be usable as a type argument inside generic
types. Currently `Own` is a type qualifier for params and return types, not a
composable first-class type. The type system would need to support `Own[T]` wherever
`T` is valid in type arguments, including in protocol conformance checks and
coercion rules.

### 3. Loop variable type `Own[T]`

If `__iter__(Own[Self])` returns `Iterator[Own[T]]`, the loop variable `x` in
`for x in b` (last use) has type `Own[T]`. This means `x` can only be used once
per iteration -- a new constraint the sema must enforce. The error messages when
`x` is used twice could be confusing to users who didn't write any explicit move.

### 4. Ownership propagation through fields

`auto_own` requires that `Own[Struct].field` produces `Own[FieldType]`. This is
a new type system rule. It interacts with:
- Field access codegen (needs `std::move(self.field)` for owned self)
- Partial moves (moving one field invalidates the struct)
- Interaction with `@nocopy` fields

### 5. `auto_own` parser cloning

The `auto_own` cloning mechanism mirrors `auto_readonly` (already implemented).
The parser infrastructure exists but needs extension for the ownership axis.
Key difference: `auto_readonly` changes method constness; `auto_own` changes self
ownership and propagates through field types and return types.
