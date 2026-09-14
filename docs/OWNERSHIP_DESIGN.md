# Ownership and Lifetime Model — Design Decision

## Implementation Status

| Feature | Status |
|---------|--------|
| Pointer-local model (non-value locals are `T*`) | Done |
| Global pointer model (non-value globals are `T*` with static backing) | Done |
| Rvalue direct move, `copy()`, init from param/element, for-each mutation, rebinding | Done |
| Param/loop-var reassignment error (non-value types) | Done |
| `Own[T]` return by value, basic dangling detection (return-local) | Done |
| Copy warning: `self.field = x` | Done |
| Copy warning: `append(x)`, `insert(x)`, `items[i] = x` — `Own[T]` params | Done |
| Move optimization (liveness analysis -> `std::move`) | TODO |
| Loop-local escape detection (scope-depth based) | Done |
| Pointer provenance (`return best` from param container) | Done |
| `None`/nullptr for nullable pointer-locals | Done |
| `Ptr[T]` escape analysis (cross-function provenance) | TODO |
| Iterator invalidation detection | TODO |
| `Box[T]`, `Rc[T]`, user-defined value types | TODO |

## Summary

TurboPython uses a **pointer-variable model with value-storage** for ownership. Local variables are pointers to stack-allocated values, enabling shared references that match CPython's binding semantics. Persistent storage (record fields, global variables, container elements) owns values inline, eliminating lifetime tracking for stored data. No garbage collection, no reference counting, no per-access runtime overhead.

## Core Rules

### 1. Local variables are pointers

When an object is created, it is allocated on the stack. The variable holds a pointer to that stack slot. Assignment between local variables copies the pointer — both variables reference the same object.

```python
x = Point(1, 2)        # Point on stack, x points to it
y = x                   # y points to same object — shared reference
y.value = 99            # x.value is also 99 (matches CPython)
```

### 2. Persistent storage owns values (copy on assignment)

Record fields, global variables, and container elements store values inline (by value). Assigning a pointer-variable to persistent storage copies the value. The compiler emits a warning suggesting `copy()` to acknowledge the semantic difference from CPython.

```python
# Record field — copies value
self.field = x          # warning: copies Point; use self.field = copy(x)

# Global variable — copies value
global saved
saved = x               # warning: copies Point; use saved = copy(x)

# Container — copies value
results.append(x)       # warning: copies Point; use results.append(copy(x))
```

After the copy, the persistent storage owns an independent value. The original stack slot is unaffected.

### 3. Value types copy silently

Built-in value types (`int32`, `Bool`, `float`, `char`) and user-defined value types (analogous to frozen dataclasses — small, immutable) copy without warnings. There is no observable difference between copying and sharing for these types.

```python
a: int32 = 42
b = a                   # copies, no warning — semantically invisible
```

**Immutable types are value types.** Types where copy-vs-share is unobservable (because the type is immutable in Python) are treated as value types. This includes `str` (string_view) and `DynStr` (std::string). Since Python strings are immutable, `t = s` is semantically identical whether it copies or shares — no warning, no pointer indirection. The compiler optimizes to a move when the source is dead.

This keeps string handling simple — `s += gen_str(...)` in a loop is plain in-place `std::string::operator+=`, with no aliasing concerns. For hot paths where even DynStr copies are too expensive, lighter types are available: `str` (zero-copy view), `FixStr[N]` (stack-allocated, bounded).

### 4. `copy()` for explicit value duplication

The `copy()` function (already in the language) creates an independent value from a pointer. It silences the copy warning and makes the programmer's intent explicit.

```python
y = copy(x)             # y owns an independent copy of x's value
self.field = copy(x)    # field owns a copy, no warning
results.append(copy(x)) # list owns a copy, no warning
```

**Move optimization:** When the compiler can prove that the source variable is not used after the copy, the copy is silently optimized to a move (no data duplication). This is an invisible optimization — the programmer doesn't need to think about it. Additionally, C++ copy elision (RVO/NRVO) may eliminate copies entirely when returning values.

