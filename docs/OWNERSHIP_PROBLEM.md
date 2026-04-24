# Object Ownership and Lifetime in TurboPython

## Context

TurboPython is a compiler that translates Python source code to C++. The goal is **Python-first**: idiomatic Python should work out of the box, producing efficient native code. Equally important, the language must allow writing C++-level efficient code when desired — the programmer can opt in to low-level control over memory layout, allocation, and ownership without leaving the language. The compiler handles type inference, maps Python types to C++ equivalents, and generates readable C++ that is then compiled with a standard C++ compiler.

The type system already supports:
- Value types: `int` (arbitrary precision), `float`, `Int32`, `Bool`, `Char`
- User-defined records (classes mapped to C++ structs)
- Containers: `list[T]` (vector), `Array[T, N]` (fixed-size), `Span[T]` (read-only view)
- Pointers: `Ptr[T]` (raw mutable pointer), `Ptr[readonly[T]]` (raw const pointer)
- Ownership transfer: `Own[T]` (return by value with move semantics)
- Generics, protocols (concepts), single and multiple inheritance (static MI)

Records (user-defined classes) are currently stack-allocated C++ structs. They are passed to functions by reference and returned by value via `Own[T]`.

## The Problem

CPython and C++ have fundamentally different object models, and we need to decide how TurboPython bridges the gap.

### In CPython

All objects live on the heap. Variables are name bindings — they point to objects, they don't contain them. Assignment creates a shared reference:

```python
x = Point(1, 2)
y = x              # y and x refer to the SAME object
y.field = 99       # x.field is now also 99
x = None           # x unbound; object lives on (y still holds it)
```

Lifetime is managed by reference counting (plus cycle collection). The programmer never thinks about ownership — objects live as long as someone references them.

### In current TurboPython

Records are C++ value types. Assignment copies:

```python
x = Point(1, 2)
y = x              # y is a COPY of x
y.field = 99       # x.field is still 1
```

This works and is efficient, but silently differs from CPython semantics for mutable objects. The divergence is invisible for immutable/value-like usage but becomes observable when code depends on shared mutation.

### Where the semantic gap manifests

**Assignment:**
```python
x = Point(1, 2)
y = x
y.field = 99
print(x.field)      # CPython: 99, TurboPython: 1
```

**Container insertion:**
```python
points = []
p = Point(1, 2)
points.append(p)
p.field = 99
print(points[0].field)  # CPython: 99, TurboPython: 1
```

**Multiple references to the same object:**
```python
def register(obj: Thing) -> None:
    global_list.append(obj)

t = Thing()
register(t)
t.name = "updated"
# In CPython, global_list[0].name is "updated"
# In TurboPython (value semantics), it's the old value
```

**Nullability:**
```python
x = Point(1, 2)
x = None            # CPython: fine, x is now None
                     # TurboPython: not supported — Point is not nullable
```

Note: function parameter passing already matches CPython — records are passed by reference, so mutations inside a called function are visible to the caller.

### What we need to decide

1. **What should assignment of a record mean?** Copy (current)? Move (Rust-like, invalidating the source)? Shared reference (CPython-like, needs lifetime management)?

2. **How should heap allocation work?** When does an object need to live on the heap vs. stack? Should this be explicit (user annotates) or inferred (compiler decides)?

3. **How should shared ownership work, if at all?** Reference counting? Unique ownership with explicit cloning? Something else?

4. **How should `None`/nullability work for objects?** Optional wrapper? Nullable pointer types only? Any type is nullable?

5. **How much Rust-like safety do we want?** Move semantics? Borrow checking? Use-after-move detection? Or is simpler-but-less-safe acceptable?

6. **How should containers store objects?** Inline (value, current)? By pointer? Configurable?

### Constraints

- **No garbage collection.** The target is real-time / high-performance C++.
- **No memory leaks.** Object lifetime must be deterministic and correct.
- **CPython compatibility where practical.** Ideally, the same source file runs correctly under both CPython and the TurboPython compiler. Where semantics must diverge, the compiler should warn.
- **Performance by default.** The common case should be fast — stack allocation, no indirection, no reference counting — without requiring annotations.
- **Explicit over magical.** The programmer should be able to understand what the compiled code does. Hidden costs (implicit heap allocation, reference counting) should require opt-in.
- **`@noalloc` hot paths.** The language supports performance profiles where heap allocation is forbidden. The ownership model must work within this constraint.

### Key constraint: the model must be uniform

An important design constraint: the ownership model should work uniformly across all code, including `@noalloc` hot paths. Approaches that require runtime overhead (reference counting, generation checks) would need a separate model for `@noalloc` contexts, creating a hybrid system that is harder to implement and harder for programmers to reason about. We strongly prefer a single model that works everywhere — from casual scripts to real-time inner loops.

