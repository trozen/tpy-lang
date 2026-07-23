# Writing TurboPython code (for coding agents)

This file is a bootstrap guide for coding agents writing TurboPython (TPy)
code in downstream projects. It assumes you already know Python; the goal is
to describe only the delta and the conventions that lead to idiomatic TPy.

For the full language reference, see `TPY_LANGUAGE_FEATURES.md` (shipped
alongside this file). Only sections marked **Working** are usable today --
treat **Planned** / **Open** as non-existent.

---

## 1. What TurboPython is

TurboPython is a Python-to-C++ compiler. Source files are ordinary Python and
typically also run under CPython unchanged (dual-target), but are compiled to
C++ for performance. The compiler performs static type checking and produces
native binaries.

Why this matters for you as an agent writing code:

- Type-check statically: functions need parameter and return annotations;
  incorrect types are errors, not runtime failures.
- Stay Python-idiomatic: most features you'd write in Python work the same
  way. Reach for TPy-specific additions (`Own`, `Ptr`, `readonly`) only when
  the semantics genuinely require them.
- `Own[T]` is a *semantic necessity* for some patterns (ownership transfer,
  returning newly-created objects) -- it is not an optimisation knob.
- `Ptr[T]` and `readonly[T]` are *specialised tools*; well-written TPy
  rarely uses them.

---

## 2. Python -> TPy delta

The semantic gaps that actually bite when writing code:

- **Function signatures require type annotations.** Parameter types and the
  return type are mandatory. Local variable types are usually inferred.
- **Classes with annotated attributes get inline field storage.** There is
  no `__dict__`; you cannot add attributes not listed as annotations. If the
  same file must also run under CPython, add an `__init__` that assigns each
  field -- CPython doesn't create attributes from type annotations alone.
- **The `int` annotation means arbitrary precision (`BigInt`).** Use a
  fixed-width type (`Int32`, `Int64`, ...) when you want machine integer
  performance.
- **Untyped integer literals default to `Int32`** (configurable per
  project via `--default-int=Int64|BigInt`). Plain `x = 5` infers as
  `Int32`, not `BigInt`.
- **`str` is fine as a parameter type.** The compiler passes it as
  `std::string_view` in parameters and stores it as `std::string` in fields.
  You only need `StrView` / `String` / `Char` when you want explicit
  control.
- **`None`-safety is enforced.** Optional values must be narrowed (`if x is
  None` / `if x is not None`) before non-optional use.
- **Unions `A | B` are tagged variants.** Match them with `match`/`case`.
- **Ownership is explicit at a few specific boundaries** -- returning a
  newly-created object, consuming a parameter, and storing into persistent
  storage (fields, containers, globals). See section 5.

Everything else -- loops, comprehensions, f-strings, dicts, sets, tuples,
decorators, generators, match/case, context managers, async/await -- works
like Python.

---

## 3. Types at a glance

Import what you need from `tpy`; `from tpy import *` is acceptable for small
files. Core types:

| Category | Types |
|----------|-------|
| Numeric  | `int` (BigInt), `Int8`/`Int16`/`Int32`/`Int64`, `UInt8`/`UInt16`/`UInt32`/`UInt64`, `float` / `Float64`, `Float32`, `bool` |
| Text     | `str`, `StrView`, `String`, `Char`, `FStr` (parameter type that accepts an f-string without materialising a `str`) |
| Collections | `list[T]`, `dict[K, V]`, `set[T]`, `tuple[...]`, `Array[T, N]`, `Span[T]` |
| Bytes    | `bytes`, `bytearray`, `BytesView` |
| Optional / union | `Optional[T]` / `T | None`, `A | B` |
| Ownership markers | `Own[T]`, `Ptr[T]`, `readonly[T]` |
| Concurrency | `spawn` (`tpy.thread`), `Mutex[T]` / `RwLock[T]` / `Condvar` (`tpy.sync`), `Atomic[T]` (`tpy.atomic`), `Arc[T]` (`tplib.arc`), channels (`tplib.channel`) |

