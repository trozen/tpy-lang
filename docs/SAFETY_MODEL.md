# Safety Model -- Ownership Checking Without a Borrow Checker

## Philosophy

TurboPython aims for memory safety without the friction of Rust's borrow checker.
The guiding principle: **compile-time where it's easy, runtime checks where it's hard,
never force annotations.**

Rust's borrow checker provides three guarantees:

1. No use-after-move
2. No dangling references
3. Aliasing XOR mutability (one `&mut` OR many `&`, never both)

Guarantee #3 is the source of most developer friction -- it rejects valid programs
(graphs, caches, self-referential structs) and forces workarounds (`Rc<RefCell<T>>`).
TurboPython intentionally drops #3 and provides #1 and #2 through simpler mechanisms.

## Safety Checks

### Use-After-Move (Compile-Time)

The compiler tracks when variables are moved (via auto-move at last use or explicit
ownership transfer). Using a moved variable is a compile-time error.

```python
b = Box[Int32](42)
val = b.take()      # moves value out of b
print(b.get())      # ERROR: b has been moved
```

This extends the existing auto-move analysis. No annotations needed -- the compiler
already knows which uses are last uses and which transfers take ownership (`Own[T]`
parameters, consuming-self methods).

**Status**: Partially implemented (auto-move at last use works; use-after-move
rejection is TODO).

### Dangling References -- Obvious Cases (Compile-Time)

The compiler rejects patterns that are statically provable to create dangling
references, without requiring lifetime annotations:

- Returning a reference to a local variable
- Storing a reference to a local in a struct that outlives the scope
- References escaping their scope through assignment to outer variables

```python
def bad() -> Ptr[Int32]:
    x: Int32 = 42
    return unsafe_ptr(x)    # ERROR: cannot return reference to local
```

**Status**: Partially implemented (return-local detection, loop-local escape).

### Dangling References -- Complex Cases (Runtime Checks)

When the compiler can't statically prove safety, the runtime checks at use.
This covers patterns like:

- Pointer to element in a container that gets resized
- Reference stored in a struct used after the referent is freed
- Iterator invalidation

```python
items = [1, 2, 3]
p = items.ptr()
items.append(4)       # may reallocate
print(unsafe_load(p, 0))  # checks on (every tpy build): runtime panic
                           # -DNDEBUG: undefined behavior
```

The approach is already used in `UninitHeapStorage` / `UninitArrayStorage`
(alive-slot tracking). The checks are gated on `NDEBUG`; no `tpy` build variant
defines it -- the default `-O3` build and `--debug` both keep them (measured as
free at `-O3`) -- so stripping them is a deliberate, explicit opt-in, never a
side effect of optimizing. The same pattern generalizes: checked builds track
validity, a stripped build trusts the programmer.

**Status**: Implemented for storage types (alive_ tracking). General pointer
validity tracking is TODO.

### Aliased Mutation (No Restriction)

TurboPython deliberately does NOT restrict aliased mutation. Multiple mutable
references to the same object are allowed:

```python
x = Point(1, 2)
y = x               # shared reference (pointer copy)
y.value = 99        # x.value is also 99 -- this is fine
```

This matches Python semantics and avoids the most common source of borrow checker
friction. The tradeoff: data races are possible in concurrent code. This will be
addressed separately when concurrency is added (message passing, actors, or
thread-local ownership -- not aliasing rules).

## Comparison

| Safety check | Rust | TurboPython |
|---|---|---|
| Use-after-move | Compile error | Compile error |
| Dangling ref (obvious) | Compile error | Compile error |
| Dangling ref (complex) | Lifetime annotations | Runtime panic (unless `NDEBUG`) |
| Aliased mutation | Compile error | Allowed |
| Double-free / leak | Compile error | Runtime panic (unless `NDEBUG`) |
| Iterator invalidation | Compile error | Runtime panic (unless `NDEBUG`) |

## @noalloc Context

The `@noalloc` decorator (planned) marks functions/classes/modules where heap
allocation is forbidden. In this context, ownership is simpler -- everything is
stack-allocated or passed by reference -- so the compiler can verify more safety
properties statically without annotations. This gives hot-path code stronger
guarantees while keeping the default mode ergonomic.

## Design Rationale

Rust's borrow checker is a hard requirement because Rust has no garbage collector
and no runtime safety net. Every safety property must be proven at compile time,
which forces lifetime annotations and restricts valid programs.

TurboPython has runtime checks as a fallback. This means the compiler
doesn't need to prove everything statically -- it can defer complex cases to
runtime. The result:

- No lifetime annotations, ever
- No fighting the compiler to express valid patterns
- Runtime checks catch safety violations that the compiler can't prove, in
  every `tpy` build variant
- Stripping them (`-DNDEBUG`) is an explicit opt-in, never implied by `-O3`
- @noalloc provides stricter static guarantees where needed