### Practical frequency of patterns

The ownership model should be optimized for the patterns programmers use most often:

1. **Very common: working with elements in containers.** Finding, referencing, and mutating objects that live in lists or other containers. Example: "find the object with the highest value." This pattern needs zero-copy access to container elements.

2. **Common: simple local computation.** Creating a local object, working with it, returning a result. Value semantics is perfect here.

3. **Less common: creating objects in a loop and storing them.** Building a list of new objects. This can be addressed by constructing objects directly into the container: `results.append(SomeObject(...))` rather than creating a local and then storing it.

4. **Occasional: object graphs, shared state, observers.** These patterns genuinely need shared references. They are important but less frequent than patterns 1-2.

The "find best in container" pattern illustrates the trade-offs well:

```python
# In CPython — zero copies, returned object IS the element in the list
def find_max(points: list[Point]) -> Point:
    best = points[0]            # reference to first element
    for p in points:            # p references each element
        if p.value > best.value:
            best = p            # rebind — pointer swap, free
    return best                 # reference to element in the list

result = find_max(points)
result.value = 99               # modifies the element inside points
```

With value semantics (Approach A), this requires copies on each improvement and the caller gets a disconnected copy. With pointer variables (Approach B), this works exactly like CPython — zero copies, direct access.

### Approaches under consideration

#### Approach A: Copy semantics with explicit acknowledgment

Objects are stack-allocated values. Assignment copies the value. The compiler uses liveness analysis to silently optimize to a move when the source variable is not used afterwards. When a copy is observable (source is used after assignment), the compiler emits a warning asking the programmer to use `copy()` to explicitly acknowledge the semantic difference from CPython.

```python
x = Point(1, 2)

# x not used after assignment → compiler silently moves (unobservable optimization)
y = x
print(y.field)          # OK, no warning

# x used after assignment → copies, warns
y = x                   # warning: copies Point; use y = copy(x) to make explicit
print(x.field)          # ← source still used, so it can't be a move
print(y.field)

# explicit copy → no warning
y = copy(x)             # OK, programmer acknowledged the copy
print(x.field)
print(y.field)
```

**Value types** — both built-in (`Int32`, `Bool`, `float`) and user-defined (analogous to frozen dataclasses in CPython — small, immutable, no observable difference between copy and share) — would copy silently without warnings, since the copy is semantically invisible.

**The "find best" pattern** requires returning an index or `Ptr[T]` instead of the object itself, since returning a copy disconnects it from the container:

```python
# Option 1: return index
def find_max_idx(points: list[Point]) -> Int32:
    best: Int32 = 0
    for i in range(len(points)):
        if points[i].value > points[best].value:
            best = i
    return best

# Option 2: return pointer
def find_max(points: list[Point]) -> Ptr[Point]:
    best: Ptr[Point] = take_ptr(points[0])
    for i in range(len(points)):
        if points[i].value > best.value:
            best = take_ptr(points[i])
    return best
```

**Strengths:**
- Simple mental model: values belong to variables, `copy()` to duplicate
- Zero-cost in the common case (move when source is dead)
- No lifetime tracking needed
- `@noalloc` compatible
- Use-after-move analysis is local (within one function), tractable for the compiler

**Weaknesses:**
- The very common "find/reference element in container" pattern requires index-based or pointer-based APIs instead of natural Python-style code
- Diverges from CPython for mutable objects (but the warning makes it explicit)
- Patterns that depend on shared mutable state (object graphs, registries) need explicit pointer/reference types

**Open questions:**
- How should containers interact? `list.append(x)` also copies — same warning?
- Should the compiler auto-detect user-defined value types (immutable, small), or require explicit annotation (e.g., `@value class Point`)?

#### Approach B: Stack-allocated objects with pointer variables

Objects are allocated on the stack, but variables hold pointers to those stack-allocated values rather than the values themselves. Assignment copies the pointer, not the value — so two variables can reference the same stack-allocated object. This matches CPython's binding semantics while avoiding heap allocation.

```python
x = Point(1, 2)        # Point allocated on stack; x is a pointer to it
y = x                   # y points to same stack object — shared reference
y.field = 99            # x.field is also 99 — matches CPython
x = Point(3, 4)        # new stack allocation; x points to new object
                        # y still points to old object
```

**Containers own their elements by value** (stored inline, like C++ `std::vector<T>`). Variables that reference container elements are pointers into the container's storage:

```python
# Find best — natural, zero copies, matches CPython
best = points[0]                # pointer to element in points' storage
for p in points:                # p points to each element
    if p.value > best.value:
        best = p                # pointer rebind — free
best.value = 99                 # modifies the element in the list in-place

# Build a list — construct directly into container
for i in range(100):
    results.append(Point(i, i)) # constructed into list's storage, no stack slot needed
```