When to reach for each:

- **`int` vs fixed-width**: use fixed-width (`Int32` / `Int64`) for
  performance. Use `int` / `BigInt` only when you genuinely need arbitrary
  precision -- it is much slower.
- **`list[T]`** is the default growable container, same role as in Python.
  Use `Array[T, N]` for fixed-size stack-allocated arrays, `Span[T]` for
  non-owning views into contiguous data.
- **`Optional[T]`** for "maybe-a-value" slots. `Ptr[T]` is already nullable,
  so don't write `Ptr[T] | None` (the compiler warns and collapses it to
  `Ptr[T]`). `T | None` for non-value `T` and `Ptr[T]` interconvert at
  coercion boundaries (call args, returns, locals, field assignment) --
  both lower to `T*` at borrow positions, and storage-form Optional
  sources are lifted automatically when flowing into `Ptr[T]` slots.

### tplib at a glance

`tplib` ships TPy-native types that are too project-specific for the
language but common enough to standardize. Import as `from tplib import X`.

| Type | Use when |
|------|----------|
| `Box[T]` | You need a heap-allocated owning container -- e.g. recursive types, or oversized records you don't want stack-stored. Single owner. |
| `Rc[T]` | You need shared ownership (multiple owners of the same value, possibly with shared mutation). `@nocopy`; share via `.clone()`. Construct with `Rc.new(value)`. |
| `Weak[T]` | Non-owning companion to `Rc[T]`; breaks reference cycles. `from tplib.rc import Weak`. Mint via `rc.downgrade()`; recover a strong handle (or `None`) via `weak.upgrade()`. |
| `Arc[T]` | Atomic sibling of `Rc[T]` for shared ownership *across threads* (`Send + Sync` iff `T` is). `@nocopy`; share via `.clone()`. `from tplib.arc import Arc` (its own `Weak` lives there too). Canonical shared-mutable form: `Arc[Mutex[T]]`. |
| `ArrayList[T, N]` | Fixed-capacity list with stack-allocated storage -- avoids heap allocation when the upper bound is known. |
| `FixStr[N]` | Fixed-capacity string with stack-allocated storage. |
| `tplib.json` | JSON decoding/encoding via a `@model` class macro. |
| `tplib.channel` | Blocking MPSC channel for cross-thread handoff: `tx, rx = channel[T](capacity)`; clone senders per producer; `for item in rx:` ends when the last sender drops. |

For full details (semantics, tradeoffs, current rough edges), see
`LANGUAGE_FEATURES.md`.

---

## 4. Integer conventions

- Default fixed-int is **`Int32`** unless the downstream project configures
  `--default-int` otherwise. Check the project's build config (or just try
  compiling a snippet with a literal) before assuming.
- Prefer `Int32` for counters, loop variables, and small counts.
- Use `Int64` when you might plausibly exceed 2 billion (byte counts on
  large data, timestamps in nanoseconds, etc.).
- Use `int` / `BigInt` only when unbounded precision is required.
- Do not sprinkle `Int32(...)` constructors -- trust inference and write
  plain literals (`x = 5`, `xs = [1, 2, 3]`). Explicit constructors are
  only needed when the inferred type would otherwise be wrong.

---

## 5. Ownership and references

This is the one section where TPy semantics differ from CPython in
important ways. Read it once; most code you write afterwards will be
plain-`T` and the ownership markers will stay out of your way.

### 5.1 Mental model

In CPython, every variable is a reference to a heap object. TPy keeps that
behavior for **locals and parameters** (so `y = x` and `f(x)` behave the
same way as in Python), but differs for **persistent storage** -- fields,
list/dict/set elements, globals -- which *own* their values rather than
sharing references.

This single change (storage owns values) is the root of every ownership
rule below. Value types (`Int32`, `bool`, `float`, `str`, `Char`, ...) are
unaffected: they copy silently and never need `Own`.

