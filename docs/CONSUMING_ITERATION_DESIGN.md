# Consuming Iteration Design

## Status

| Feature | Status |
|---------|--------|
| `own(x)` builtin -- explicit `T -> Own[T]` conversion | Near-term |
| `__iter__(self: Own[Self]) -> Iterator[Own[T]]` overload | Planned (blocked -- see challenges) |
| `OwnIter[T]` runtime type (list drain) | Planned |
| User-defined drain iterators (`ArrayListDrainIter`) | Planned |
| Coercion `list[T] -> Iterable[Own[T]]` with copy warning | Planned |
| Borrow tracking for view-type drain iterators | Planned (depends on view type tracking) |

### Near-term: `own()` builtin

`own(x)` is an explicit `T -> Own[T]` conversion, analogous to C++ `std::move()`.
It mirrors the existing `span(x)` builtin (`span(x)` -> `Span[T]`, `own(x)` -> `Own[T]`).

```python
a.extend(own(b))     # transfer ownership of b's elements -- no warning
a.extend(b.copy())   # explicit copy -- no warning
a.extend(b)          # implicit copy -- warns "use own() or copy()"
```

Once `__iter__(self: Own[Self])` auto-dispatch is implemented, the compiler will
warn "unnecessary own() -- auto-move applies" at last-use call sites, allowing
users to drop the explicit annotation. Code written with `own()` stays valid.

---

## Motivation

In CPython, list elements are references -- iterating, extending, or slice-assigning
copies references cheaply. In TurboPython, lists store values, so these operations
copy the values themselves, which can be expensive for large record types.

The copy warning system (`Own[T]` parameter coercion) already surfaces this for
`append()` and direct element assignment. But methods taking `Iterable[T]` (e.g.
`list.extend()`, `ArrayList.extend()`) silently copy all elements with no warning:

```python
b: list[Node] = [...]
a.extend(b)       # copies all Node values -- no warning today
a[1:3] = b        # same gap (partially fixed by OwnType coercion in slice assign)
```

The root cause: `Iterable[T]` cannot express ownership intent, so the coercion
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

```python
b: list[Node] = [...]
a.extend(b)         # b used after: list[T].__iter__() -> Iterator[T], copies, warn
a.extend(b)         # b last use:   Own[list[T]].__iter__() -> Iterator[Own[T]], moves
a.extend(b.copy())  # explicit copy: no warning
```

`self: Own[Self]` syntax already exists in the language (e.g. `Box.take()`).

### Method signatures

Functions that consume their argument use `Iterable[Own[T]]`:

```python
# list built-in
def extend(self, other: Iterable[Own[T]]) -> None: ...

# ArrayList (tplib)
def extend(self, items: ReadOnlySpanLike[T] | Iterable[Own[T]]) -> None: ...
```

The `ReadOnlySpanLike[T]` path in `ArrayList.extend()` is a bulk-copy optimization
(memcpy for trivial types). It always copies -- the caller is responsible for ensuring
the copy is intentional (e.g. explicitly passing a span or using `.copy()`).

### Slice assignment

Slice assignment already uses `OwnType(list[T])` as the coercion target (introduced
with the slice assignment implementation), which triggers the same copy warning.
This is consistent with the `Iterable[Own[T]]` approach for method params.

### Iterator[Own[T]] -> Iterator[T] coercion

`Iterator[Own[T]]` coerces to `Iterator[T]` via the existing `Own[T] -> T` coercion
on each element. This allows passing a consuming iterator to a function that only
expects a borrowing one.

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

**Coercion** `list[T] -> OwnIter[T]`:
- Last use: `OwnIter<T>{std::move(vec)}`
- Not last use: `OwnIter<T>{vec}` (copy) + warn "copies list[T]; use copy()"

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
| `ArrayListDrainIter[T]` | Borrows inline storage | No | Yes |

Borrow tracking for view-type drain iterators depends on the general "view type
borrow tracking for user types" feature (currently only built-in view types --
`Span[T]`, `Ptr[T]` -- are tracked). See `TODO.md` Safety section.

---

## Coercion summary

| From | To | Condition | Action |
|------|----|-----------|--------|
| `list[T]` | `Iterable[Own[T]]` | last use | `OwnIter<T>{std::move(vec)}`, no warning |
| `list[T]` | `Iterable[Own[T]]` | not last use | `OwnIter<T>{vec}` (copy), warn |
| `list[T]` | `Iterable[Own[T]]` | `b.copy()` explicit | `OwnIter<T>{copy}`, no warning |
| `Span[T]` | `Iterable[Own[T]]` | always | copy elements, warn |
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

## Open questions

- **`Iterable[T]` vs `Iterable[Own[T]]` conformance**: does `Iterable[Own[T]]`
  subsume `Iterable[T]` in protocol conformance? i.e. can a function accepting
  `Iterable[T]` receive an `Iterable[Own[T]]`? Answer: yes, via `Iterator[Own[T]]
  -> Iterator[T]` coercion (each `Own[T]` element is unwrapped to `T`).

- **`Array[T, N]` drain**: should `Array[T, N]` also support `__iter__(Own[Self])`?
  It is stack-allocated like `UninitArrayStorage`, so a drain iterator over it would
  also be a borrowing view type.

- **Generic user types**: a user type `Stack[T]` with
  `__iter__(self: Own[Self]) -> MyDrainIter[T]` needs `MyDrainIter[T]` registered
  as a view type for borrow tracking to apply. Until view type annotations are
  implemented, the safety guarantee is best-effort.

- **Partial consumption and drop**: for `ArrayListDrainIter`, unconsumed elements
  are moved-from but not explicitly destructed by the iterator. Their destructors
  will run when the ArrayList goes out of scope (the ArrayList's destructor still
  owns the storage). This works naturally: TurboPython objects carry a `__tpy_owned`
  field that tracks whether they have been moved out, so the ArrayList destructor
  skips already-consumed elements without any special handling.

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

`Iterator[Own[T]]` and `Iterable[Own[T]]` require `Own` to be usable as a type
argument inside generic types. Currently `Own` is a type qualifier for params and
return types, not a composable first-class type. The type system would need to
support `Own[T]` wherever `T` is valid in type arguments, including in protocol
conformance checks and coercion rules.

### 3. Loop variable type `Own[T]`

If `__iter__(Own[Self])` returns `Iterator[Own[T]]`, the loop variable `x` in
`for x in b` (last use) has type `Own[T]`. This means `x` can only be used once
per iteration -- a new constraint the sema must enforce. The error messages when
`x` is used twice could be confusing to users who didn't write any explicit move.

### 4. Non-last-use coercion to `Iterable[Own[T]]`

When `b: list[T]` is not at last use and `extend` expects `Iterable[Own[T]]`:
`list[T]` has no `__iter__() -> Iterator[Own[T]]` at non-last-use. The coercion
system must know to insert a copy of `b` into `Own[list[T]]` first, then call the
consuming overload. This requires a new coercion rule: `list[T] -> Iterable[Own[T]]`
inserts a copy (with warning) and then calls the `Own[Self]` overload.

### 5. Context-dependent protocol conformance

Does `list[T]` statically conform to `Iterable[Own[T]]`? The `Own[Self]` overload
is only selectable at last use -- at non-last-use, `list[T]` would conform via
copy (with warning). The protocol checker would need to understand this
context-dependent conformance rather than a simple structural check.