The compiler uses escape analysis and liveness analysis to manage stack slot lifetimes:

**No aliasing (trivial — most common case):**
```python
for i in range(1000):
    x = Point(i, i)     # compiler reuses same stack slot each iteration
    process(x)
```
Only `x` references the slot, and `x` is reassigned next iteration. One stack slot total.

**Alias doesn't outlive scope:**
```python
for i in range(1000):
    x = Point(i, i)
    y = x                # alias, but both die at iteration end
    process(y)
```
Liveness analysis shows `y` is not used after the loop. Stack slot reused.

**Object creation in loops:** When creating objects in a loop and storing them, the idiomatic pattern is to construct directly into the container rather than creating a local variable first:

```python
# Preferred: construct directly into container
for i in range(1000):
    results.append(Point(i, i))     # no stack slot needed

# Also works but less idiomatic:
for i in range(1000):
    x = Point(i, i)
    results.append(copy(x))        # explicit copy into container
```

**Strengths:**
- `y = x` matches CPython (shared reference, mutations visible through both)
- The very common "find/reference element in container" pattern works naturally — zero copies, direct access
- Stack allocation — no heap overhead in common cases
- Function parameter passing is naturally by-reference (already matches CPython)
- No runtime overhead (no RC, no generation checks) — fully `@noalloc` compatible
- Container elements stored inline — cache-friendly

