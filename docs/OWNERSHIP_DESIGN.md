# Ownership and Lifetime Model — Design Decision

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

Built-in value types (`Int32`, `Bool`, `float`, `Char`) and user-defined value types (analogous to frozen dataclasses — small, immutable) copy without warnings. There is no observable difference between copying and sharing for these types.

```python
a: Int32 = 42
b = a                   # copies, no warning — semantically invisible
```

### 4. `copy()` for explicit value duplication

The `copy()` function (already in the language) creates an independent value from a pointer. It silences the copy warning and makes the programmer's intent explicit.

```python
y = copy(x)             # y owns an independent copy of x's value
self.field = copy(x)    # field owns a copy, no warning
results.append(copy(x)) # list owns a copy, no warning
```

**Move optimization:** When the compiler can prove that the source variable is not used after the copy, the copy is silently optimized to a move (no data duplication). This is an invisible optimization — the programmer doesn't need to think about it. Additionally, C++ copy elision (RVO/NRVO) may eliminate copies entirely when returning values.

**Rvalues don't need `copy()`:** Constructor calls and function results are rvalues — they don't have an existing owner, so they can be moved directly into persistent storage without copying:

```python
results.append(Point(i, i))        # rvalue — moved directly, no copy() needed
self.field = create_point(1, 2)    # rvalue — moved directly, no copy() needed
```

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

def create_point(x: Int32, y: Int32) -> Own[Point]:
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
def process() -> Int32:
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
def find(points: list[Point], val: Int32) -> Point | None:
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

For high-performance code that needs explicit pointer control, `Ptr[T]` and `ConstPtr[T]` remain available. Basic safety checks are enforced:

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