**Rvalues that OWN their result don't need `copy()`:** constructor calls, factories and `Own[T]`-returning functions have no existing owner, so they move directly into persistent storage:

```python
results.append(Point(i, i))        # rvalue — moved directly, no copy() needed
self.field = create_point(1, 2)    # rvalue — moved directly, no copy() needed
```

A call that returns a BORROW (`-> T` on a reference type) is an rvalue by the address-of test but is not a fresh value: the reference aliases the callee's storage, so an owning slot copies from it and the compiler says so (a warning at every owning slot, the `Own[T]` return included; `copy(...)` silences it). One rule, no lifetime reasoning: a temporary receiver does not exempt the call (`Point(n).updated()` warns too), because what the callee hands back can reach past its receiver.

### 5. `Own[T]` for return by value

Functions that create and return new objects use `Own[T]` (already in the language) to return by value. Functions that return references to existing objects (e.g., elements in a parameter container) return `T`.

```python
def find_max(points: list[Point]) -> Point:
    # Returns pointer to element in caller's list — safe
    best = points[0]
    for p in points:
        if p.value > best.value:
            best = p
    return best

def create_point(x: int32, y: int32) -> Own[Point]:
    # Returns a new value — copy/move
    return Point(x, y)
```

## Common Patterns

### Finding/referencing elements in containers

The most common pattern. Works naturally with zero copies:

```python
best = points[0]                # pointer to first element in list
for p in points:                # p points to each element
    if p.value > best.value:
        best = p                # pointer rebind — free
best.value = 99                 # modifies element in list in-place
```

### Simple local computation

Value semantics through pointer indirection. No lifetime concerns:

```python
def process() -> int32:
    p = Point(1, 2)
    p.x += 10
    return p.x                  # stack slot freed when function returns
```

### Building a list of objects

Construct directly into the container. Rvalues (constructor calls) can be moved directly — no `copy()` needed:

```python
for i in range(100):
    results.append(Point(i, i))     # rvalue — moved directly into list storage
```

When building from an existing variable, `copy()` is required:

```python
for i in range(100):
    p = Point(i, i)
    p.value *= 2                    # modify first
    results.append(copy(p))         # copy into list storage
```

### Searching with fallback

Return pointer to found element, or `None` if not found. Caller handles creation separately:

```python
def find(points: list[Point], val: int32) -> Point | None:
    for p in points:
        if p.value == val:
            return p            # pointer into caller's list — safe
    return None

# Caller:
result = find(points, 42)
if result is None:
    points.append(Point(42, 0))
    result = points[-1]         # pointer to newly added element
```

## Safety: Dangling Pointer Detection

The compiler performs local (intra-function) analysis to detect dangling pointers. No cross-function analysis is required for the base model.

### Returning a local

```python
def make_point() -> Point:
    p = Point(1, 2)
    return p                    # ERROR: returning pointer to local
                                # fix: return copy(p) with -> Own[Point]
```

### Loop-local escaping to outer scope

```python
saved = None
for i in range(1000):
    x = Point(i, i)
    if x.value > 500:
        saved = x               # ERROR: loop-local pointer escapes iteration
                                # fix: saved = copy(x)
```

### Rebind slot for rvalue reassignment

When a pointer-local is reassigned to an rvalue (constructor call, `copy()` result), the new object needs storage that outlives the current block -- a rebind inside a loop must not leave the pointer at a slot that dies with the iteration:

```python
p = Point(0, 0)                 # function scope, __slot_1
saved = Point(0, 0)
for i in range(1000):
    p = Point(i, i)             # in place: __slot_1 is p's own storage, nothing else holds it
    saved = p                   # safe -- points at function-scoped storage
print(saved.x)                  # valid
```