**Weaknesses:**
- Stack slot lifetime management needs escape analysis to avoid dangling pointers or unbounded stack growth
- Return values need special handling (stack frame is gone after return — may need to copy/move into caller's frame)
- Iterator invalidation: pointers to container elements become dangling if the container reallocates (e.g., `best = points[0]; points.append(...)` — `best` may dangle). The compiler would need to detect and warn about this.
- The escape analysis complexity is similar to what other approaches need, but failure modes (dangling stack pointers) are less obvious than explicit copies

**Open questions:**
- What happens when a reference would escape its scope (e.g., returned from function)? Copy? Compile error?
- How does the compiler detect and warn about iterator invalidation?
- Is the escape analysis tractable for all common patterns, or will programmers hit confusing errors?

#### Approach C: Swift-style value/reference type split

The programmer explicitly chooses whether a type has value or reference semantics. Value types copy on assignment (like Swift's `struct`). Reference types use automatic reference counting and share on assignment (like Swift's `class`).

```python
class Point:            # value type (default for simple records)
    x: Int32
    y: Int32

@reftype                # or some other marker — reference type, ARC-managed
class Node:
    children: list[Node]
    value: Int32
```

```python
# Value type — copies (no warning needed, this IS the semantics)
p1 = Point(1, 2)
p2 = p1                 # copy
p2.x = 99              # p1.x still 1

# Reference type — shares (matches CPython, ARC-managed)
n1 = Node(10)
n2 = n1                 # shared reference, refcount incremented
n2.value = 99           # n1.value is also 99 — matches CPython
n1 = None               # refcount decremented
```

Prior art: **Swift** makes exactly this distinction (`struct` vs `class`). **Mojo** also separates value types (with `__copyinit__`/`__moveinit__`) from reference-counted types.

**Strengths:**
- No ambiguity: the type author decides the semantics once, users follow
- Value types are efficient and `@noalloc` compatible
- Reference types match CPython exactly — object graphs, caches, observers all work naturally
- No compiler warnings needed — assignment does what the type says

**Weaknesses:**
- Two different models in one language — reference types have RC overhead and are not `@noalloc` compatible, creating a split where some types work in hot paths and others don't
- Programmer must learn and choose between two kinds of types
- Python doesn't have this distinction — all types are reference types in CPython
- The boundary between value and reference types may be unclear for some types

**Open questions:**
- What's the default — value or reference? Value is more efficient; reference matches CPython
- Can a value type contain a reference type field (and vice versa)?
- Should the compiler suggest which to use based on usage patterns?

#### Approach D: Mojo-style ownership annotations

Functions declare how they receive parameters: borrowed (read-only reference), mutably borrowed (in-out reference), or owned (takes ownership). Variables use value semantics with explicit transfer syntax. This is the model chosen by Mojo, which has a similar goal (Python syntax, native performance).

```python
def process(borrowed p: Point) -> Int32:    # immutable reference (default)
    return p.x + p.y

def update(inout p: Point) -> None:         # mutable reference
    p.x += 1

def consume(owned p: Point) -> Int32:       # takes ownership, caller can't use p after
    return p.x

x = Point(1, 2)
process(x)              # borrowed — x still valid
update(x)               # inout — x mutated in place
consume(x^)             # owned — explicit transfer, x is dead after this
```

Prior art: **Mojo** uses exactly this model. **Rust** is similar but with implicit borrowing and lifetime annotations instead of explicit parameter modifiers.

**Strengths:**
- Fine-grained control over ownership at each call site
- No hidden costs — every reference/copy/move is visible in the signature
- Compatible with `@noalloc` (no heap allocation needed)
- Maximum performance — the compiler knows exactly what each function does with its arguments

**Weaknesses:**
- Significant departure from Python syntax and feel (`borrowed`, `inout`, `owned` are not Python concepts)
- Every function signature carries ownership information — annotation burden
- Steep learning curve for Python programmers
- May be overkill for the common case where simple value semantics suffice

**Open questions:**
- Can the annotations be inferred in most cases, reducing burden?
- Is the `^` transfer syntax acceptable for a Python-first language?

### Other models considered but likely not suitable as the default

The following approaches have merits but introduce runtime overhead that conflicts with the goal of uniform performance across all code paths, including `@noalloc` hot paths. Using them as the default would require a separate model for performance-critical code, creating a hybrid system that is harder to implement and harder for programmers to reason about. They may still be useful as opt-in wrapper types (e.g., `Rc[T]` for shared ownership).

#### Compile-time reference counting (Lobster/Nim-style)

The compiler inserts reference counting operations automatically but uses static analysis to elide most of them. Move semantics are used wherever the compiler can prove the source is dead. RC only kicks in when sharing is needed.

Prior art: **Lobster** (compile-time RC with move optimization), **Nim's ORC** (move for locals, RC for heap, cycle detection), **Swift** (ARC with compile-time elision).

**Why not as default:** Even with optimization, RC operations may survive in hot paths. The compiler can't guarantee zero RC in `@noalloc` contexts without falling back to a different model. Cycle handling adds further complexity. Performance becomes dependent on optimizer quality rather than predictable from the source code.

#### Hylo-style mutable value semantics

All types have value semantics — no references as first-class values. Mutation through `inout` bindings with exclusivity guarantees. No aliasing, no borrow checker needed.

Prior art: **Hylo** (formerly Val), based on the "Mutable Value Semantics" paper by Racordon, Haller, et al.

**Why not as default:** Fundamentally incompatible with CPython's object model. The very common "find/reference element in container" pattern is unnatural — you can't hold a reference to an element. Object graphs and shared-state patterns require escape hatches that undermine the model.

#### Vale-style generational references

Every allocation is tagged with a generation number. References carry the expected generation. On access, generations are compared — mismatch means use-after-free, triggering a panic.

Prior art: **Vale**.

**Why not as default:** Runtime overhead on every field access (one integer comparison). Even though small, this conflicts with `@noalloc` / real-time guarantees where every cycle counts. Safety is runtime, not compile-time.

### Other models worth noting

**Region/arena allocation:** Objects are allocated in a region tied to a scope. All objects in the region are freed when the scope exits. Variables are pointers into the region. Limitation: objects can't outlive their region. Prior art: Cyclone, early Rust (before the borrow checker matured).

**Full borrow checking (Rust):** Move by default, explicit borrow references (`&T`, `&mut T`) with lifetime tracking. Maximum safety and performance. Cost: significant language complexity, steep learning curve, may be too far from Python's feel. Approach D (Mojo-style) is a simplified version of this.

**Copy-on-Write (COW):** Assignment shares the underlying storage (cheap pointer copy + refcount). Mutation triggers a copy only when there are multiple references. Prior art: Swift uses COW for `Array`, `String`, `Dictionary`. Note: COW still diverges from CPython (mutation doesn't propagate to aliases) and still involves refcounting.

### Examples of real patterns we need to support

**Pattern 1: Object graph / parent-child**
```python
class Node:
    children: list[Node]    # recursive type — can't be stack-allocated inline
    parent: ???             # back-reference — who owns this?
```

**Pattern 2: Cache / registry**
```python
cache = {}
def get_or_create(key: str) -> Thing:
    if key not in cache:
        cache[key] = Thing(key)
    return cache[key]        # caller gets a reference to cached object
```

**Pattern 3: Observer / callback**
```python
class Button:
    listeners: list[Listener]   # Button doesn't own the listeners

def setup():
    handler = MyHandler()
    button.add_listener(handler)
    # handler must outlive button, or at least the registration
```

**Pattern 4: Simple local computation (90% of code)**
```python
def process() -> Int32:
    p = Point(1, 2)
    p.x += 10
    return p.x     # no sharing, no aliasing — value semantics is perfect
```

The challenge: Pattern 4 is the common case and should be maximally efficient. Patterns 1-3 are less common but must be expressible. The ownership model needs to serve both without making either painful.