The compiler internally distinguishes two C++ shapes for non-value types:
**storage form** at owned slots (fields, container elements, `Own[T]`
params, returns) and **borrow form** at non-owning slots (regular params,
locals, iterator yields). You rarely think about this directly --
ownership annotations like `Own[T]` and the copy warnings below are the
user-facing surface. The terms appear in compiler diagnostics ("borrowed
Optional/Union", "storage-form source"); `TPY_LANGUAGE_FEATURES.md` has
the full definition if you need it.

### 5.2 Plain `T` is the default -- use it almost everywhere

- **Parameters**: `def f(x: MyRecord) -> ...` -- passed by reference.
  Non-mutating parameters are auto-downgraded to const-reference.
- **Locals**: `y = x` aliases the same object, exactly like Python.
- **Returning references to caller-owned data** (e.g. an element of a list
  parameter): plain `T`.
- **Value types**: always plain. Never wrap `Int32` or `str` in `Own`.

### 5.3 When you need `Own[T]`

Three concrete triggers:

**(a) Returning a newly-created object.** Factory functions, constructors
that produce fresh values. The compiler is free to put the value on the
stack or the heap; what matters is that the caller now owns it.

```python
def make_point(x: Int32, y: Int32) -> Own[Point]:
    return Point(x, y)

def load_config(path: str) -> Own[Config]:
    ...
    return Config(...)
```

The same applies element-wise inside a returned tuple: a function
typed `-> tuple[str, Point]` is rejected (the `Point` element looks
like return-by-reference and the constructor temporary doesn't
outlive the return). Spell it `-> tuple[str, Own[Point]]` -- the
`Own[T]` rule applies per slot:

```python
def find(p: Point) -> tuple[str, Own[Point]]:
    return ("found", Point(p.x + 1, p.y))
```

This is the most common stumbling block when porting Python code that
returns multiple results bundled in a tuple.

**(b) A parameter that consumes its argument.** Use `Own[T]` for a
parameter when the function takes ownership of the value -- typically when
storing it into a long-lived container.

```python
def adopt(self, dog: Own[Dog]) -> None:
    self.kennel.append(dog)   # dog is consumed
```

If the caller's variable isn't used after the call, the compiler
auto-moves silently (see 5.5). If it *is* used afterwards, the compiler
copies and emits the warning from 5.4 -- the variable stays usable, but
you've paid for a copy.

**(c) Fields, list/dict/set elements, global variables.** These own their
values; this is the default. You write the field type as plain `T`
(`kennel: list[Dog]`), not `list[Own[Dog]]` -- ownership is implied by
storage.

### 5.4 CPython divergence and `copy()`

The compiler issues a warning when it would emit a copy that CPython would
*not* emit (CPython would share a reference instead). You fix it by making
the copy explicit with `copy` from `tpy`:

```python
from tpy import copy

def remember(self, dog: Dog) -> None:
    self.last_seen = copy(dog)   # explicit; both runtimes now match
```

The warning fires when a non-value-type lvalue (parameter, owned local
that's still alive afterwards, borrowed Optional/Union) is used as the
source of:

- A field assignment (`self.x = other`).
- A container op (`xs.append(other)`, `xs[i] = other`, `d[k] = other`,
  `d.update(other)`).
- An argument passed into an `Own[T]` parameter.
- A global assignment.

It does **not** fire when:

- The source is an rvalue (e.g. `xs.append(Point(1, 2))`).
- The source is a value type (`Int32`, `bool`, `float`, `str`, `Char`,
  ...).
- The source is at its last use (auto-move kicks in silently -- see 5.5).

Three ways to resolve the warning, in order of preference:

1. **Restructure so the source is dead afterwards** -- auto-move takes over
   and no `copy()` is needed.
2. **Construct fresh at the assignment site** -- rvalues never warn.
3. **Wrap the source with `copy(...)`** when you genuinely need both the
   original and a copy.

For iterating a container with per-element copies, there is a related
warning suggesting `copy_iter()` (copy each element on iteration) or
`copy()` (copy the whole container up front) -- pick based on which
behaviour you actually want.

This warning exists precisely so that dual-target code behaves identically
under CPython and the compiled binary. Never silence it without
understanding which path you want.

### 5.5 Auto-move at last use

You do not write `std::move` / explicit consume markers. When a variable
isn't used after being passed to an `Own[T]` parameter, the compiler moves
it automatically (no warning). If it *is* used after, the compiler copies
(with the warning from 5.4).

Structure code so ownership hand-off is the last use of the variable, and
you'll almost never see copy warnings.

### 5.6 `Ptr[T]` -- non-owning pointers stored in structures

The main valid use case is a structure that holds a reference to another
structure it doesn't own: back-pointers, observer lists, graph edges, cache
entries pointing into a larger data structure.

```python
class Node:
    parent: Ptr[Node]            # already nullable; assign None to clear
    children: list[Node]

root = Node()
child = Node()
child.parent = root              # plain assignment; no take_ptr() needed
```

`Ptr[T]` is always nullable; assign `None` to clear. Null dereferences are
checked at runtime.

Do **not** reach for `Ptr[T]` for:
- Function parameters -- plain `T` is better.
- Optional-by-reference -- use `Optional[T]`.
- Returning new values -- use `Own[T]`.
- Returning a reference to caller-owned data -- plain `T`.

For most assignments into `Ptr` fields or arguments, plain assignment
works (the compiler inserts an implicit address-of coercion, as in the
TreeNode example above). `take_ptr(x)` exists for the case where you want
to bind an explicit `Ptr[T]` *local variable* (or interop with native
code) and need to materialise the pointer yourself; you will almost
certainly never need it.

### 5.7 `readonly[T]` -- mostly compiler-inferred

The compiler infers const-ness automatically from your code's mutation
patterns (non-mutating parameters become const-ref, etc.). You write
`readonly[T]` explicitly in two situations:

- **C++ interop signatures**, where the mapping to `const T*` /
  `std::span<const T>` must be explicit.
- **Communicating intent on spans or pointers**: `Span[readonly[T]]`,
  `Ptr[readonly[T]]`.

In pure-TPy code you rarely write it by hand.

### 5.8 Advanced: consuming iteration

When you iterate a container that's at its last use, the compiler
automatically switches to consuming iteration -- elements are moved out
of the source rather than copied. You don't need to write anything
special:

```python
from tpy import Own

def drain() -> Own[list[Widget]]:
    src: list[Widget] = [Widget("a"), Widget("b")]
    dst: list[Widget] = []
    for w in src:           # src is dead after this loop -> auto-consumed
        dst.append(w)       # w is moved into dst, no copy warning
    return dst
```

If you need to force consuming iteration even when the source might be
used afterwards, wrap with `own_iter()` from `tpy` -- this consumes the
source explicitly. Reach for it only when you specifically need that
behaviour; the auto-consuming case above is fine almost always.

---

## 6. Writing idiomatic TPy

- **Annotate function signatures; let the compiler infer locals.** Only
  annotate a local when inference would pick the wrong type.
- **Prefer plain literals** (`1`, `"hello"`, `{1, 2}`) over explicit
  constructors and variable annotations.
- **Put logic inside functions**, not at module top level. Top-level code
  uses a different codegen path (globals as pointer slots); keep top-level
  to imports, constants, and a single `main()` call.
- **Use `match`/`case` for unions and enums.** It's the idiomatic dispatch
  form in TPy.
- **Narrow `Optional[T]` explicitly** with `if x is None` / `if x is not
  None` before non-optional use.
- **Prefer protocols over inheritance.** Protocols are structural; they
  don't require class hierarchies.
- **Keep `@native` / `@export` in a dedicated interop module**, not
  scattered across regular code.
- **Read copy warnings; don't suppress them.** They flag real CPython / TPy
  semantic divergences.

---

## 7. Worked examples

Only the things that differ from Python or that agents commonly get wrong.

**Record with fields and methods.**

```python
from tpy import Int32

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def distance_sq(self, other: Point) -> Int32:
        dx = self.x - other.x
        dy = self.y - other.y
        return dx * dx + dy * dy
```

**Factory returning `Own`.**

```python
from tpy import Own

def make_origin() -> Own[Point]:
    return Point(0, 0)

def main() -> None:
    p = make_origin()
    print(p.x, p.y)
```

**Union and `match`.**

```python
class Cat:
    name: str
    def __init__(self, name: str) -> None: self.name = name

class Dog:
    name: str
    def __init__(self, name: str) -> None: self.name = name

def describe(animal: Cat | Dog) -> str:
    match animal:
        case Cat():
            return f"a cat named {animal.name}"
        case Dog():
            return f"a dog named {animal.name}"
```

**`Optional` narrowing.**

```python
def greet(name: str | None) -> None:
    if name is None:
        print("hello, stranger")
        return
    print("hello,", name)   # here name: str
```

**Generic function with a protocol bound.**

```python
from tpy import Comparable    # builtin protocol: defines __lt__(self, other: Self)

def maximum[T: Comparable](a: T, b: T) -> T:
    if a < b:
        return b
    return a
```

For user-defined protocols, use `Self` from `typing` for methods that
reference the implementing type (so the bound `T` propagates correctly):

```python
from typing import Protocol, Self
from tpy import Own

class Clonable(Protocol):
    def clone(self) -> Own[Self]: ...

def clone_it[T: Clonable](item: T) -> Own[T]:
    return item.clone()
```

**Non-owning back-pointer.**

```python
from tpy import Ptr, Own

class TreeNode:
    value: Int32
    parent: Ptr[TreeNode]
    children: list[TreeNode]

    def __init__(self, value: Int32) -> None:
        self.value = value
        self.parent = None
        self.children = []

    # Own[TreeNode]: the tree takes ownership of the child so the
    # subsequent append() doesn't trigger a copy warning. The caller's
    # variable is auto-moved at last use (see 5.5).
    def add_child(self, child: Own[TreeNode]) -> None:
        child.parent = self
        self.children.append(child)
```

**Explicit `copy()` on field assignment.**

```python
from tpy import copy

class Cache:
    last: Point

    def remember(self, p: Point) -> None:
        self.last = copy(p)   # acknowledged copy; warning gone
```

**`async`/`await` with asyncio.**

```python
import asyncio
from tpy import Int32

async def work(n: Int32) -> Int32:
    await asyncio.sleep(0.001)
    return n * 2

async def main_coro() -> None:
    t1 = asyncio.create_task(work(5))
    t2 = asyncio.create_task(work(7))
    print(await t1 + await t2)

asyncio.run(main_coro())
```

Coroutines compile to stack-allocated state machines (no heap, no C++20
coroutines) driven by a single-threaded executor. The asyncio subset
covers `run`, `sleep`, `create_task` / `Task`, `gather`, `gather_list`,
`gather_list_settled`, `wait_for` (+ `TimeoutError`), `Future`, `Event`,
and exception-based cancellation (`Task.cancel()` / `CancelledError`).
`async for`, `async with`, async methods, and generic async functions all
work; `await` is allowed in arbitrary control flow.

**Threads with a channel.** Real OS threads use a Rust-style Send+move
API: `spawn(task)` takes ownership of a task object with a `run()`
method; `handle.join()` returns `run()`'s result. Cross-thread safety is
compile-checked -- moving a non-`Send` value across `spawn` or a channel
is a compile error, not a race.

```python
from tpy import Int32, Own, nocopy
from tpy.thread import spawn
from tplib.channel import channel, Sender

@nocopy
class Producer:
    tx: Sender[Int32]

    def __init__(self, tx: Own[Sender[Int32]]) -> None:
        self.tx = tx

    def run(self) -> None:
        for i in range(3):
            self.tx.send(i)

def main() -> None:
    tx, rx = channel[Int32](4)
    h1 = spawn(Producer(tx.clone()))
    h2 = spawn(Producer(tx))         # last sender moved in
    total = 0
    for v in rx:                     # ends when both senders drop
        total += v
    h1.join()
    h2.join()
    print(total)
```

For shared mutable state instead of message passing, wrap it in
`Arc[Mutex[T]]`: give each thread its own `arc.clone()` and mutate under
the lock (`with shared.lock() as g: ...` -- the guard derefs to the
payload). `RwLock[T]`, `Condvar`, and `Atomic[T]` cover the
reader-heavy, wait/notify, and lock-free-counter shapes.

---

## 8. Verifying your code

Compile and run a snippet with the downstream project's `tpy` invocation
(this varies per project -- check its README or AGENTS.md for the exact
command). A typical form:

```bash
tpy path/to/snippet.py               # compile + run
tpy --dump-code path/to/snippet.py   # print generated C++
tpy -O path/to/snippet.py            # release build
tpy --print-types                    # list every public type/function from lib/tpy/
```

`tpy --print-types` dumps a markdown reference with a TOC and every
record/protocol/function signature available in the `tpy`, `tplib`,
`typing`, and standard-library stubs. Useful when you need to confirm a
method exists, check a signature, or look up a protocol before writing
code. Pipe to `glow -p` for a nice terminal view.

Diagnostics are line-precise and usually name the fix. When a type error
is confusing, inspect the generated C++ with `--dump-code` -- the mapping
is usually clear enough to spot what the compiler inferred.

Re-running `tpy` on an unchanged program skips the whole pipeline and
executes the cached binary (~100ms), so tight edit-run loops are cheap.

If the code must also run under CPython (dual-target), run it with
`python` as well and confirm output matches. CPython reference-shares
values that TPy copies; that's exactly what the `copy()` warnings from 5.4
exist to prevent diverging on.

---

## 9. Current limitations (may be lifted)

The compiler is under active development. The following features are not
yet available but are on the roadmap:

- Dynamic attribute *storage* on compiled records -- fields must appear as
  class-level annotations (there is no `__dict__`). The fallback hooks
  `__getattr__` / `__setattr__` / `__delattr__` and the `getattr` /
  `setattr` / `hasattr` builtins *are* supported, but they route to those
  hooks; they cannot create undeclared fields.
- `match` sequence patterns (`case [a, b]:` / `case (a, b):`).
- The Python `threading` / `multiprocessing` *modules* are absent, and
  the asyncio executor is single-threaded (no cross-thread asyncio).
  Threads themselves are fully supported -- `spawn`/`join`, `Arc`,
  `Mutex`/`RwLock`, `Condvar`, `Atomic`, channels (see section 7) --
  just under TPy's own compile-checked API, not the `threading` module.

If you're tempted to use one of these, pick the closest fully-annotated /
single-threaded equivalent and leave a comment noting the dependency.

---

## 10. Where to go next

For the full language reference, see `TPY_LANGUAGE_FEATURES.md` (shipped
alongside this file). Only trust sections marked **Working**.

For the exact list of types, functions, and protocols available from
`tpy`, `tplib`, `typing`, and the standard-library stubs, run `tpy
--print-types`. This is the authoritative surface -- if it's not
there, it's not exposed.

Topics worth looking up there when you need depth beyond this guide:

- Ownership edge cases, escape analysis, move semantics.
- `Optional` narrowing rules and flow-sensitive analysis.
- `match`/`case` patterns (literal, record, union, optional, enum, guard,
  or-pattern, positional, polymorphic class dispatch).
- `async`/`await`, the asyncio subset, and cancellation.
- Threads and shared state: `Send`/`Sync` inference, lock guards,
  channel close semantics ("Concurrency" / "Thread Safety Markers").
- Union types (variant codegen, recursive unions).
- `@native` / `@export` native interop for C++ bindings.
- Authoring CPython extension modules (`# tpy: ext_module` + `@export`
  -- compile TPy to an importable CPython `.so`; "CPython Extensions").
- `@error_return` zero-cost error handling.
- Protocol definition and conformance.