Where an rvalue rebind writes is sema's alias-rebind verdict
(`tpyc/sema/alias_rebind.py`, stamped on the statement as `rebind_storage`).
IN_PLACE renders `(*p) = <rvalue>;` -- the new object is constructed into the
storage the pointer already aims at and the superseded one is destroyed there,
which is CPython's drop point for an object no other name holds. OWN draws a
slot private to that site, `std::optional<T> __slot_N;` on the body's hoist
lines (the function prologue; the lambda prologue for a nested def;
`static __global_slot_N` at module scope), and renders
`p = &*(__slot_N = <rvalue>);`, so a name that still holds the old object
keeps it. The declaration reserves nothing: `T __slot_1 = init; T* p =
&__slot_1;` is the whole of it. A reassignment that needs no storage -- a
borrow-returning call or operator (which takes an address), or `None` (a null
pointer) -- reseats the pointer bare.

Because an OWN slot lands in the prologue, it is declared before every inline
local -- so it is destroyed *after* all of them, including the local's own
init slot. A `__del__` that observes another object's teardown sees
prologue-declared slots drop last. A slot drawn inside a block is hoisted the
same way, so the value one holds lives to function exit rather than block
exit -- CPython's timing for a value some other name still holds.

On a resumable frame the same verdict picks between `p.emplace(<rvalue>)` on
an owning `frame_slot<T>` field (IN_PLACE) and, for a local with any OWN
site, a `T* p` field over per-site `std::optional<T> __ptr_slot_fN` fields
(`_prescan_resumable_ptr_slots`), where OWN renders
`p = &*(__ptr_slot_fN = <rvalue>);` and IN_PLACE `(*p) = <rvalue>;`.

### Stack slot reuse in loops

When no pointer escapes, the compiler reuses the stack slot:

```python
for i in range(1000):
    x = Point(i, i)            # reuses same stack slot — one allocation total
    process(x)
```

### Iterator invalidation (phase 2)

Pointers into container storage become invalid if the container reallocates:

```python
best = points[0]               # pointer into points' storage
points.append(Point(5, 6))    # may reallocate — best could dangle
best.value                     # potentially dangling
```

Detecting this reliably is non-trivial — any function call could potentially mutate a container and invalidate pointers. This is deferred to a later phase alongside `Ptr[T]` escape analysis. In the first iteration, iterator invalidation is the programmer's responsibility (as it is in C++ with `std::vector`).

## Explicit Pointers: `Ptr[T]`

For high-performance code that needs explicit pointer control, `Ptr[T]` and `Ptr[readonly[T]]` remain available. Basic safety checks are enforced:

- Cannot return `Ptr` to a local variable
- Cannot take `Ptr` of a loop-local that escapes the iteration

More advanced `Ptr[T]` escape analysis (tracking pointer provenance across function boundaries) is planned as a future compilation pass. Until then, complex `Ptr[T]` usage is the programmer's responsibility — similar to raw pointers in Rust's `unsafe` blocks.

For precompiled modules, pointer provenance metadata can be emitted alongside compiled output to enable cross-module analysis without re-reading source.

## How This Differs from CPython

| Operation | TurboPython | CPython | Warning? |
|-----------|------------|---------|----------|
| `y = x` (local) | Pointer copy (shared) | Reference copy (shared) | No — matches |
| `f(x)` (parameter) | Pointer/reference | Reference | No — matches |
| `self.field = x` | Value copy | Reference copy | Yes — suggest `copy()` |
| `global_var = x` | Value copy | Reference copy | Yes — suggest `copy()` |
| `list.append(x)` | Value copy | Reference append | Yes — suggest `copy()` |
| `x = None` | Only for nullable types | Always allowed | Error if not nullable |

The divergence is limited to persistent storage. Variable-to-variable assignment and function parameter passing match CPython.

## Interaction with `@noalloc`

`@noalloc` is a performance profile annotation applied to functions or modules, declaring that the annotated code must not allocate heap memory. This enables use in real-time / latency-critical hot paths where allocation jitter is unacceptable.

The ownership model is fully compatible with `@noalloc`:

- Stack allocation only — no heap
- No reference counting — no atomic operations
- No per-access checks — no runtime overhead
- Pointer variables are just `T*` in C++
- Value copies are plain struct assignments

No special mode or alternate model is needed for performance-critical code. The same ownership rules apply in `@noalloc` and unrestricted contexts.

## Design Notes for Future Type Integration

The ownership model must accommodate types that will be added later without requiring retroactive changes. Key considerations:

1. **Value type classification must be extensible.** Not hardcoded to built-in primitives — any type that is small, immutable, or where copy-vs-share is unobservable should be declarable as a value type. Strings (`str`, `DynStr`) use this. User-defined frozen records will too.

2. **View types in fields need lifetime care.** `str` (string_view) and `Span[T]` store inline in fields but reference external data — like `Ptr[T]`, they don't own what they point to. The ownership model's "fields store values inline" rule is correct (the view itself is inline), but additional safety rules are needed for view-type fields (restrict sources to long-lived data: literals, globals, owned fields). See `docs/STRING_HANDLING.md` for the string-specific rules.

3. **Liveness analysis should track sole ownership.** Beyond "is this pointer dangling?" the analysis should answer "is this the only reference?" This enables in-place optimizations and is needed generally for move optimization, not just strings.

## Implementation Phases

### Phase 1 (initial implementation)
- Pointer-variable model with value-storage for persistent data
- Copy warnings with `copy()` acknowledgment
- Move optimization when source is dead (liveness analysis)
- Rvalue direct move (no `copy()` for constructor calls / function results)
- Basic dangling detection: return-local, loop-local escape
- `Own[T]` for return by value (already exists)

### Phase 2 (safety analysis)
- **Escape analysis for `Ptr[T]`**: Cross-function pointer provenance tracking to catch misuse of explicit pointers
- **Iterator invalidation detection**: Tracking container mutations while pointers into the container are live
- Pointer provenance metadata for precompiled modules

### Future
- **`Box[T]`**: Heap-allocated unique ownership (`std::unique_ptr<T>`) for recursive types and large objects. Nullable (supports `None`).
- **`Rc[T]`**: Opt-in reference-counted shared ownership for patterns that genuinely need CPython-like sharing semantics (object graphs, caches). Not allowed in `@noalloc`.
- **User-defined value types**: Annotation or auto-detection for types that should copy silently (frozen, small, immutable).

## Rejected Alternatives

- **Pure value semantics (copy on all assignment)**: Simple, but makes the most common pattern (find/reference elements in containers) unnatural — requires returning indices or explicit pointers instead of the object itself. Every `y = x` would copy, diverging from CPython even for locals.

- **Reference counting by default (CPython-like / Lobster / Nim ORC)**: Matches CPython semantics exactly, but introduces runtime overhead (heap allocation, refcount operations, indirection) that conflicts with `@noalloc` hot paths. Would require a separate model for performance-critical code, creating a hybrid system.

- **Swift-style value/reference type split**: Clean per-type decision, but reference types use ARC and can't be used in `@noalloc`. Creates a two-tier type system where some types work in hot paths and others don't.

- **Mojo-style ownership annotations (`borrowed`/`inout`/`owned`)**: Maximum control, but the annotations are un-Pythonic and add burden to every function signature. Too far from Python's feel for a Python-first language.

- **Hylo-style mutable value semantics**: No aliasing eliminates a class of bugs, but fundamentally incompatible with CPython's object model. The common "find/reference element in container" pattern is unnatural since you can't hold a reference to an element.

- **Vale-style generational references**: Runtime overhead on every field access (generation check). Even though small, conflicts with `@noalloc` / real-time guarantees. Safety is runtime, not compile-time.

- **Full Rust-style borrow checking**: Maximum safety and performance, but significant language complexity and steep learning curve. Too far from Python's feel.

- **Copy-on-Write (COW)**: Efficient for read-heavy patterns, but still involves refcounting and diverges from CPython (mutation doesn't propagate to aliases).
