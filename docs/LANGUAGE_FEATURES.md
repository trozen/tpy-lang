# TurboPython Language Features

> **For coding agents**: when this file is installed in a downstream
> project (via `tpy --install-agent-docs`), it ships alongside
> `TPY_FOR_AGENTS.md` -- a concise bootstrap covering the Python-to-TPy
> delta, ownership rules, and idiomatic patterns. Start there; use this
> file for depth on specific features. Only trust sections marked
> **Working** -- treat **Planned** and **Open** as design notes, not
> available features.

Status legend:
- **Working** - Implemented now
- **Planned** - Will add
- **Open** - Could add with right design (notes on how)

---

## Design Philosophy

**Performance**: Low-latency compiled output. Most code uses standard Python constructs freely, while hot paths can opt into stricter constraints (e.g. `@noalloc`). No garbage collection, no interpreter overhead.

**Regular Python compatibility**: TurboPython aims to compile and run regular Python code wherever practical. When a feature is unsupported, or semantics differ from CPython (for example, inline field storage vs Python object references), diagnostics should explain the gap clearly. A program CPython runs that TPy rejects with `this construct is not yet supported by C++ code generation` is a shape the lowering does not handle yet, not a rule of the language -- those shapes are tracked as a queue (`scripts/thir_migration/review/`) and close one arm at a time.

**Constrained C++ interop**: Easy integration with existing C/C++ code, but only through explicit interop rules. TurboPython does not aim to accept arbitrary native C++ types or signatures without declared compatibility.

**Familiar syntax**: Keep the language approachable for non-programmers and close to regular Python. The everyday constructs (`int`, `str`, `list`, `dict`, functions, classes) should feel familiar, with minimal required annotations.

**Semantic transparency**: When TurboPython behavior intentionally differs from CPython, the compiler should warn so differences are visible during development and testing.

**Tooling-friendly**: Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins or language server protocols. Development uses the same tools as regular Python.

**Thread safety**: Unlike CPython (which relies on the GIL), TurboPython targets multi-threaded, high-performance environments. The compiler should produce thread-safe code by default where possible without sacrificing performance, and give the user explicit control where trade-offs exist. Compiler analyses (e.g. narrowing, aliasing) must be sound in the presence of concurrent access.

**Pluggable backends**: The mapping from TurboPython to C++ should be configurable. Different projects have different needs:
- `Span[T]` → `std::span<T>`, `Span[readonly[T]]` → `std::span<const T>`, or custom span types
- `print()` → `std::cout` (default) or a logging framework
- `str` → `std::string` or a custom string class

---

## Performance Profiles

Different code paths have different requirements. Performance profiles control what operations are allowed:

### Profile Levels (Open - designing the hierarchy)

**Level 0: Unrestricted** (default)
- All Python constructs allowed
- Dynamic allocation permitted
- Exceptions, virtual dispatch, etc.
- Use for: initialization, configuration, tests, most application code

**Level 1: Allocation-aware**
- Allocations allowed but tracked/logged
- Useful for profiling and finding unexpected allocations
- Use for: development, debugging hot paths

**Level 2: No heap allocation** (`@noalloc`)
- No `new`, no container growth, no string concatenation
- Stack and pre-allocated memory only
- Use for: hot paths, real-time code

**Level 3: Deterministic** (future)
- No allocation + no unbounded loops + no recursion
- Guaranteed O(1) or bounded O(n) operations
- Use for: interrupt handlers, hard real-time

### Applying Profiles

Profiles can be set at multiple granularities:

```python
# Module-level default
# tpy: profile=noalloc

# Class-level
@profile("noalloc")
class HotPath:
    def process(self, data: Span[int32]) -> int32:
        ...

# Function-level (overrides module/class default)
@noalloc
def critical_loop(data: Span[int32]) -> int32:
    ...

@alloc  # explicitly allow allocation in otherwise restricted context
def setup() -> Config:
    ...
```

Compiler flag can set project-wide default:
```bash
tpy --default-profile=noalloc src/
```

---

## Pluggable C++ Backends (Open)

The mapping from TurboPython types/functions to C++ should be configurable via backend modules:

```python
# tpy.backend.default - ships with tpyc
Span[T]     → std::span<T>
Span[readonly[T]] → std::span<const T>
str         → std::string
print(...)  → std::cout << ...
list[T]     → std::vector<T>

# tpy.backend.custom - example overrides for a user-defined backend
Span[T]     → mylib::span<T>
str         → mylib::fixed_string<256>
print(...)  → LOG_INFO(...)
list[T]     → mylib::static_vector<T, N>

# tpy.backend.embedded - custom for embedded
print(...)  → uart_printf(...)
list[T]     → etl::vector<T, N>
```

Usage:
```bash
tpy --cxx=embedded src/app.py
```

This allows the same TurboPython source to target different environments without code changes.

---

## Type Categories

TurboPython distinguishes between **value types** and **reference types**:

### Value Types
Small, cheaply copyable, passed by value:
- `int`, `float`, `int32`, `int64`, `bool`
- Small immutable structs (configurable threshold)
- `FixStr[N]` (fixed-size string)
- User records that extend `ValueType` (see [ValueType Protocol](#working-valuetype-marker-protocol))

### Reference Types
Larger, mutable, passed by reference to functions but stored inline in fields and containers:
- Classes/records (by default)
- `str` (dynamic string)
- `list`, `dict`, `set`, `Array[T, N]`
- `bytearray` (mutable byte buffer; `bytes` stays value-like since it is immutable)

A user container (a class holding a buffer, e.g. `tplib.ArrayList`) is an ordinary reference type. Since 2026-09-05 the compiler admits on that axis rather than on a list of builtin container names wherever it can: the arms it folded test "is this a reference type", so records and every builtin container reach them together instead of `list`/`dict`/`set` reaching them alone. At the four slots where the lowering used to run a record arm above a container arm -- the return slot, a field write, an lvalue ternary, and a method argument -- there is now ONE arm, so a source shape admitted for a record is admitted for a container by the same code rather than by a parallel copy that can drift. Two exceptions are deliberate and measured: the method gate shares only its RETURN half (routing a user record through the builtin-stub receiver loop rejects loudly at `method.arg_shape`, because the stub argument table has no record pass-through row), and the field write from a container prvalue (`self.lines = data.splitlines()`, `self.buf = [e] * n`) keeps its own row, because the reference row pins the source type to the slot to stop a subclass rvalue slicing into a base-typed field and a container has no subclass. (That same subclass admission is why the merged RETURN arms cannot carry the container ladder's slot-type pins: `dt == slot` at the bare name, the field read and the operator each reject an upcast source the record half routes -- measured, so the arms keep `_f1_ref` alone.) The fold is not uniform beyond that -- rows that still enumerate their families remain (they are the residue filed in `TODO.md`), so a `bytearray` or `Array[T, N]` can still reject at a row its `list` twin passes. A user container is a record to the compiler, so its own METHODS take the record path rather than a builtin stub's -- and a few slots still key on a name (the element-of-a-user-container-at-a-native-slot row is filed in TODO, as is the receiver membership list). Two decisions stay builtin-specific because they have no receiver type to resolve against: literal construction (`[1, 2]`, `{"a": 1}`, comprehensions -- there is no callee) and pending-literal resolution (which container type an un-annotated literal settles on).

### Parameter Passing Convention
For reference types, `T` in a parameter implicitly means reference:
```python
def process(data: MyClass) -> None:  # data is passed by reference
    data.value = 42  # modifies original
```

**Const reference optimization**: Record and container parameters that are never mutated (no field writes, no mutating method calls, no address-taking via `take_ptr(param)` or implicit value-to-`Ptr[T]` coercion, no borrow-form `Optional[T]` coercion, no mutable `Span` coercion) are automatically passed as `const T&` instead of `T&`. This applies transitively via call-graph propagation: if a wrapper only passes a parameter to a non-mutating callee, the wrapper parameter also becomes `const T&`. Applies to constructors too: a ctor whose body passes a record-typed param to a `Ptr[T]` callee (or to any mutating sink) emits `T&` instead of the default `const T&`. Protocol-typed parameters (both static template types and `@dynamic` base classes) receive the same optimization: a non-mutated protocol param emits `const T_x&` / `const Base&` instead of `T_x&` / `Base&`. For protocol params, a call to a non-`@readonly` protocol method counts as mutation of the receiver. Note that `T -> Optional[T]` only counts as address-taking when the destination is borrow-form (param / local / return); storage-form destinations (field / container element) copy the value into `std::optional<T>` and don't mutate the source param's binding. A mutation reached through an ALIAS of the parameter counts as a mutation of the parameter -- a plain `q = p` binding, an element/field borrow, and a `match`-arm capture (`case Cat() as c: c.hunger -= 1`) all follow the borrow back to EVERY storage they alias, so the match spelling and the `isinstance` spelling of the same body give the same verdict. An alias normally has one such storage; one that is RE-SEATED (a `match` capture a nested match rebinds, a local reassigned from a second container) holds a loan per source and a mutation through it reaches whichever is live, so every source is credited -- a capture bound over `self.field` and then over a parameter makes the method non-const AND the parameter mutable. The alias also inherits the storage's `readonly[...]`, so a write through a capture of a readonly subject is rejected at sema rather than by the C++ compiler. A LOOP VARIABLE is an alias of the storage it was iterated out of, in both directions: iterating a const source binds it const (below), and mutating it -- directly, or by lending a mutable borrow such as a tuple unpack's element -- credits the mutation to the iterable's root in turn, so a nested `for row in g.rows:` / `for b in row:` that writes `b` leaves `row` bound `auto&&` and `g` mutable. The source is read through the whole lvalue chain, so the iterable may equally be an lvalue CHAIN -- a field hop, a subscript hop, or a field hop followed by a subscript one, off a declared name, a parameter, `self`, a loop variable or a module global (`for b in rows[i]`, `for b in g.rows`, `for b in self.rows[i]`, `for b in row.cells[i]`, `for b in table[k]`), by list index or dict key -- since the element a hop lends is still the root container's storage. The chain stops where the iteration's borrow stops being CHECKABLE: the borrow is filed under the storage the iterable comes out of, and only a name or a one-hop `obj.field` path has such a key, so a deeper chain (`for b in self.grid.rows[i]`, `for b in self.inner.xs`, `for b in table[k].cells`, `for b in shelves[0].flat`, `for b in cube[i][j]`) keeps a located reject (BUGS.md#iter-borrow-place-needs-hops) -- admitting it would hand out references into storage no mutation of which could be matched against the loan. A chain whose TAIL is a `@property` is the one exception, and only outside a resumable body: the read is a getter CALL, so the iteration's loan comes from the getter's borrow provenance instead of from a key over the chain, and `for b in o.inner.items:` compiles at any depth -- with that loan filed on the receiver ROOT rather than on the container the getter handed back, so a mutation of THAT container during the loop is not diagnosed (BUGS.md#property-iter-loan-misses-getter-storage). A property in the MIDDLE of the chain (`for b in h.leaf.boxes` with `leaf` a getter) is not covered and keeps the reject. A getter that hands its result back BY VALUE is admitted with the rest: the read IS a call, so the loop takes the owning `auto __obj_N =` capture an `-> Own[list[T]]` method call always took, and a value-typed getter (`-> str`, bytes, a view) the same. Binding the result to a local first (`snap = c.snapshot` then `for x in snap:`) is no longer needed for the iteration, and is still the workaround for the two decl positions that reject: the same read branch-hoisted (both arms of an `if`/`else`, a `with` body, a `try`/`finally`), and a decl off a record-returning `-> Own[Rec]` getter, while the method spelling of either compiles (BUGS.md#own-property-iterable-materialize). Whether a getter returns by value is read at the INSTANTIATION, not from the annotation: a bare type-param getter (`-> T`) is spelled `val_or_ref_t<T>`, so at a reference argument it IS a reference and iterates exactly as its monomorphic twin does. Inside a GENERATOR or an `async def` the exception stops applying once the loop body SUSPENDS -- a `yield` or an `await` inside it: the frame then holds its iterators across every suspension, so a property iterable deeper than one hop keeps the located reject there. At ONE hop a by-value getter is admitted, the value-typed and view-returning ones included: the frame materializes the read into its own `__for_src_N` slot, the way it already did for the spelled-method twin. A loop whose body does not suspend lowers on the sync route wherever it sits and takes the verdicts above. The one-hop property iterable a frame does admit is not guarded against a mutation the CALLER makes between two resumes: the iteration's loan is intra-procedural while the frame's borrow is not (BUGS.md#frame-iter-loan-blind-to-caller). Binding a local first compiles and keeps the checked borrow on that local's own source, either the element (`v = self.grid.rows[i]` then `for b in v`) or the intermediate record (`g = self.grid` then `for b in g.rows[i]`). A CALL anywhere in the chain (`for b in self.get().xs`) keeps a located reject for a second reason: the call result is a temporary the loop's capture would outlive. That second rule is the general one, and it is what every hop of a chain has to satisfy wherever a chain is read: the hop must name storage the ROOT outlives, which a stored field read and a reference-yielding element read do, and a call, a SLICE (`rows[i:i + 2][j]` -- the slice is a fresh span over the receiver, not a fresh list) and a by-VALUE accessor do not. An accessor hop is admitted on exactly that test: a `@property` or a user `__getitem__` declared `-> T` hands back a reference and is a hop, its `-> Own[T]` sibling mints a temporary and is not (BUGS.md#chain-temporary-hop-rejected). A SPELLED zero-arg method passes the same test and is refused anyway, so `o.made.pair` is a chain where `o.made_m().pair` is not -- a queued widening, not a rule. A slice is still fine as the WHOLE iterable (`for b in xs[0:2]`), where the span is the loop's own -- mutating the underlying list inside that loop warns, since the append can reallocate the storage the span names. The other two chain readers have no borrow to file and so take the chain at any depth: the tuple-unpack source (`a, b = self.o.h.pair`) and a value-position subscript read (`len(cube[i][j])`, `return cube[i][j]`). Mutation of what is iterated is diagnosed at the same granularity: iterating an ELEMENT (`for b in rows[i]`) warns on a reallocating method or an assignment through an element of that container (`rows[j].append(...)`, `rows[j] = [...]`) as well as on a mutation of the container itself, while iterating the CONTAINER (`for row in rows`) warns only on the latter, since replacing or growing an element leaves the outer iteration valid. The element case compares the two indices, so what the warning claims is what was proved: two int or str LITERALS that differ name different elements and are not warned at all (`for v in rows[0]: rows[1].append(v)` is valid Python and compiles clean); the identical literal or the identical name is warned as certain (*Mutation of 'rows[...]' while iterating over it*); and anything in between -- a literal against a name, two different names, a negative literal (`rows[-1]` is `rows[0]` in a one-element list), an arithmetic index -- still warns, worded as possible (*Mutation of 'rows[...]' may hit the element being iterated*). A fourth wording covers a loan the iteration no longer owns: rebinding the local a loop iterates retargets the ITER loan onto the outer container and MERGES it with that local's own element loan, which keeps the element hop but loses both the ITER kind and the index, so a write through the container reads *Mutation of 'cube[...]' may hit a borrowed element ('append' may invalidate references)* (pinned by `tests/cases/list/warn_iter_mutation`). A `readonly[...]` source is unaffected: the write through the element is rejected at sema, so nothing marks the source mutated and both bindings stay const. A GENERATOR or `async def` body takes the same verdict as its non-yielding twin: the resumable frame captures a non-mutated reference parameter as `const T&`, so a caller holding its own borrow can drive the generator, and every slot derived from that capture follows -- the loop's `begin_iter_t<const C>` iterators, the universal `iter_type_t<const S>` iterator, a pointer-form local or `match` capture bound out of it (`const T*`), the element pointers of a tuple unpack off it, and a `.items()` / `.values()` view over it. The frame's const bindings are transitive: a loop variable iterating a const source is itself const, so a nested loop, an alias (`a = d[0]`) or a view taken inside that loop is spelled const in turn. That last one is not a qualifier on the named type but a different type (`dict_items_view<K, const V>`), since the method is `@auto_readonly` and C++ resolves which half of the pair runs from the receiver; the frame therefore spells the slot by deduction from the call, the way the sync body's `auto` does. The verdict is the only way into that const capture: spelling the parameter `readonly[...]` explicitly still rejects on a resumable body (`def gen(d: readonly[dict[K, V]])` errors *this construct is not yet supported by C++ code generation (res.local_storage)*), while the identical body with a bare `dict[K, V]` param compiles and captures `const`.


**Element-ref deferral**: taking an element reference (`v = items[i]`) does not immediately mark `items` as mutated. The source container is only marked when the borrowed element is actually written through (field write, subscript write, or pass to mutating callee). This allows read-only element-ref patterns to preserve `const T&` for the container param:

```python
def read_elem(items: list[Point]) -> int32:
    v = items[0]    # deferred -- items not yet marked
    return v.x      # read only -- items stays const T&
    # C++: int32_t read_elem(const std::vector<Point>& items)

def write_elem(items: list[Point], val: int32) -> None:
    v = items[0]    # deferred
    v.x = val       # write through -- items now T&
    # C++: void write_elem(std::vector<Point>& items, int32_t val)
```

```python
def read_point(p: Point) -> int32:
    return p.x  # const Point& p in C++

def mutate_point(p: Point) -> None:
    p.x = 99    # Point& p in C++ (field write detected)
```

### Borrow Form vs Storage Form

A non-value type has two C++ representations depending on whether a slot
*owns* its data or *borrows* it from somewhere else. The compiler picks
the form per slot context and inserts conversions at the boundary; this
section is what those terms mean.

- **Storage form** -- the slot owns its data: class fields, container
  elements (`list[T]` / `dict[K, V]` / `set[T]` / `tuple` slot in a
  field or container), `Own[T]` parameters, function return values.
  Self-contained values: `T`, `std::optional<T>`, `::tpy::Union<A, B>`,
  `std::tuple<...>`, `std::vector<T>`, `std::string`. One carve-out: a
  union whose members are ALL `Ptr[T]` spells `::tpy::Union` here
  too -- it already holds borrowed references, so there is nothing for
  the owning form to own, and it compares through the pointee at every
  position.

- **Borrow form** -- the slot is borrowed from storage that lives
  elsewhere: regular function parameters, locals bound from a borrowed
  source, iterator yields, generator `__next__()` returns. Indirect
  references into existing storage: `T&` (for standalone records), `T*`
  (for pointer-repr `T | None` AND for any non-value tuple element --
  a reference can't be a `std::tuple` member, so tuple borrows are
  uniformly pointer-element form `std::tuple<T*, ...>`),
  `::tpy::Union<A*, B*>` (the borrow form of a non-value `A | B`,
  which owns Python's comparison rule through the pointee),
  `std::string_view`, `std::span<T>`. Generic tuple elements use the
  `tpy::val_or_ptr_t<T>` trait (the pointer sibling of `val_or_ref_t`),
  which instantiates to `T*` for non-value `T` and `T` by value
  otherwise, keeping generic and concrete tuple ABIs compatible.

For value types (`int32`, `bool`, `char`, etc.) the two forms coincide
-- they're cheaply copyable, so the value form serves both roles.

For non-value types, the codegen inserts conversions at boundaries
between the two forms. The common helpers:

| Boundary | Storage form | Borrow form | Lift / lower |
|----------|--------------|-------------|--------------|
| pointer-repr `T \| None` | `std::optional<T>` | `T*` | `tpy::ptr_to_optional` / `tpy::optional_to_ptr` |
| pointer-variant `A \| B` (non-value) | `::tpy::Union<A, B>` | `::tpy::Union<A*, B*>`, const-pointee at a non-mutating parameter | `tpy::to_ptr_variant` / `tpy::to_const_ptr_variant` / inverse |
| `tuple` element-wise (`T \| None` slot -> `std::optional<T>`, plain non-value slot -> `T`) | `std::tuple<std::optional<A>, B, ...>` | `std::tuple<A*, B*, ...>` | `tpy::tuple_to_storage[_move]` / `tpy::tuple_to_pointer` (per-element dest-shape dispatch; mixing both slot kinds is fine) |
| `str` | `std::string` | `std::string_view` | implicit C++ conversion |
| `bytes` | `::tpy::Bytes` | `::tpy::BytesView` | explicit `::tpy::Bytes(span)` -- the owned type's own constructor (span -> buffer is not an implicit conversion) |

A container method's ELEMENT-typed parameter splits by what the method does with it. At a LOOKUP slot (`list.remove` / `index` / `count`, `set.remove` / `discard`, the membership read, a `dict` key) the argument is READ-ONLY, so a `str` / `bytes` key passes as a view like any other `str` / `bytes` argument and the RUNTIME compares it against what it stores -- `std::string` against `std::string_view` and `::tpy::Bytes` against `::tpy::BytesView`, both directly (`runtime/cpp/include/tpy/lookup_key.hpp`). `ordered_set` and `ordered_map` both give their hash table that transparent hash/equality pair, so a set or dict lookup probes with the key as it arrives and builds none -- and a bare string LITERAL (`d["a"]`, `s.discard("b")`) is a lookup form of `std::string` for the same reason, so it does not allocate either. Neither container needs a `std::hash` specialization on a standard type, which [namespace.std] forbids. At a STORING slot (`append` / `insert` / `add`, `d[k] = v` -- the `Own[T]` spellings), where the runtime does not build the element itself, the borrowed argument materializes the owned form: `std::string(x)` / `::tpy::Bytes(x)`. Nothing in the compiler names a method here; the slot's `Own[T]` spelling is the whole rule.

A GENERIC `T` parameter is spelled off the instantiation's C++ type -- `param_val_or_ref_t<T>` -- which IS the TPy type's own parameter form for every type, because each of `str` / `String` / `bytes` / `bytearray` renders a distinct C++ type: the trait answers `std::string_view` at `str`, `const ::tpy::String&` at `String`, `::tpy::BytesView` at `bytes` and `::tpy::ByteArray&` at `bytearray`. So a generic over `str` or `bytes` binds the caller's view bare, exactly as the monomorphic twin does, and costs no allocation. A slot that is spelled CONST -- an explicit `readonly[T]`, a `@readonly` method's bare `T`, a record constructor's -- renders `::tpy::readonly_form_t<T>`, the same parameter form const-qualified only where it is a mutable reference, so a reference instantiation keeps its `const T&` and a view instantiation keeps the view. `const T&` would bind neither the view nor anything a generic BODY could materialize, since `T` is not fixed there. The body positions a view slot cannot serve go through runtime helpers keyed on `T` alone: `::tpy::param_to_storage<T>` at a store into `T` storage, `::tpy::param_to_return<T>` at a by-value `T` return, and `::tpy::eq` for an open-`T` equality (whose operands may arrive in the parameter and the storage form). Which arguments owe such a temp is decided at the INSTANTIATION, not on the open `T`: a value-typed instantiation's slot is `const T&`, which binds an rvalue for the full expression, so a literal or a by-value call renders inline exactly as it does at the monomorphic twin's slot; a reference-typed one's is the mutable `T&`, which binds none, so the temp stays. A GENERATOR or COROUTINE factory is no exception at a value-typed instantiation: its frame copies the argument into its `val_or_ref_t<T>` member -- `val_or_cref_t<T>` for a const slot, the same copy-at-a-value-type rule -- inside the full expression, so the argument renders inline there too; at a reference-typed instantiation the frame keeps a reference, which the uniform frame rule below (a frame never receives a temporary) already hoists -- decided on the slot, not on what the frame does with it. Rendering inline restores C++'s unspecified argument evaluation order at these slots, which the temp incidentally serialized: `f(side("A"), side("B"))` at a value-typed `T` runs B before A under g++ where CPython runs A then B. That is the monomorphic twin's behaviour too (`BUGS.md#subexpression-right-to-left-eval`), so the generic and its twin agree; the fix belongs to that entry, not to this slot. In a position with no statement to hoist a needed temp into -- a compound `while` or an `elif` condition -- the call is rejected with a located message rather than emitted uncopied; a call whose arguments render inline needs no such position, a generator factory at a value-typed instantiation included.

In either variant form the union's members must render DISTINCT C++ types.
Every str/bytes type owns one -- `bytes | bytearray`, `str | String`,
`list[uint8] | bytes` and `Span[readonly[uint8]] | BytesView` all discriminate
correctly (`tests/cases/bytes/distinct_spelling_union`); the view pair was the
last to split, when `tpy.BytesView` became `::tpy::BytesView`, its own type
over the bare span. The lowering still refuses a union whose members would
share a spelling (`std::holds_alternative` / `std::get` are ill-formed on a
repeated alternative), a guard no builtin pair reaches any more.

`Own[T]` is the explicit user-facing marker that forces *storage form*
at a parameter or return slot, transferring ownership at the call
boundary. At a `Own[tuple[T, ...]]` slot, the storage-form rule is
applied per element (`tuple[Own[T_ref], T_value, ...]`); the
move/copy decision at the call site is then made per the same
per-element auto-move + copy-required rules as field assignments.

A tuple is *owned storage* only when EVERY non-value element is owned. A
**mixed** tuple -- one owned element and one borrowed (`tuple[Own[A], B]`,
the shape a per-element-`Own` return produces) -- has no single form: its
owned half wants storage, its borrowed half a pointer, so it renders
`std::tuple<A, B*>` and stays a `const&` borrow at a parameter rather than
taking the ownership-transfer `std::tuple<...>&&` ABI. Storing one into any
owning slot therefore materializes the borrowed half: the slot holds
`std::tuple<A, B>` and the borrowed element is COPIED. That copy is a
divergence from CPython, which aliases, and it is acknowledged at most
sinks -- a container element (literal, `append`/`insert`, subscript-assign,
comprehension, loop variable), a dict value, and a field warn per element
(`copies B into owned storage (tuple element i)`), silenced by `copy()`,
which yields the fully-owned storage form `Own[tuple[A, B]]`. Two sinks
copy SILENTLY and are tracked in `BUGS.md`: a *nested* tuple slot
(`q = (make_mixed(b), 1)`, whose borrowed element sits a level deeper than
the per-member check inspects) and a module GLOBAL.

At a LOCAL the mixed render is the local's own shape at every binding path
-- a straight bind, a rebind, a `try`-hoisted or branch-hoisted decl, a
walrus, and a generator / `async def` frame slot -- so the owned element
stays by value and the borrowed one keeps pointing at the caller's object.
No storage slot is materialized on the way in; rebinding to an all-borrow
STORAGE source is the one shape with no aliasing answer (the local's owned
element is by value, so the source's element is copied into it) and it
warns like the other owning sinks. A mixed tuple wrapped in `Optional`
(`tuple[Own[A], B] | None`, spellable only as a return) does not build yet
-- see `BUGS.md`.

An *inferred* container element type takes the same owned storage form the
annotation rule forces you to spell -- `list[tuple[A, B]]`, since `Own` in
a local, field or container annotation is rejected as redundant. Element
markers surviving into the slot would render as C++ reference members
(`std::tuple<A, B&>`), which is how the same store once aliased under a
fixed `Array` and copied under a `vector`.

An owning `T | None` (pointer-repr Optional) **local** keeps the borrow
form (`T*` + a function-scope materialization slot) -- so reference
aliasing matches CPython (`x = A; y = x; x = B` leaves `y` on `A`) -- but
at its **last use** it MOVES its slot's value into an owned sink (field /
container / `Own[T]` param / matching `Own[T] | None` return) via
`ptr_to_optional_move`, rather than copying. An aliased or non-last-use
value still copies. So `T | None` is the owned-nullable local spelling;
`Own` is unnecessary (and rejected) on a local.

You almost never write or think about this distinction directly --
the compiler picks the right form per slot and emits the conversions
silently. This auto-lift runs at any pointer-form consumer site
(call args, returns, var-decl from storage source, narrowed
field/method access) regardless of source shape -- record field,
container subscript (`pairs[i]` where `pairs: list[P | None]`),
for-loop variable iterating a storage container, or comprehension/
genexpr unpack variable bound from a storage-form tuple slot. The lift
also fires at the second-tier boundaries that were once missed: a walrus
binding (`if (t := obj.opt) is not None:`), a ternary joining a borrow
arm and a storage arm (`p if c else obj.opt`, Optional and Union), and a
container-element store (`xs[i] = p`, `xs.append(p)`). A reference-type
ternary whose arms differ in aliasing -- one variable arm, one fresh
value arm (`a if c else [9]`) -- copies the variable arm where CPython
would alias it; this is warned, and `copy()` acknowledges it.

A reference-type lvalue used as a **container-literal element** -- a
`list`/`set`/`dict` literal element or key/value (`xs = [p]`,
`{k: p}`) -- is likewise copied into the container's owned storage where
CPython would store a reference, so it carries the same `copies T into
owned storage` warning that `.append`/`.insert` and field assignment
emit; `copy()` acknowledges it. A **last-use** source instead MOVES into
the container (matching `.append`/`.insert`), and a fresh rvalue is
constructed in place -- neither warns, and a `@nocopy` last-use element
is accepted rather than failing the C++ build. (A generic element type
carries the declaration-time hedge instead, matching `.append`.)

A **value-tuple with reference (pointer-repr) members** warns per such
member (errors for `@nocopy`), matching the subscript/field-assignment
path, in two shapes: a *whole-tuple lvalue source* (`d[k] = items[0]`,
`{k: items[0]}`, `xs.append(items[0])`, `xs: list[...] = [items[0]]`)
copies every reference member, and a *fresh tuple literal* element
(`xs: list[...] = [(1, c)]`) copies each member whose source is an
lvalue reference (a fresh rvalue member constructs in place and a
last-use local moves) -- including a `const` member source (a plain
reference param, or a comprehension loop var: the transient borrow tuple
fed to `tuple_to_storage` uses a const pointer slot). A dict-literal
*value* tuple literal still hits a pre-existing build error rather than
the warning -- tracked in `BUGS.md`.

The check walks into **nested value tuples**: a reference member of an
inner tuple (`[(1, (2, c))]`) is copied into storage exactly like a
direct one, so it warns too, with the depth spelled in the element path
(`tuple element 1.1`). Only a *direct* reference element gives a tuple a
borrow form, so a nested one lands in owned storage even at a plain
local binding (`q = (make_pair(b), 1)`) -- that binding warns where the
direct spelling (`q = make_pair(b)`, a borrow-form local) does not.

It matters when:
- You see a copy warning mentioning "borrowed Optional/Union" -- that
  source is in borrow form and the slot wants storage form.
- You're writing `@native` interop and need to match the C++ ABI shape
  of a TPy type.
- You're reading `--dump-code` output and a `tuple_to_storage` /
  `optional_to_ptr` helper appears -- that's the boundary conversion.
- You `yield`/`return` a tuple **local** with a *freshly-constructed*
  non-value member (`t = (i, Box(i)); yield t`) -- rejected, the member
  would dangle even as a pointer (fix: `Own[T]` on that element). A
  *durable* reference member SHARES: `t = (i, b); yield t` (and aliases,
  ternaries, branch merges of such locals) hands out a pointer borrow of
  `b`, so post-boundary mutation reaches the original object exactly as
  CPython aliases it. The same holds for a param-sourced tuple
  (`def gen(p: tuple[int32, Box]): yield p`) and a call-returned one
  (`u = make(); yield u`).
- You bind a tuple local from a **storage location** (`t = items[0]`,
  `t = h.pair`) -- the binding ALIASES the stored element (CPython
  semantics): mutation through `t` reaches the source. Returning such a
  tuple (directly or through the local) lifts element addresses into
  that storage, so the root must outlive the call: params / `self` /
  globals are fine; function-local containers, `Own[T]` params, and
  `self` in a consuming method are rejected at sema (use `Own[...]`
  elements to return by value).

The borrow/storage-form duality is implicit in today's sema -- one
`TpyType` per value, the form is decided per context at codegen. Making
form a first-class type fact is open IR work; see `IR_DESIGN.md` Open
Questions item 9.

---

## Types

### TurboPython -> C++ Type Mapping

Full mapping of TurboPython types to their C++ representation. Where parameter representation differs from storage, both are shown.

| TurboPython | C++ |
|-------------|-----|
| `int` | `tpy::BigInt` (arbitrary precision) |
| `int8/16/32/64` | `int8_t/int16_t/int32_t/int64_t` |
| `uint8/16/32/64` | `uint8_t/uint16_t/uint32_t/uint64_t` |
| `float` / `float64` | `double` (IEEE 754) |
| `float32` | `float` (IEEE 754 single precision) |
| `bool` | `bool` |
| `str` | `std::string` (parameters: `std::string_view`) |
| `String` | `::tpy::String` (parameters: `const ::tpy::String&`) |
| `StrView` | `std::string_view` |
| `char` | `char` |
| `None` (function-return slot, e.g. `def f() -> None`) | `void` |
| `None` (every other annotation slot -- function params, locals, fields, type-args like `Future[None]`/`Own[None]`/`list[None]`, tuple elements, ...) | `std::monostate` |
| `T \| None` (T value type) | `std::optional<T>` |
| `T \| None` (T non-value, params/returns/locals) | `T*` (borrow form -- pointer-repr Optional) |
| `T \| None` (T non-value, fields/containers) | `std::optional<T>` (storage form) |
| `tuple[T1, T2, ...]` | `std::tuple<T1, T2, ...>` (per-element rules apply; see Tuples below) |
| `dict[K, V]` | `tpy::ordered_map<K, V>` |
| `set[T]` | `tpy::ordered_set<T>` |
| `list[T]` | `std::vector<T>` |
| `Array[T, N]` | `std::array<T, N>` |
| `Span[T]` | `std::span<T>` |
| `Span[readonly[T]]` | `std::span<const T>` |
| `SpanIter[T]` | `tpy::SpanIter<T>` |
| `A \| B` (value types) | `::tpy::Union<A, B>` (a `std::variant` that owns Python's comparison rule -- see Union/Optional) |
| `A \| B` (non-value, params/returns/locals) | `::tpy::Union<A*, B*>` (borrow form -- pointer-variant), `::tpy::Union<const A*, const B*>` at a parameter the body does not mutate through (see the Union/Optional section). Members must render distinct C++ types -- every str/bytes type owns one, the view pair included (`tpy.BytesView` is `::tpy::BytesView`, `Span[readonly[uint8]]` the bare span) |
| `A \| B` (non-value, fields/containers) | `::tpy::Union<A, B>` (storage form -- the SAME type a value union spells, so one type owns Python's comparison rule at every owning position) |
| `Ptr[A] \| Ptr[B]` (every position) | `::tpy::Union<A*, B*>` -- the members are already borrowed references, so this union takes the borrow form's HEAD even where a union owns; one type serves both forms, so no carve-out is needed. Only the alias and a parameter signature reach codegen today (BUGS.md#ptr-member-union-unreachable) |
| `Ptr[T]` | `T*` |
| `Ptr[readonly[T]]` | `const T*` |
| `Ptr[None]` / `Ptr[readonly[None]]` | `void*` / `const void*` (preserves the C/C++ opaque-pointer idiom for `@native` interop, despite `None` lowering to `std::monostate` at other type-argument positions) |
| `Ptr[P]` where `P` is a `@dynamic` protocol | `P*` (pointer to the @dynamic base class). Non-owning polymorphic reference; method calls dispatch through `P`'s vtable. Static (non-`@dynamic`) protocols are rejected as pointer element types -- they have no runtime representation. See `docs/DYNAMIC_PROTOCOL_DESIGN.md` for the owning siblings (`Box[P]` / `Rc[P]`). |
| `Own[T]` | `T` (by value; valid only in param/return types -- rejected on locals and fields, which own their value inline. An owned nullable local is just `T \| None`: it owns the value and moves it out at its last use; no `Own` spelling) |
| `Box[T]` | TPy class wrapping `Ptr[T]` (heap-allocated owning container); `@nocopy`, explicit `.clone()` to duplicate. Construct via `Box(value)` where `value: Own[T]`. |
| `Rc[T]` | TPy class wrapping `Ptr[_RcCellBase]` (strong/weak counters + virtual bookkeeping) + `Ptr[T]` (payload, aliases into the cell's inline storage); `@nocopy`, explicit `.clone()` to share. Construct via `Rc.new(value)`. One heap allocation per `Rc.new` -- the cell is generic `_RcCell[U]` (derived from `@dynamic _RcCellBase`) and holds the payload inline via `UninitArrayStorage[U, 1]`. Works for both concrete T and abstract `@dynamic` P; structural conformers of a `@dynamic` T are wrapped at the call site as `Adapter<T, U>` so the inline storage holds a type that inherits T's vtable. |
| `Weak[T]` | Non-owning companion to `Rc[T]`; shares the cell but doesn't keep the payload alive. `@nocopy`. Mint via `rc.downgrade()`; recover a strong handle (or None) via `weak.upgrade()`. |
| `Atomic[T]` (`T: AnyFixedInt`) | `@nocopy` but movable, `Send + Sync` wrapper over `std::atomic<T>`. `from tpy.atomic import Atomic, MemoryOrder`. Full `std::atomic<integral>` op surface (`load`/`store`/`exchange`, `fetch_add/sub/and/or/xor`, `compare_exchange[/_weak]`) plus a module-level `fence`. Each op takes a `MemoryOrder` (a `@native("std::memory_order")` enum) that **defaults to `SEQ_CST`**, so casual use needs no ordering (`c.fetch_add(1)`); CAS returns `(succeeded, observed)`. Ergonomic in-place operators `+= -= &= |= ^=` (atomic RMW at seq_cst) and `str`/`repr` (snapshot print); binary operators and implicit `int()` are deliberately omitted (they'd make the non-atomic `a = a + 1` look valid). |
| `Arc[T]` (arc) | TPy class mirroring `Rc[T]` (`tplib/arc.py`) but with `Atomic[uint32]` strong/weak counters, so handles cross threads. `@nocopy`; `Send + Sync` iff `T` is (conditional override). Construct via `Arc.new(value)`; share via `arc.clone()`. `from tplib.arc import Arc` (not re-exported from `tplib` -- keeps the atomic runtime out of non-threaded consumers). |
| `Weak[T]` (arc) | Non-owning atomic companion to `Arc[T]`. `@nocopy`. `from tplib.arc import Weak` -- module-scoped, coexists with `tplib.rc.Weak`. |
| `Mutex[T]` / `RwLock[T]` | Blocking locks wrapping `std::mutex` / `std::shared_mutex` (`tpy.sync`, `from tpy.sync import Mutex, RwLock`). `@nocopy`; the interior-mutability primitives. both `Mutex[T]` and `RwLock[T]` are `Send + Sync` iff `T: Send` (conditional override). For `RwLock` this is looser than Rust's `RwLock<T>: Sync iff T: Send + Sync`, and sound on the safe surface (a safe not-`Sync` `T` is always a container whose shared-mutability the readonly read guard removes; TPy has no safe interior mutability, unlike Rust's `Cell`). It is *not* sound for a `Send`-but-not-`Sync` interior-mutable payload built with the unsafe `unsafe_interior_mutable` hatch (a user `Cell`-analog): the bound fabricates a `Sync` the author never asserted -- a latent hole, low priority; see `BUGS.md` / `docs/SEND_SYNC_DESIGN.md`. `lock()` (Mutex) / `read()` / `write()` (RwLock) are `@readonly` -- you lock through a shared handle, so `arc.lock()` works -- and return a `@nocopy` `Deref[T]` guard used as a context manager: `with m.lock() as g: g.append(x)` (deref forwards to the payload; value-type payloads use `g.get()`/`g.set()`). `set()` takes `Own[T]` -- it moves into the lock's storage, so replacing a reference payload with a still-live lvalue warns (copy-into-owned-storage; silence with `set(x.copy())`), while an rvalue/last-use moves in cleanly. The guard acquires the lock in `__enter__` and releases in `__exit__`; a guard never entered never blocks. Canonical shared-mutable form is `Arc[Mutex[T]]`, which each spawned thread reaches through its own `arc.clone()`. |
| `Condvar` | Blocking condition variable (`tpy.sync`, `from tpy.sync import Condvar`) over `std::condition_variable`. `@nocopy`, `Send + Sync` unconditionally (the primitive is internally synchronized, like `Atomic`). `@readonly` `wait(guard)` / `notify_one()` / `notify_all()`. `wait` takes the live `Mutex` guard directly (`with m.lock() as g: cv.wait(g)`), atomically releases the lock it holds, blocks until notified, then reacquires; callers re-check their predicate in a loop (spurious wakeups possible). The guard is taken via a monomorphized structural hook, so `wait(g)` is a static call and `_RawMutex` never appears in the surface. Pairs with `Mutex` for blocking producer/consumer handoff across threads. |
| `bytes` | `::tpy::Bytes` (parameters: `::tpy::BytesView`) |
| `bytearray` | `::tpy::ByteArray` (mutable; parameters: a reference) |
| `BytesView` | `::tpy::BytesView` (a `std::span<const uint8_t>` subclass carrying the family's `==`, `<=>` and `std::hash`) |
| `basic_slice` | `tpy::BasicSlice` (start, stop) |
| `slice` | `tpy::Slice` (start, stop, step) |
| `Any` | `tpy::Any` (std::any + per-type ops table) |

### Numeric
- **Working**: `int` (Python's int -> `tpy::BigInt` arbitrary precision, custom runtime implementation)
- **Working**: `float` (Python's float -> `double`, 64-bit IEEE 754)
- **Working**: `float32` (32-bit single precision -> `float`), `float64` (alias for `float`)
- **Working**: `int8`, `int16`, `int32`, `int64`, `uint8`, `uint16`, `uint32`, `uint64`, `bool`, `char`
- **Working**: a binding under a scalar type's name hides the type wherever that binding is visible, so a later `int32(x)` calls the variable. Valid Python, so it warns rather than rejects, at the binding: parameters, locals, tuple-unpack, walrus, `for` and `with` targets and nested-def names (`'int32' shadows the tpy type 'int32' for the rest of this function`), module-level statements (`... for the rest of this module`), and comprehension variables and `except ... as` names (`... within its scope`). The check keys on the imported type, so `from tpy import int16 as i16` makes a local `i16` warn and a local `int16` not. Not covered: `match` captures, and a scalar re-exported through a user module. Rename the binding or use the qualified `tpy.int32`.

**Naming rule for scalar types.** A lowercase name denotes a machine representation and sits in the same register as Python's own `int`, `float`, `bool`, `str`: `int8`..`int64`, `uint8`..`uint64`, `float32`, `float64`, `char`. A CapWords name denotes an abstraction over a representation: `String`, `StrView`, `BigInt`, `Span`, `Array`, `Own`, the protocol names. Future scalars follow the rule (`int128`, `float16`, `bfloat16`, `complex64`); a vector type composes the scalar instead of minting a name per lane count (`Simd[int32, 4]`). `int` stays Python's arbitrary-precision integer; the fixed widths are the opt-in. The names coincide with numpy's dtype names, so `from tpy import int32` and `from numpy import int32` in one module shadow each other; import one of them qualified.
- **Working**: `%` on floats follows Python floor semantics (sign-of-divisor) -- `-1.5 % 2.5 == 1.0`, `7.0 % -3.0 == -2.0`, `-0.0 % 3.0 == 0.0`. Distinct from `math.fmod`, which keeps C truncation semantics (sign-of-dividend) for compatibility with CPython's `math.fmod`.
- **Working**: `int.bit_length()` returns the number of bits to represent `abs(self)`, matching CPython (`(0).bit_length() == 0`, sign is ignored). Returns `int32`.
- **Working**: `float.as_integer_ratio()` / `int.as_integer_ratio()` return the exact `(numerator, denominator)` pair (`tuple[int, int]`) in lowest terms with a positive denominator, matching CPython; `float('inf').as_integer_ratio()` raises `OverflowError` and `nan` raises `ValueError`. (Available on `int`/`float` typed values; a bare `int`/`float` literal keeps its `IntLiteral`/`FloatLiteral` type and doesn't expose the method.)

`int` stores values from `-(2**62)` through `2**62 - 1` inline. Comparisons,
floor division, remainder, `divmod`, shifts, and bitwise operations avoid heap
allocation when their inputs and integer results fit this range. Larger results
promote to arbitrary-precision storage. True division retains its correctly
rounded algorithm and can allocate for intermediate values even with small inputs.

#### Default Integer Type for Unannotated Literals (Working)

Unannotated integer literals (`x = 42`) use the configured default integer type, controlled by `--default-int` (default: `int32`). Explicit `int` annotations always mean `BigInt`:

```python
x = 42          # int32 (default), or int64/BigInt with --default-int
y: int = 42     # always BigInt (explicit annotation)
```

The compiler performs range-safe fallback: if a literal's value exceeds the configured type's range, it automatically falls back to `BigInt` with a warning:

```python
a = 2147483647   # int32 (fits)
b = 2147483648   # BigInt with warning (exceeds int32 range)
c = -2147483648  # int32 (exactly int32 min)
d = -2147483649  # BigInt with warning (below int32 min)
```

Constant folding is uniform: every variable-free integer binop (`1 << 100`, `2 ** 40`, `1024 * 1024 * 1024 * 4`) is evaluated at compile time wherever it appears, and the slot it lands in decides only how the folded literal is spelled -- never whether the fold happens. The range check above applies to the folded result, so a value too wide for its slot is a sema error rather than an overflowing chain of run-time checked ops.

See `docs/INTEGER_INFERENCE_DESIGN.md` for the full design rationale.

#### Mixed Arithmetic and Type Promotion (Working)

All fixed-width integer types use checked arithmetic (panics on overflow). Operations between fixed-width types and `int` (BigInt) follow Python's promotion rules - the result is always the wider type:

```python
x: int32 = 5
y: int = 10     # explicit int (BigInt)
z = x + y       # Result is int (BigInt), not int32
```

| Operation | Result Type | Rationale |
|-----------|-------------|-----------|
| `int32 + int32` | `int32` | Both operands same type, checked arithmetic |
| `int32 + int` | `int` | Promotes to BigInt to avoid overflow |
| `int + int32` | `int` | Promotes to BigInt to avoid overflow |
| `int32 + literal` | `int32` | Literal coerces to target type |

**Implicit widening**: Smaller fixed-width integers widen to larger ones automatically:
- Signed: `int8` → `int16` → `int32` → `int64`
- Unsigned: `uint8` → `uint16` → `uint32` → `uint64`
- Cross-sign: `uint8` → `int16`, `uint16` → `int32`, `uint32` → `int64`

`int32 → uint64` and similar same-or-narrower cross-sign conversions are deliberately **not** implicit -- negative signed values don't round-trip through unsigned types. Direct integer literals at a fixed-width call site (`f(0)` for `f: uint64`) bypass this gap because the value is known at compile time; non-fitting literals (`f(-1)`, `f(300)` for `uint8`) are rejected at compile time with a range error.

**Implicit `int` (BigInt) → fixed-width narrowing**: a `BigInt` value flows into a fixed-width-int slot without an explicit cast via a runtime range check that panics on overflow (the same `to_fixed_check` the explicit constructor uses). This applies at scalar positions (annotated assignment, call argument, return), at `list` / `tuple` literal element positions (`ports: list[int32] = [get_port()]`), and at subscript key/index positions against the receiver's declared key/index width (a `dict[int64, T]` key or a `list` index narrows to that width; a BigInt-keyed `dict` or a user `__getitem__` taking `int` passes the key through unchanged -- `d[2**40]` works like CPython). Membership (`in` / `not in`) is the exception: it is a value question, so a BigInt needle against a fixed-int-keyed `dict`/`set` (or their views, or fixed-int sequence elements) never range-panics -- an out-of-width needle is simply absent (`2**70 in d` is `False`, like CPython), an int needle in `bytes` raises `ValueError` outside `range(0, 256)` (also like CPython), and only a user `__contains__` with a declared fixed-int param takes the standard call-arg narrow. `dict` / `set` literal elements do not yet accept it (a coercible element is rejected at compile time) -- see BUGS.md.

**Literal-seeded local retro-widening**: A function-local initialized from a non-negative integer literal (`offset = 0`, `n = 5`) without an annotation takes the configured default integer type at the assignment, then retroactively re-types to a fixed-width target the first time it flows into a typed slot the standard widening rules can't reach -- call argument, return value, annotated init, reassignment to a typed local, container element, field assign, or dict-key in subscript-assign. This makes `offset = 0; _pcre_match(..., offset, ...)` work when the parameter is `uint64` without an explicit cast. The lock is one-shot: the first such use pins the local's type, and a later use demanding a different type produces a type-mismatch error with a hint pointing at the locking site. Reassigning the local from a non-literal source (`offset = some_func()`) drops the seed; module-level globals and collection-element literals are not covered by retro-widening (see `INTEGER_INFERENCE_DESIGN.md`). This is distinct from the runtime-checked `BigInt -> fixed-width` element coercion noted above, which applies to a direct `BigInt` *value* at a list/tuple element regardless of retro-widening.

Negation (`-x`) is only available on signed types — unsigned types produce a compile error.

**Explicit cross-type casts**: Any fixed-width integer type can be explicitly converted to any other using the constructor. Out-of-range values panic at runtime:

```python
from tpy import int8, int32, uint8

a: uint8 = uint8(42)
b: int32 = int32(a)      # OK: widening, always safe
c: uint8 = uint8(b)      # OK: narrowing, panics if b > 255

d: uint8 = uint8(int32(300))  # Runtime panic: uint8 overflow
e: uint8 = uint8(-3)          # Compile error: out of range literal
```

**Truncating conversion** (`trunc`): For wrapping/modular conversion without panicking, use the `trunc()` static method:

```python
from tpy import int8, int32, uint8

x: int32 = int32(300)
print(uint8.trunc(x))       # 44 (300 % 256)
print(uint8.trunc(int8(-1))) # 255

# Also accepts BigInt (int)
print(uint8.trunc(2**100 + 42))  # 42 (low 8 bits)
```

**Float promotion**: Any operation involving `float` promotes to `float`:

| Operation | Result Type | Rationale |
|-----------|-------------|-----------|
| `float + float` | `float` | Both operands same type |
| `float + int` | `float` | Float is wider than int |
| `int + float` | `float` | Float is wider than int |
| `float + IntN` | `float` | Float is wider than any fixed-width int (signed or unsigned) |
| `float32 + IntN` | `float32` | Result keeps `float32`; mixing with `float` widens to `float` |
| `int / int` | `float` | True division always returns float, correctly rounded like CPython (raises `ZeroDivisionError` on zero divisor, `OverflowError` when the quotient exceeds double range) |
| `int32 / int32` | `float` | Fixed-width true division, same CPython-parity semantics (int64/uint64 magnitudes beyond 2^53 route through the correctly-rounded BigInt path) |

`float + bool` (e.g. `3.0 + True`) is currently rejected, unlike CPython where `bool` is an `int` -- `bool` does not extend the `AnyFixedInt` marker the float operators widen over (see BUGS.md).

For augmented assignment (`+=`, `-=`, `*=`, `/=`, etc.), behavior depends on whether the variable has an explicit type annotation:

**Explicitly annotated variables** -- the annotation is preserved; the right-hand side is converted to match. If the operation would produce a wider type, it is a compile error (same as a regular assignment mismatch):
```python
total: int32 = 0
big_value: int = 10  # int (BigInt)
total += big_value  # big_value converted to int32, then int32 addition
total *= big_value  # same: converts to int32 first

y: int32 = 10
y *= 1.5            # error: '*=' produces float but 'y' is annotated as int32
```

This ensures fixed-width variables stay in the checked arithmetic domain. If the BigInt value is too large for the target type, the conversion panics at runtime.

**Unannotated (inferred) variables** -- if the operation produces a wider type, the variable widens to match (same as Python semantics):
```python
x = 14       # inferred as int (BigInt)
x *= 1.3     # x widens to float; result is 18.2
```

**Cross-width FixedInt aug-assign is an error** -- assigning a wider integer type with `+=`/`*=` etc. where the result would silently truncate is rejected:
```python
i = int16(2)
i += int64(3)   # error: '+=' is not supported between int16 and int64
```
Use an explicit cast if narrowing is intended: `i += int16(int64(3))`.

#### Float Literal Adaptation (Working)

Bare float literals (`2.0`, `1.5`) carry an unresolved `FloatLiteralType` that adapts to context, analogous to how integer literals adapt to `int32`/`BigInt`. The default when no context forces a specific type is `float` (64-bit):

```python
x = float32(1.5)
x *= 2.0            # stays float32 -- literal adapts to float32 context
x *= float64(2.0)   # widens to float -- explicit float64 forces widening

y = 2.0             # float (default)
z: float32 = 2.0    # float32 -- annotation forces adaptation
```

This means `float32` arithmetic stays in single precision without requiring explicit `float32(...)` wrappers on every literal.

### Strings
- **Working**: `str` type -- context-dependent: `std::string` by default, `std::string_view` for parameters
- **Working**: `String` (`tpy.String`) -- explicit owned `::tpy::String`, a `std::string` subclass (parameters use `const ::tpy::String&`)
- **Working**: `StrView` (`tpy.StrView`) -- explicit `std::string_view`
- **Working**: `char` type for single characters (str-like: `str + char`, `char + str`, `char + char` concat, `char * n` / `n * char` repeat, `len(c)` returns 1, `ord(str)` with runtime length-1 check)
- **Working**: a `char` (what `s[i]` yields, where CPython yields a one-character `str`) is admitted at these `str` / `String` / `StrView` slots and spelled as the one-character string: return, local decl, module-level global, parameter (`str`, `Own[str]`, `StrView`), record field (in a method and in `__init__`), `list` / `Array` / annotated-tuple literal element, set and dict literal element, `list.append`/`insert`/`__setitem__`, `set.add`, dict key (read, store and `del`) and dict value, a generator's `yield` slot, and a comprehension element. Elsewhere it still wants the explicit `str(s[0])`: a membership needle (`s[0] in xs`, BUGS.md#char-needle-membership-rejects), an unannotated tuple literal (BUGS.md#char-tuple-literal-admission), and five of the slots in BUGS.md#char-at-str-residual-slots (`+=` onto a `str`, a `str | None` field, a ternary arm, a compare against a longer literal, a `*args: str` pack). A borrowing `str` parameter (and any other view-form `str` slot: a dict lookup key, `list.index`/`remove`, `set.discard`) takes the char as a view of the runtime's static one-character table, so nothing is allocated and the view never dangles
- **Working**: String concatenation with `+` and `+=`
- **Working**: `str()` conversions (e.g. `str(42)`) safe to store in variables (no dangling)
- **Working**: `list[str]` generates `std::vector<std::string>`
- **Working**: `Final[str]` generates `constexpr std::string_view` (compile-time constant)
- **Working**: `str.split()`, `str.split(sep)`, `str.split(sep, maxsplit)` -- returns `list[str]`
- **Working**: `str.join(items)` -- joins iterable of strings
- **Working**: `strip`/`lstrip`/`rstrip`, `replace`, `find`/`rfind`/`index`/`rindex`, `startswith`/`endswith`, `upper`/`lower`, `capitalize`/`title`/`swapcase`, `count`, `isdigit`/`isalpha`/`isalnum`/`isspace`/`isupper`/`islower`, `removeprefix`/`removesuffix`, `splitlines`
- **Working**: `str.replace("", sep)` inserts separator between every character (Python semantics)
- **Working**: `str.count("")` returns `len(s) + 1` (Python semantics)
- **Working**: For-each iteration over string literals (no null terminator leak)
- **Working**: F-strings (`f"hello {name}"`) via `std::format` -- supports format specs (`.2f`, `#x`, `>10`), `!s` conversion, all scalar types
- **Working**: `FixStr[N]` -- fixed-capacity string, stack allocated (via `tplib`); supports `__str__() -> StrView` for zero-copy printing

#### String Type Semantics (Working)

`str` is context-dependent, matching Python's actual semantics where parameters are borrowed and returns/fields are owned. Locals are inferred: `std::string_view` when safe (literal, param, narrowed `str | None` param deref, Array element, a ONE-HOP record field read off a name, `list[str]` element, `dict[K, str]` value source, an enum member's `.name`, tuple-unpack target, or a ternary / `and`-`or` compound of any of these), `std::string` when ownership is needed. In a generator / `async def` body the "safe" set shrinks to static-lifetime sources (literals, `Final` constants, an enum member's `.name` -- it views `EnumUtil`'s static member-name table, pinned by `enum/enum_name_owned_sinks::frame_view`) and explicit-view PARAMS (whose borrow is checked at the call; a local sourced from an explicit-view LOCAL promotes, since that binding's borrow is unchecked against the frame lifetime): any other view local would be hoisted into the resumable frame, which outlives the case-block temps and suspensions the sync judgment assumes the binding shares scope with, so those locals resolve owned instead (the locals side of the owned param capture rule). That param capture -- a bare `str`/`bytes` parameter held as OWNED storage in the frame -- is a filed defect, not the intended design (`BUGS.md#generator-frame-copies-str-param`): the declared type of such a parameter is a view, a record parameter of the same frame is held by reference, and the caller already keeps the argument alive for the frame's whole lifetime. Retiring it should revisit the locals restriction stated just above, since a view local sourced from a param is as safe in a frame as in a sync body once the param is itself a view. An explicit `x: str = ...` annotation on a local names the FAMILY, not the storage -- the same view-vs-owned resolution applies (a never-mutated view-safe source stays a zero-copy view); use `String` for guaranteed owned storage:

```python
def greet(name: str) -> str:   # param=string_view, return=std::string
    return "Hello, " + name

class Config:
    name: str                  # field = std::string (owned)

s = "hello"                    # local = std::string_view (literal source)
t = name                       # local = std::string_view (param source)
u = str(42)                    # local = std::string (owned source)
v = "start"
v += " end"                    # local = std::string (augmented assignment)

# Array/record/list/dict sources are view-safe unless the source is mutated:
arr: Array[str, 3] = ["a", "b", "c"]
x = arr[0]                     # local = std::string_view (stable storage)
p = Config("test")
y = p.name                     # local = std::string_view (stable storage)
p.name = "new"                 # source mutated -> y falls back to std::string

names: list[str] = ["alice", "bob"]
a = names[int32(0)]            # local = std::string_view
names.append("carol")          # source mutated -> a falls back to std::string

d: dict[str, str] = {"key": "val"}
b = d["key"]                   # local = std::string_view
d["key"] = "new"               # source mutated -> b falls back to std::string

# A FIELD read borrows in one shape only: the ONE-HOP `str` read `p.name`
# above, off a NAME receiver. A read through two or more hops, off a
# temporary or through a `@property` (a hop that dispatches to a getter mints
# a temporary the path does not own), and EVERY `bytes` field read, take an
# owned copy: their source has no storage key a later write could be resolved
# to (`bytes` additionally did not compile at all before this rule, so a view
# there would be a new dangle rather than a preserved one). What keeps the
# one-hop view correct is the demotion list: a write to that field, a
# mutating method on the record, an ALIAS bind of it (`m = p` hands out a
# second write path), and the record reaching a parameter that is not
# `readonly`. A write to a SIBLING field never demotes. The escape rule reads
# the callee's SIGNATURE, not its body -- a callee in another module is
# compiled separately, so "does it actually write" is not a question this
# decision can ask, and asking a whole-program verdict here would make the
# local's form depend on where its callee lives. The list is known to be
# incomplete (BUGS.md#field-view-escape-needs-place): a callee writing the
# record through a module GLOBAL, a `*args` pack element, a nested-def
# closure capture and a write through a SECOND name over the same container
# element (a loop variable, or the element subscript itself) each replace the
# buffer with no demotion, which is why that entry's design row makes escape
# a borrow-tracker fact.
q = o.inner.tag                # local = bytes (owned copy, 2 hops)

# Compound sources (ternary / and-or, including nested) follow the same rule,
# borrowing EVERY arm's storage; mutating any root demotes the local to owned.
m = names[0] if cond else d["key"]  # std::string_view (borrows names AND d)
names.append("dave")                # any root mutated -> m falls back to std::string

# A narrowed `str | None` param dereferences to its contained string_view, so
# a local bound to it (plain or in a compound arm) stays a zero-copy view:
def pick(opt: str | None, b: str) -> None:
    if opt is not None:
        z = opt if len(b) > 0 else b   # std::string_view (no owned copy)
```

For explicit control, use `String` or `StrView` from the `tpy` module:

```python
from tpy import String, StrView

def process(name: String) -> String:   # const ::tpy::String& in, ::tpy::String out
    return name

def peek(data: StrView) -> StrView:    # string_view in, string_view out
    return data
```

`StrView` is a non-owning view, so the compiler enforces safety restrictions:
- Cannot be used as a record field (dangling risk -- use `str` or `String`)
- Cannot use `+=` (would dangle -- use `str` or `String` for mutable strings)
- Returning a `StrView` referencing a local or temporary is an error. This
  covers view-returning methods (`return a.strip()`), f-strings (`return
  f"..."`, `return f"..."[a:b]`), and the owned-`bytes`-as-`BytesView` sibling.
  A view-returning str/bytes method's result borrows its receiver (the
  registration heuristic stamps the receiver-borrow for body-less view-returning
  stubs), so the check sees the receiver lifetime uniformly; an unrecognized
  view-typed return expression fails closed (treated as dangling).
- An *explicit* `StrView`/`BytesView` annotation is a promise not to copy, so a
  binding whose source dies at end-of-statement is an error (`v: StrView =
  make()` / `make().strip()`): drop the annotation (the local then owns) or
  annotate `str`/`bytes`. An *inferred* local takes the storage its bindings
  need -- an owned copy where a source is a temporary (`s = make().strip()`
  becomes an owned `str`, matching CPython) or where any binding of the same
  local owns, a zero-copy view where every binding borrows a
  parameter/global/stable local. The join is the strictest binding's demand,
  and it is proven more strictly after the first binding, so the two orders of
  one pair of bindings can differ in whether the local copies -- never in its
  type, and never towards the unsafe side
  (`BUGS.md#view-storage-join-stricter-after-first-binding`). The rule is
  per-FUNCTION: a module-level slot makes no storage decision at all and keeps
  its first binding's spelling
  (`BUGS.md#module-level-view-slot-ignores-owned-binding`). A generator or
  `async def` body runs the same join -- its locals are frame fields, so a view
  of one outlives every suspension -- except for a source whose buffer the frame
  does not OWN, which owns there: one the frame does not hold at all (a loop
  variable, a tuple-unpack temp), and a reference-typed one (a `bytearray`
  parameter), which the frame holds as a reference to the caller's object that
  the caller may grow across a suspension. A frame field that does own its
  buffer is a valid root for a yielded view, so a generator may yield a view of
  an OWNED `str`/`bytes` local -- the borrow is live until the consumer's next
  step, like every borrow yield. A `String` local is the same owning frame
  field but is not accepted as such a root yet
  (`BUGS.md#string-local-not-view-root-for-yield`). Collecting those yields
  with `list()` rather than stepping them keeps the views past the frame's
  death (`BUGS.md#collect-iterator-of-views-into-dead-frame`).
- Storing a temporary into a view-typed container element is an error
  (`xs: list[StrView] = []; xs.append(make())`), since the temporary dies at
  end-of-statement.
- Mutating the backing storage of a live view warns (`v = a.strip(); a += ...`),
  as `+=` reallocates the buffer.
- Returning (or yielding) a borrow of a module global that any function in the
  module rebinds through `global` is an error, naming the global and the
  rebinding function. A module global outlives every call, so the
  local-or-temporary check passes it -- but `global S; S = ...` reseats the
  slot and frees the buffer an outstanding view points into, and the rebind
  can sit in a different function than the borrow. The rule covers every
  borrow return form (`StrView` / `BytesView` / `Span`, a reference-typed
  global, `Optional[T]`, a non-value tuple member). For a VIEW over a
  value-typed global's buffer the escape is returning the owned type
  (`-> str`); for a reference-typed global there is no owning escape to offer,
  since rebinding one is rejected in its own right (`Cannot reassign global
  variable ... of non-value type`) -- the remedy is to stop rebinding, and
  which of the two errors fires depends only on which function sema reaches
  first. A value element COPIED out of a global is not a borrow of the slot,
  so a tuple carrying one is left alone exactly as the scalar return of the
  same expression is. A `global X` a body never writes only reads the slot and
  is not a rebind. A walrus counts as a rebind wherever the emit treats it as
  one -- including inside a LAMBDA body, where Python would bind the lambda's
  own local and TPy does not
  (`BUGS.md#lambda-body-walrus-binds-enclosing-scope`). What the rule asks is whether the return points INTO the slot, so a
  global that already holds a HANDLE is exempt: returning a `Ptr[T]` (or a
  `Span[T]` over someone else's buffer) global copies the handle, and
  reseating the slot leaves what the copy points at untouched. The exemption
  reads the SLOT's own type and never asks where the handle points, so a
  handle global aimed at another global's buffer is exempted on the same
  grounds (face (2) of `BUGS.md#rebound-global-borrow-open-sinks`). Three faces
  stay open: a rebind in the module that DEFINES an imported global, a
  module-level rebind with no `global` statement, and the non-return borrow
  sinks -- a field store and a caller-side local
  (`BUGS.md#imported-global-view-outlives-foreign-rebind`,
  `BUGS.md#returned-view-outlives-module-level-rebind`,
  `BUGS.md#rebound-global-borrow-open-sinks`).

A `str` / `bytes` FIELD is READ through every receiver, chained ones included (`o.inner.tag`,
`rs[0].name`), but is WRITTEN only through a narrower set: a name, `self`, a nested field
(`o.mid.inner.name = "x"`), an unproven Optional name, an unproven Optional over a field, and a
loop variable -- each measured to demote a live view of the written field to an owned copy -- at
module level as well as in a function, method, constructor, generator, `with` body,
`try`/`finally` and `match` arm. A user `__getitem__` element (`bag[0].name = "x"`) is admitted
too, but does NOT demote: that dangle predates the receiver work and stays listed as an open
escape in `BUGS.md#field-view-escape-needs-place`. Five receivers are rejected at the write
(`assign.field_write_shape`), because a view of the field would survive the write with no
diagnostic (same entry): a `Ptr` (a parameter or one held in a record field), a tuple element, a
container element (`rs[0].name = "x"`, `d["k"].name = "x"`, and the `*args` pack subscript), a
borrow-returning call (`b.get().name = "x"`), and a `@property` getter (`h.val.name = "x"`). The
workaround for all five is to name the record first -- take it by reference instead of `Ptr`, or
bind the element / call / getter result to a local (`r = rs[0]; r.name = "x"`), which borrows
it, so the container still sees the write. The `int32` field
beside them keeps every receiver: no view borrows a scalar. At the OWNING insert sinks -- a
comprehension element (`[r.name for r in rows]`), an insert argument (`acc.append(o.inner.tag)`),
a container-literal element, and both halves of a subscript assign (`d["k"] = i.name`, and the
KEY the same way) -- the member read lands BARE and the owned slot constructs from it; a
VIEW-typed member (a `StrView` field) takes an explicit `std::string(v)` copy there instead,
since the slot owns and the view does not.

`t: str | None = <source>` declares an owned-inner Optional (`std::optional<std::string>`), so it
COPIES the source at BOTH positions -- the straight-line declaration and the branch-first one. A
BORROW-form source (a `str` param, a `StrView` local or field, a `bytes` param) constructs the
inner first (`std::optional<std::string> t = std::string(s);`), because the optional's converting
ctor from a view is explicit; an OWNED source (a `str` field, whose member read is already
`std::string`) lands bare. `str` and `bytes` are value types, so the copy is what CPython's
rebind-the-attribute semantics give: mutating the source field afterwards leaves `t` alone.

Reassigning an inferred string local to an incompatible type is a sema error
(`s = "asd"; s = 5`), not a deferred C++ build failure -- consistent with how
scalar locals reject incompatible rebinds.

When a generic type parameter `T` is inferred from a string literal, sema picks
`T = str` (storage form `std::string`), not `T = StrView`. The auto-downgrade
to `StrView` was unsound (it could silently demote owned-string returns through
callable parameters into dangling views) and was removed; users who want explicit
view semantics in a generic call write `f[StrView]("...")`. Generic returns and
yields of view types are still subject to dangling-view checks at sema (a lambda
or generator that materializes an owned string at a `StrView` return slot is
rejected with a clear diagnostic). The trade-off is one `std::string`
materialization at the call site for generic-over-`str` calls vs. zero materialization
for direct-`str` calls -- SSO covers short literals; longer ones heap-allocate
once per call. Tracked as a known asymmetry to be closed by descriptor-based
generic codegen post-IR migration (see `docs/IR_DESIGN.md` Open Questions item 8).

#### F-string Formatting (Working)

F-strings use `std::format` as the backend:

```python
s = f"x={x}"
# -> std::string s = std::format("x={}", x);

s = f"{val:.2f}"
# -> std::format("{:.2f}", val);
```

Bool and float use Python-compatible wrappers for default format (no spec):
`tpy::bool_to_str()` ("True"/"False"), `tpy::float_to_str()` (Python-style).

**Supported conversions:**
- `!s` conversion: applies `str()` / `__str__()`
- `!r` conversion: applies `repr()` / `__repr__()`
- User types with `__str__()`: `f"{obj}"` dispatches to `__str__()`
- User types with only `__repr__()`: `f"{obj}"`, `str(obj)`, `print(obj)` fall back to `__repr__()` (matches Python)
- Containers (tuple, list, dict, Array, Span): `f"{container}"`, `f"{container!s}"`, `f"{container!r}"` all stringify using runtime to_str helpers (matches `print()` output)
- Unions (`A | B`): `print(u)`, `str(u)`, `repr(u)`, `f"{u}"`, `f"{u!s}"`, `f"{u!r}"` all visit the active variant alternative and dispatch to that member's `__str__` / `__repr__`. Each member must individually be string-renderable. (Exception: a recursive-union alias holding a top-level `str` member renders it repr-quoted under `print`/`str` -- see BUGS.md.)
- `repr(str)` and string elements inside containers (`print([s])`, `print({s})`) emit Python-faithful escape form: outer quotes plus `\\`, `\n`, `\r`, `\t`, the active quote, and `\xNN` for other ASCII control bytes. Quote selection prefers `'`; switches to `"` when the string contains `'` and no `"` (matches CPython).
- `__str__() -> StrView` is accepted (zero-copy; protocol-safe coercion to `str`)

**Not yet supported:**
- `!a` conversion
- Expressions inside format specs (`f"{x:{width}}"`)
- `__format__` dispatch on user types
- Print streaming optimization (`print(f"...")` currently allocates)

**Planned `@noalloc` interaction:**

```python
# @noalloc context - error (allocates)
s = f"x={x}"  # ERROR: f-string allocates in @noalloc context

# Fixed-size string (future) - no heap allocation
buf: FixStr64 = f"x={x}"

# Format string passthrough (future) - zero overhead
def log(fs: FormatString) -> None: ...
log(f"x={x}")
# -> log("x={}", x)  # format string + args passed separately
```

### Bytes

- **Working**: `bytes` type -- immutable byte sequence -> `::tpy::Bytes` (a `std::vector<uint8_t>` subclass; parameters take the view `::tpy::BytesView`)
- **Working**: `bytearray` type -- mutable byte sequence -> `::tpy::ByteArray` (the same base, a distinct type; a reference type, so parameters and locals alias)
- **Working**: `BytesView` (`tpy.BytesView`) -- non-owning read-only view -> `::tpy::BytesView`, a `std::span<const uint8_t>` subclass. Its own type so the family has an `==`, a `<=>` and a `std::hash` the standard does not give a span: `bytes` / `bytearray` / `BytesView` compare, order, hash and serve as lookup keys through it in every pairing, with bare operators, the way `str` / `String` / `StrView` do through `std::string_view` (`tests/cases/bytes/view_type_compare`)
- **Working**: Byte literals (`b"hello"`, `b"\x00\xff"`) -- use static storage (C++ string literal) when used as `BytesView` or function arguments (zero heap allocation)
- **Working**: `bytes(n)` zero-fill constructor, `bytes(b)` / `bytearray(b)` copy constructors, and the same three spellings as a `bytearray` field's constructor member-init (`self.buf = bytearray()` / `bytearray(seed)` / `bytearray(n)`). Caveats: `bytearray(n)` with a runtime `BigInt` size fails the C++ build (`BUGS.md#bytearray-size-bigint-unnarrowed`), and a `bytearray` field initialized in a constructor from an ordinary FUNCTION call (`self.buf = make(n)`) still rejects (`BUGS.md#bytearray-ctor-field-and-list-conv`).
- **Working**: `bytes(iter)` / `bytearray(iter)` from `Iterable[uint8]` (fast path) or `Iterable[int32]` (runtime range-checked 0..255); generator expressions work too
- **Working**: `bytearray.extend(iter)` accepts `Iterable[uint8]` or `Iterable[int32]` (range-checked)
- **Working**: Subscript (`b[i]` -> `uint8`), `len()`, `in` operator
- **Working**: Concatenation (`+`), repetition (`*`), equality and ordering (`==`, `!=`, `<`, `<=`, `>`, `>=`; `bytes` is `Comparable`, so `list[bytes].sort()` works), a bytes needle in a tuple literal (`name in (b"A", b"B")`, an `==` OR-chain over the static literal spans) and in a `list` / `set` / `dict` of bytes
- **Working**: `decode()` -> `str`, `hex()` -> `str`
- **Working**: Search methods: `find`, `rfind`, `count`, `startswith`, `endswith`
- **Working**: Transform methods: `replace`, `split`, `join`, and `strip`/`lstrip`/`rstrip` (on immutable `bytes`, return a zero-copy `BytesView` of the receiver like `str.strip -> StrView`; annotate the result owned `bytes` to copy. On mutable `bytearray` they return an owned `bytearray` -- a view would alias the mutable buffer, diverging from CPython's independent copy)
- **Working**: `bytearray` mutation: `append`, `extend`, `pop`, `clear`, `insert`, `remove`, `__setitem__`
- **Working**: `hash(b)` for `bytes` and `BytesView` -- enables use as dict keys and set elements
- **Working**: Iteration over bytes (`for b in data`)
- **Working**: View deduction: bytes literals and `list[bytes]` subscripts infer `BytesView` when safe, fall back to owned `bytes` when mutated. A `bytes | None` parameter lowers to the borrow form `std::optional<std::span<const uint8_t>>`, matching `str | None` (`std::optional<std::string_view>`) -- both are members of one view-type family, so a real `bytes` value can be passed without a copy. When such a borrow flows into an owned sink (return, field/container store, `dict[k] = v` value), an explicit `::tpy::Bytes(span)` is emitted (unlike `string_view -> string`, `span -> vector` is not an implicit conversion). In an `async def` / generator, a `bytes | None` / `str | None` param is captured OWNED in the resumable frame (the borrow copied into `std::optional<owned>` at frame construction) so it survives suspension, parallel to bare `str`/`bytes` -- and that owned capture is a filed defect rather than the intended design for either shape (`BUGS.md#generator-frame-copies-str-param`)

#### Bytes Type Semantics (Working)

`bytes` maps to `::tpy::Bytes` and `bytearray` to `::tpy::ByteArray` -- two distinct C++ types over the same `std::vector<uint8_t>` base, neither convertible to the other (a conversion between them is a TPy coercion the front end decides, never something C++ does on its own), and both distinct from `list[uint8]`, which keeps the bare base. The difference is at the type-system level: `bytes` is immutable (no mutation methods), `bytearray` is mutable. `bytearray` is a **reference type** (like `list`/`dict`/`set`): a local binding or field/return read aliases the buffer rather than deep-copying it, so mutation through the alias is visible, matching CPython; storing one into owned storage (field, container element) copies and warns like any reference type. `bytes` stays value-like -- it is immutable, so the copy is unobservable. Being a reference type, `bytearray` takes the reference-shaped slots the other containers do: an `Own[bytearray]` parameter reads as a plain owned local, and a coroutine may return `Own[bytearray]`.

A `bytearray` receiver is the same method-call family a `list`/`dict`/`set` receiver is, so every receiver shape they admit it admits: a bare name, a field, a container-valued **property** (the getter call is the receiver lvalue), an inner-call result (`ba.strip().upper()`), and an inherited stub method on a `class MyBA(bytearray)` subclass that declares its own `__init__`. A `bytearray | None` **field** stores `std::optional<std::vector<uint8_t>>` and takes the same bare-or-moved name write an `Optional[list]` field takes.

Five shapes still reject for `bytearray` where `list` routes, all measured rather than inferred: binding a local from a container **element** (`b = bas[0]`) and calling through one (`len(bas[0])`), appending a freshly built `bytearray` to a container (`bas.append(bytearray(b"ab"))`), iterating `d.values()` over a `dict[str, bytearray]`, and a comprehension **producing** `bytearray` elements. Four more reject for `bytearray` and `list` alike -- a container LITERAL returned at an `Own[T] | None` slot (an owning CALL source routes there), an `Own[T | Record]` return, that union's constructor-argument twin, and a walrus binding a fresh container (`use(x := [1, 2])`).

Only two facts about `bytearray` are genuinely its own, and both live on the type registry rather than in the lowering: its parameter slot is `const ::tpy::ByteArray&` by default with a separate mutable spelling (the pair that also lets an rvalue bind such a slot), and its storage owns a buffer. Everything else it gets by being a reference type -- the field write, the `Own` parameter and return, the coroutine and generator slots, the container element store, the ternary and select arms all admit it through the same reference-axis test a `list` or a record passes, with no `bytearray` leg. The places the compiler still spells the bytes family are OPERATIONS, not admissions: the concat / repeat / slice / `in` renders, `bytes_getitem` and `bytearray_setitem`, and the print-formatter ladder each name a runtime symbol per family, and they close together when the resolved dunder is read off the node (the stage-5 entry in `TODO.md`). The one admission that still names it is the method RECEIVER. What made `set[bytes]` unsafe there was `discard`'s lookup key arriving as a view at an `erase(const std::vector<uint8_t>&)`; the runtime now takes the view for both the parameter and the literal spelling, so what remains is re-running the fold's own gates for the other families the pure axis would admit.

Binding a `bytes` value to a `bytearray` slot (`ba: bytearray = p`) is a
located ERROR, and it is a rule rather than a detected hazard: CPython does
not enforce the annotation, so `ba` stays the same immutable object and only
a later mutation raises -- a program that never mutates runs fine there.
Copying silently would make the TPy program mutate a private buffer where
CPython raises, so the copy has to be written
(`tests/cases/bytes/error_bytes_at_bytearray_slot`).

The reverse direction, a `bytearray` at an OWNING `bytes` sink, is the SAME
rule, in the same shape: a located ERROR asking for the explicit `bytes(ba)` --
`Type mismatch in variable 'b': expected bytes, got bytearray -- write
'bytes(...)' around it` (`tests/cases/bytes/error_bytearray_at_bytes_sink`). Every owning sink is
covered -- an annotated local (`b: bytes = ba`), a `-> bytes` return, a `bytes`
field store, an `Own[bytes]` slot including the `list[bytes]` element
`seen.append(ba)` reaches, a container LITERAL element (`[ba]` at a
`list[bytes]`, a dict key or value, a set element) and a COMPREHENSION element,
key or value. Neither buffer type
converts to the other, so TPy would have to copy; CPython does not enforce the
annotation, so it binds the bytearray OBJECT and every later read sees mutations
of the source. Only the explicit `bytes(ba)` copies there too, which is why the
spelled form is parity-clean at every sink
(`tests/cases/bytes/bytearray_copy_into_bytes_sink` writes it at the element
slot, the local, the return, the field, a list literal and a comprehension,
mutates the source and observes that each copy stood still, in TPy and in
CPython alike; `tests/cases/bytes/error_bytearray_in_bytes_literal` pins the
literal face of the rejection).

A sink OWNS what it is handed when it is a field, a container element, an
`Own[...]` slot, an initializer or a return -- everything except a plain
argument, which borrows. That last clause is a statement about the PARAMETER,
not about what the callee then does with it: a callee that returns its `bytes`
parameter, or hands it to a sink that owns, materializes an owned copy the
caller never asked for, and nothing at the call site says so
(`BUGS.md#bytearray-at-bytes-param-owned-silently`). The `bytearray` -> `bytes` conversion DECLARES that
it is admitted only at a borrowing sink, so at an owning one it does not
apply at all and the program sees the ordinary type mismatch; no part of the
compiler names the two buffer types to reach that. The verdict is read where
the binding is already stated rather than re-derived, and the question is
asked again at every level a wrapper peels: `bytes | None`, a union member and a container
element each reach it as their own binding, carrying the same verdict as the
bare slot, so `ob: bytes | None = ba` is refused exactly as `b: bytes = ba` is.

A TUPLE element is the one wrapper that does not inherit the outer verdict: a
tuple holds its elements by value, so each element owns even where the tuple
itself is borrowed, and `f((ba, 1))` at a `tuple[bytes, int]` PARAMETER is
refused like every other owning sink. A view element is the opposite and keeps
the borrow: `Span[bytes]` is a window onto someone else's buffer, so its
element owns nothing.

So neither buffer type converts implicitly to the other at an owning sink, in
either direction -- one rule over both.

The `-- write 'bytes(...)' around it` half is not a fact about buffers either.
A mismatch ends that way exactly when a conversion for the pair EXISTS but is
admitted only where the destination borrows: the value can be handed over, so
the one thing missing here is the copy, and spelling the destination type
around it is the copy. The name inside the quotes is the destination type's
own. Every other mismatch is left alone -- a `list` at a `bytes` slot, or a
`str` at an `int32` slot, needs a conversion or a parse, not a copy, and gets
the bare mismatch.

The `bytes` -> `bytearray` direction is one of those: it has no conversion at
any position, so it is a plain located mismatch with no remediation, while its
reverse -- which DOES bind at a borrowing `bytes` parameter -- is told to copy.

A BORROWING `bytes` slot is not affected either way: a `bytes` parameter is
`::tpy::BytesView`, a free view over whatever buffer it is handed, so a
`bytearray` argument passes bare with no copy at all and both languages see the
caller's object (`tests/cases/generics/generic_slot_distinct_buffer_types`) --
for as long as the view stays a view. A callee that OWNS the value (returns it,
or stores it) builds a fresh `bytes` from the view, and the caller's bytearray
identity is lost silently
(`BUGS.md#bytearray-at-bytes-param-owned-silently`).

`BytesView` (`::tpy::BytesView`, over `std::span<const uint8_t>`) is a non-owning view, analogous to `StrView` for strings. Bytes literals use C++ string literal static storage (via `bytes_literal()`), so `BytesView` references to literals never dangle. Local variables inferred from bytes literals or `list[bytes]` subscripts use `BytesView` when safe, and fall back to owned `bytes` when mutated:

```python
b = b"hello"           # BytesView (static storage, zero allocation)
b += b"!"              # mutation -> promotes to owned bytes

items: list[bytes] = [b"alice", b"bob"]
x = items[0]           # BytesView (no mutation follows)
items.append(b"carol")  # source mutated -> x becomes owned bytes
```

As with strings, reassigning an inferred bytes local to an incompatible type
is a sema error (`b = b"hi"; b = 5`), not a deferred C++ build failure.

Bytes literals passed as function arguments also use static storage, avoiding heap allocation:

```python
def process(data: bytes) -> None: ...
process(b"hello")      # zero-alloc: static span passed directly
```

`bytes` and `BytesView` are hashable and can be used with `hash()`. `bytes` can be used as dict keys and set elements. `bytearray` is not hashable (mutable type).

### Containers
- **Working**: `list[T]` - dynamic list → `std::vector<T>` (with context-dependent inference; type parameter is invariant: `list[Child]` is not compatible with `list[Base]`)
- **Working**: `list[T] + list[T]` concatenation → new list, `list[T] += list[T]` extend in-place, `del lst[i]` element removal
- **Working**: `lst[x:y]` basic slicing -> `Span[T]` (zero-copy view, clamped, no-panic). `lst[x:y:z]` stepped slicing -> `list[T]` (owned copy). `lst[x:y] = rhs` basic slice assignment -> replaces, resizes, deletes, or inserts (Python semantics). `lst[x:y:z] = rhs` stepped slice assignment -> in-place replacement (RHS length must match selected elements; step=1 allows resize like basic slice). RHS accepts any `Iterable[T]` (lists, generators, ranges). Both dispatch through `__setitem__(basic_slice/slice)` stubs.
- **Working**: Array literals `[1, 2, 3]` → `std::array<T, N>` or `std::vector<T>` (context-dependent)
- **Working**: An element read whose own receiver is an element (`cube[i][j]`, `g[k][0]`, `m[0][k]`, `self.cells[i][j]`) is an ordinary hop: the inner subscript lends the root's storage, so the outer read borrows into it rather than copying. It is admitted at every VALUE position -- `len()`, a comparison, a call argument, a method receiver, a local binding, a further subscript read or write, and a borrow `return` -- in a free function, a method, a `with` body, `try`/`finally`, a `match` arm, a generator and an `async def` alike, with a `readonly` root reading const through both hops. The for-each ITERABLE is the exception, and not for a lifetime reason: the iteration's borrow has no place to be filed under, so it keeps a located reject (`BUGS.md#iter-borrow-place-needs-hops`); binding the inner container to a local first iterates. At MODULE level only the write face works: binding a module-level name to any global's element is a located reject at any depth (`BUGS.md#global-alias-of-global-element-rejected`). The general rule every chain reader applies -- "the hop must name storage the ROOT outlives", which is what rejects a call, a slice or a by-value accessor as a hop -- is stated once in **Const reference optimization** under [Parameter Passing Convention](#parameter-passing-convention).
- **Working**: `Array[T, N]` - fixed-size array with explicit type annotation. Like `list`/`dict`/`set`/`bytearray` it is a **reference type** (a local binding, field read or borrow return aliases the array rather than copying it), so it takes the same reference-shaped slots: an `Own[Array[T, N]]` return, an `Array[T, N] | None` local and return slot, an array field as an `==` operand or a `for` iterable, and an array name at a protocol-typed constructor slot. The `Own[Array[T, N]]` return takes a bare owned array NAME as well as a literal. The two shapes still rejected are both on the `Own[...] | None` spelling, which is value-repr (`std::optional<std::array<...>>`) rather than a pointer: a container LITERAL at such a return slot (the slot has no typed-brace render -- an owning CALL source routes), and binding an `Own[Array[T, N]] | None` parameter to a local.
- **Working**: `Span[T]` - non-owning mutable view into contiguous memory → `std::span<T>`
- **Working**: `Span[readonly[T]]` - non-owning read-only view into contiguous memory → `std::span<const T>`
- **Working**: `SpanIter[T]` - lightweight iterator over a contiguous span → `tpy::SpanIter<T>`. Constructed from `Span[T]` or `Span[readonly[T]]`. Implements `NativeIterable[T]`, `Iterable[T]`, `Iterator[T]`. Used as the return type of `__iter__()` on span-backed user types (e.g. `ArrayList`).
- **Working**: `tuple[T1, T2, ...]` - fixed-length typed tuple -> `std::tuple<T1, T2, ...>`
  - Element lowering follows the same per-type rules as scalar values: a non-value `T` element becomes `T*` in params/returns/locals (so `tuple[T, U]` returns `std::tuple<T*, U*>`) and `T` in fields/containers. A `T | None` element of a non-value `T` becomes `T*` too (nullable; `tuple[T | None, T | None]` returns `std::tuple<T*, T*>`) and `std::optional<T>` in fields/containers.
  - Mutation-based const inference applies to tuple borrow params: when the body provably doesn't mutate through any slot, the slots become const (`tuple[T, T]` → `const std::tuple<const T*, const T*>&`; `tuple[T | None, T | None]` → `const std::tuple<const T*, const T*>&`). Bodies that mutate keep mutable slots. This mirrors the existing `T → const T&` inference for plain record params and applies all-or-nothing per param (per-slot precision is a future refinement). Generic-slot tuples (`tuple[T, ...]` with type-param `T`) skip inference -- the C++ ABI is decided at instantiation.
  - Boundaries between borrow form and storage form (field-init, field-write, global init, container-element init, destructuring of a storage-form source) lower to element-wise conversions via `tpy::tuple_to_storage<...>` / `tpy::tuple_to_pointer<...>`. The pass-through case (return value of one function flowing into the param of another, both in pointer form) needs no conversion.
  - Rvalue tuple elements of reference type (e.g. `f((Point(1,2), 42))` where the slot expects a borrow) trigger the same C++ "address of rvalue" limitation as the analogous non-Optional case; bind to a local first. Tracked in BUGS.md.
  - **Owned-element unpack (move-out):** unpacking a fresh `tuple[Own[A], Own[B], ...]` rvalue (`a, b = make_pair()`) binds each target as an *owned, movable* local -- each `Own[T]` element is moved out of the consumed temporary, so the targets can be moved onward (into `Own[]` params, etc.) just like a single-assign owned rvalue. Non-`Own` elements keep their usual value/borrow semantics. The named-source form (`t = make_pair(); a, b = t`) works too: when the unpack is the last use of the tuple local, the source is moved into the temp and each element moved out. One carve-out on that form: in a generator or `async def` body where the tuple local lives across a suspension (so the frame owns it), the unpack binds the frame slot rather than the tuple and fails the C++ build (`BUGS.md#frame-tuple-unpack-slot`). An owned-element tuple **parameter** (`def f(p: tuple[Own[A], Own[B]])`) takes ownership -- it is rendered `std::tuple<...>&&` (the tuple analog of the scalar `Own[T] -> T&&` ABI), so the callee can unpack/move its elements out, forward it onward, or return it; the caller moves a last-use owned source in. A source NOT at its last use can't move into the `&&` param: a `@nocopy` tuple is a use-after-move error, and a copyable one is warned and auto-copied (mirroring the scalar `Own[T]` arg -- TPy copies where CPython would alias, the same acknowledged, warned `Own`-copy divergence; silence it with an explicit `copy()` or by passing at the last use). Like a scalar `Own[T]` param it also warns when never consumed (suppressed for `@nocopy`, whose drop is a legitimate consume), and using it after a consuming unpack is a use-after-move error; the read-only borrow alternative is the `Own`-less `tuple[A, B]`. Move-out of an aggregate *member* in any other shape -- partial element move-out (`take(pair[0])`), record-field move-out -- is not yet supported (needs per-place partial-move tracking); use whole-tuple unpack or `Rc.clone()`. Tracked in BUGS.md.
  - Protocol conformance: `tuple[T1, T2, ...]` conforms to `Hashable`, `Comparable`, and `Equatable` when every `Ti` conforms to the same protocol. Lexicographic `<` and element-wise `==` lower to `std::tuple`'s built-in operators; this also unblocks the canonical `list[tuple[priority, payload]]` priority-queue pattern via `heapq`.
- **Working**: `dict[K, V]` - ordered hash map → `tpy::ordered_map<K, V>` (insertion-order preserving)
  - Literals `{k: v, ...}`, subscript `d[k]`/`d[k] = v`, `del d[k]`, `len(d)`, `k in d`, `for k in d`
  - Constructor: `dict(iterable)` from any iterable of `tuple[K, V]` (list of tuples, `.items()` view, etc.)
  - Usage-based inference: `d = {}; d[k] = v` and `d = dict(); d[k] = v` infer key/value types from subsequent subscript assignment (with numeric widening). Empty `{}` also takes its `K`, `V` from a `dict[K, V]` annotation on the LHS (local annotation, class field, function param, return type, nested dict slot).
  - **Annotated nested values**: a value written as a container LITERAL takes the annotated value type, one level at a time -- `d: dict[str, list[int32]] = {"a": [1, 2]}` stores a real `list[int32]`, and so do the `dict`, `set`, `Array[T, N]` and tuple-of-container value slots, an `Optional[dict[K, V]]` slot, and arbitrary nesting (`dict[str, list[list[int32]]]`). A value literal of a different container kind is still a type mismatch.
  - Methods: `get(k)`, `get(k, default)`, `pop(k)`, `pop(k, default)`, `clear()`, `update(other)`, `setdefault(k, default)`, `keys()`, `values()`, `items()`; augmented `|=` (merge in-place)
  - Views: `d.keys()`, `d.values()`, `d.items()` return zero-allocation views with `for`-loop, `len()`, `in`
  - **Aliasing (CPython semantics)**: `for k, v in d.items()` and `for v in d.values()` bind `v` as an alias of the stored value -- mutations reach the dict (also inside generators/async, and through the leaked loop var after the loop -- readable there only when the loop provably runs, see "Loop clause locals, the zero-trip loop and `break`"). `setdefault(k, default)` returns a borrow of the stored value, so `d.setdefault(k, []).append(x)` mutates the dict. On a `readonly[dict]` receiver the views yield readonly elements and mutation is a sema error. Keys bind as const borrows (mutating a key object through iteration is a compile error -- CPython permits it but it corrupts the hash table).
  - **Readonly reads**: the read surface works on a `readonly[dict]` (including a dict field read inside a `@readonly` / auto-inferred-readonly method, where `self._data` is `readonly[dict[K,V]]`): `d[k]`, `d.get(k)` / `get(k, default)`, `k in d`, and `for k in d`. A read of a reference-type value yields `readonly[V]`. (Known gap: `sorted(d.items(), key=...)` directly on a readonly dict fails because `items()` projects `readonly` onto a value-typed element -- build the pairs list first; see BUGS.md.)
  - **Readonly keys**: the pure-read key methods (`__getitem__`, `get`, `__contains__`, `__delitem__`, `pop`) accept a `readonly[K]` key -- the key is only hashed/compared. (`__delitem__`/`pop` mutate, so they still require a mutable dict; only their *key argument* may be readonly. `__setitem__`/`setdefault` store the key and keep a mutable `K`.)
  - **Acknowledged divergence**: two-arg `get(k, default)` on reference-type values returns a *copy* of the stored value (CPython returns the stored object); a warning fires at the call site. Alias via `d[k]` / one-arg `get(k)`, or wrap in `copy()` to acknowledge the copy. Value-type results are parity-clean (no warning). The borrow-returning form is tracked in BUGS.md.
  - `Iterable[T]` conformance: `dict[K,V]` and views conform to `Iterable` (`d` is `Iterable[K]`, `d.keys()` is `Iterable[K]`, `d.values()` is `Iterable[V]`, `d.items()` is `Iterable[tuple[K, V]]`) and can be passed to generic functions accepting `Iterable[T]`
  - Keys: `str`, `int`, fixed-width ints, `float`, `bool`, `char`
  - Keys must be hashable AND copy-constructible. Sema enforces both at annotation resolution -- annotating `d: dict[K, V]` is enough; you don't need a literal or `__setitem__` to trigger the check. Non-hashable user records (no `__hash__` / `__eq__`, including inherited) get a "missing `__hash__`; use @dataclass(frozen=True) or define it explicitly" message. `@nocopy` keys (`Rc[T]`, `Box[T]`, user `@nocopy` classes, tuples wrapping them) get a precise "non-copyable" message -- the copy-constructibility requirement is a runtime limitation (`tpy::ordered_map`'s `std::pair<const K, ...>` entries force copy-construction), not a language design choice. (Move-only **values** in `dict[K, Rc[T]]` work; only the key slot is gated.)
  - **Type parameters are invariant**: `dict[K, Child]` is not compatible with `dict[K, Base]` even when `Child` inherits from `Base`. C++ `ordered_map<V>` is a non-converting template — passing `Child` where `Base` is expected would fail at C++ build time or silently slice objects.
  - Return by value requires `Own[dict[K, V]]`
- **Working**: `set[T]` - ordered hash set -> `tpy::ordered_set<T>` (insertion-order preserving)
  - Literals `{a, b, ...}`, `len(s)`, `x in s`, `for x in s`
  - Constructor: `set(iterable)` from any iterable
  - Usage-based inference: `s = set(); s.add(v)` infers element type from subsequent `.add()` calls (with numeric widening)
  - Methods: `add(v)`, `discard(v)`, `remove(v)`, `pop()`, `clear()`, `copy()`
  - Algebra: `union(other)`, `intersection(other)`, `difference(other)`, `symmetric_difference(other)`
  - Predicates: `issubset(other)`, `issuperset(other)`, `isdisjoint(other)`
  - In-place: `update(other)`, `intersection_update(other)`, `difference_update(other)`, `symmetric_difference_update(other)`
  - Operators: `|` (union), `&` (intersection), `-` (difference), `^` (symmetric difference)
  - Comparison: `<=` (subset), `<` (strict subset), `>=` (superset), `>` (strict superset)
  - Augmented: `|=`, `&=`, `-=`, `^=`
  - Elements must be hashable AND copy-constructible (same constraint as dict keys -- enforced at annotation resolution, not just at literals/comprehensions; copy-constructibility is a runtime limitation, not a language design choice)
  - **Type parameter is invariant**: `set[Child]` is not compatible with `set[Base]` even when `Child` inherits from `Base`. C++ `ordered_set<T>` is a non-converting template.
  - Return by value requires `Own[set[T]]`
- **Open**: Bounded variants: `BoundedList[T, N]`, `BoundedDict[K, V, N]`

#### Array Literals and Span (Working)

Array literals compile to fixed-size stack arrays with inferred size:
```python
nums = [1, 2, 3]                    # type inferred as Array[int32, 3]
arr: Array[int32, 3] = [10, 20, 30] # explicit type annotation
```

#### List Literal Inference (Working)

Context-dependent inference for Python-first semantics:

| Context | Inferred Type | C++ Type | Rationale |
|---------|---------------|----------|-----------|
| Module-level (global) | `list` | `std::vector` | Cross-module safety - other modules might import and mutate |
| Function local, no mutation | `Array` | `std::array` | Stack performance, no heap allocation |
| Function local + `.append()`/`.pop()`/etc | `list` | `std::vector` | Explicit mutation requires growable container |
| Function local, passed to `list[T]` param | `list` | `std::vector` | Callee expects mutable list |
| Function local, passed to `Span[T]` param | `Array` | `std::array` | Span is a view, array stays on stack |
| Function local, different-size reassignment | `list` | `std::vector` | `x = [1,2,3]; x = [4,5]` -- sizes differ, must be dynamic |
| Function local, jagged nested literal | inner `list` | `std::vector` | `[[1,2],[3,4,5]]` -- the variable-length level demotes to `list`, uniform levels stay `Array` |
| Function local, returned as `list[T]` | `list` | `std::vector` | Return type context propagates to local variable |
| Function local, alias mutated | `list` | `std::vector` | `b = a; b.append(4)` -- both `a` and `b` become list |
| Explicit annotation `x: Array[T, N]` | `Array` | `std::array` | User opted into fixed size |
| Explicit annotation `x: list[T]` | `list` | `std::vector` | User opted into dynamic list |
| Return type `-> Own[list[T]]` | `list` | `std::vector` | Return type context propagates to literal |

List reassignment is element-type-checked (independent of the size-widening
above): `x = [1, 2]; x = ["a"]` is a sema error (`expected list[int32], got
list[str]`), like the equivalent scalar/str rebind. Numeric element widening
is still accepted.

Nested list literals apply the rule per level. Sublists of differing length
(a jagged literal, `[[1, 2], [3, 4, 5]]`) can't share a fixed `Array`, so that
level resolves to `list[list[T]]`; levels with uniform element counts stay
`Array`. The demotion propagates across same-size sibling literals so the whole
container keeps one C++ element type -- e.g. in
`[[[1, 2], [3, 4]], [[5, 6], [7, 8, 9]]]` the jagged innermost level becomes
`list[int]` for every sublist, while the uniform outer/middle levels stay
`Array`.

The demotion reaches a jagged list wherever it sits in a sibling element, not
just bare: nested in a tuple (`[(1, [2, 3]), (4, [5, 6, 7])]`), in a dict value
(`{1: [[1, 2]], 2: [[3, 4, 5]]}`), or under a concrete dict
(`{1: {10: [1, 2]}, 2: {20: [3, 4, 5]}}`). Convergence rides one traversal
shared with the compatibility check, so the side that decides peers are
compatible and the side that resolves them can't disagree. (Differing-length
sublists across the branches of a ternary / `and` / `or` are a separate,
still-open case -- see BUGS.md.)

This gives the best of both worlds:
- **Python semantics by default**: Globals behave like Python module variables (mutable, shareable)
- **Performance for locals**: Function-local arrays stay on the stack when safe
- **Escape analysis**: Compiler detects when mutation or list-typed parameters force dynamic allocation

Example:
```python
# Global - always vector (other modules might mutate)
config_values = [1, 2, 3]  # → std::vector<BigInt>

def process():
    # Local, no mutation - array (stack allocated)
    lookup = [10, 20, 30]  # → std::array<BigInt, 3>
    return lookup[1]

def build_list():
    # Local with mutation - vector
    result = [1, 2]        # → std::vector<BigInt>
    result.append(3)       # mutation detected
    return result

def use_list(items: list[int]):
    items.append(42)

def caller():
    data = [1, 2, 3]       # → std::vector<BigInt> (passed to list param)
    use_list(data)

def caller_direct():
    use_list([1, 2, 3])    # → temp std::vector passed to function
    use_list(list())       # → temp empty std::vector
    use_list([])           # → temp empty std::vector

def make_items() -> Own[list[int]]:
    return [1, 2, 3]       # → std::vector (return type context)

def make_empty[T]() -> Own[list[T]]:
    return []              # → std::vector<T> (generic return type context)

def build_from_usage() -> None:
    xs = []                # element type unknown at declaration
    xs.append(42)          # → list[int32], inferred from append arg
    xs.append(100)         # widened if needed (e.g., int32 + int64 → int64)
    print(xs)

def reader(items: Span[int]) -> int:
    return items[0]

def caller2():
    data = [1, 2, 3]       # → std::array<BigInt, 3> (Span is a view)
    return reader(data)
```

**Contextual element-type widening**: When the annotation's element type is wider than the literal's inferred element type, the literal adopts the annotation's type. This enables mixed-type literals:
```python
# Union element types: literal elements are checked against the annotation
items: list[int32 | None] = [int32(1), None, int32(3)]
empty: list[int32 | None] = []
```

Note: Passing literals (`[]`, `[1,2,3]`) or constructors (`list()`) directly to functions expecting mutable reference parameters works - the compiler generates temporary variables automatically.

`Span[T]` is a non-owning mutable view into contiguous memory. `Span[readonly[T]]` is the read-only variant. This follows the same pattern as `Ptr[T]`/`Ptr[readonly[T]]`:

```python
from tpy import int32, Span, Array

# Mutable span -- can read and write elements
def zero_first(values: Span[int32]) -> None:
    values[0] = 0

# Read-only span -- can only read elements
def sum_values(values: Span[readonly[int32]]) -> int32:
    total: int32 = 0
    for v in values:
        total += v
    return total

# Any contiguous container coerces to Span or Span[readonly[T]]:
arr: Array[int32, 3] = [10, 20, 30]
zero_first(arr)                     # Array -> Span[int32]
print(sum_values(arr))              # Array -> Span[readonly[int32]]
print(sum_values([1, 2, 3, 4, 5])) # array literal -> Span[readonly[int32]]

# Span[T] auto-coerces to Span[readonly[T]] (like Ptr -> Ptr[readonly[T]]):
s: Span[int32] = arr
print(sum_values(s))                # Span -> Span[readonly[T]]

# Through @readonly refs, Span[T] becomes Span[readonly[T]] automatically
```

Key features:
- `Span[T]` maps to `std::span<T>` (mutable), `Span[readonly[T]]` maps to `std::span<const T>`
- Requires C++23 (`-std=c++23`)
- Standard Python `len()` and `[]` indexing work for both (with bounds checking and negative index support)
- `unchecked_get(index)` for raw unchecked access (no bounds check, no negative index normalization)
- `sort()` for in-place stable sort via `std::stable_sort` (matches Python's stable sort guarantee)
- Zero-allocation passing of fixed-size arrays to functions that work with any size
- Constructors: `Span(Ptr[T], int32)` and `Span(Ptr[readonly[T]], int32)` for low-level span creation
- Explicit construction from containers: `Span[T](arr)`, `Span[readonly[T]](lst)` for any `Spannable[T]` source (list, Array)
- Containers coerce to `Optional[Span[T]]` / `Optional[Span[readonly[T]]]` at call sites
- `Ptr[T].span(length)` returns `Span[T]`, `Ptr[readonly[T]].span(length)` returns `Span[readonly[T]]`

#### Tuples (Working)

Fixed-length typed tuples with compile-time element access:

```python
from tpy import int32

# Type is inferred -- no annotation needed
t = (int32(1), "hello")
print(t)           # (1, 'hello')

# Explicit annotation also works
t2: tuple[int32, bool, str] = (int32(42), True, "world")

# Element access with compile-time integer index
x = t[0]           # -> std::get<0>(t)
s = t[1]           # -> std::get<1>(t)
last = t[-1]       # negative indexing supported

# Single-element tuple (trailing comma required, like Python)
single = (int32(42),)
print(single)       # (42,)

# Nested tuples
nested = (int32(10), ("inner", False))

# Tuple as function parameter and return type
def swap[A, B](p: tuple[A, B]) -> tuple[B, A]:
    return (p[1], p[0])

# Tuple unpacking (destructuring assignment)
a, b = (int32(1), "hello")  # fresh variables
_, second = swap(t)          # _ discards a value

# For-loop unpacking
items: list[tuple[int32, str]] = [(int32(1), "one"), (int32(2), "two")]
for n, s in items:
    print(n, s)

# Comparison (==, !=, <, <=, >, >=)
a2 = (int32(1), "hello")
b2 = (int32(1), "hello")
print(a2 == b2)       # True
print((1, 2) < (1, 3))  # True (lexicographic)

# Hashing -- tuples can be used as dict keys
d: dict[tuple[int32, int32], str] = {(1, 2): "one-two", (3, 4): "three-four"}
key: tuple[int32, int32] = (1, 2)
print(key in d)            # True -- membership test with tuple LHS
print((3, 4) in d)         # True -- tuple-literal LHS coerces to dict's key type
print(hash((1, 2, 3)))     # hash of a tuple
```

Reference types in tuples follow context-dependent semantics (same rules as standalone `T`):

```python
from tpy import int32, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

# Return context: Point is returned by reference (like -> Point)
def find(p: Point) -> tuple[Point, bool]:
    return (p, True)  # -> std::tuple<Point*, bool>

# Own[T] forces value/copy semantics in return context
def make(x: int32) -> tuple[Own[Point], bool]:
    return (Point(x, x), True)  # -> std::tuple<Point, bool>

# Local tuple: lvalue elements captured by reference
p = Point(int32(1), int32(2))
t = (p, True)     # std::tuple<Point*, bool>, p.x mutation visible through t

# Tuple unpacking: reference elements bind as aliases
pt, found = find(p)  # pt aliases p, found is bool

# A tuple-LITERAL source aliases reference lvalue elements too (CPython
# parity): `a, b = (items[0], items[1])` lowers to per-element single-assigns
# -- at both function-body and module/REPL scope -- so `a` aliases `items[0]`
# (a mutation through `a` is visible on the source) rather than copying it
# into a value tuple.
# Evaluation order is preserved (the whole RHS is evaluated before any target
# binds), so swaps (`a, b = (b, a)`) and self-referential RHS stay correct.
a, b = (items[0], items[1])  # a aliases items[0], b aliases items[1]
```

| Context | `tuple[int32, Point]` C++ | Rationale |
|---------|--------------------------|-----------|
| Return | `std::tuple<int32_t, Point*>` | Borrow form (pointer element; a reference can't be a tuple member) |
| Return (`@readonly`) | `std::tuple<int32_t, const Point*>` | Const borrow form |
| Field | `std::tuple<int32_t, Point>` | Same as `field: Point` = owned |
| Container (`list[...]`) | `std::tuple<int32_t, Point>` | Same as `list[Point]` = owned |
| Local variable | `auto` (deduced from RHS) | Same as `p2 = p` = reference |

Ownership transfer (all three forms produce identical codegen):

- `tuple[Own[T], Own[T], ...]` -- per-element Own. Each non-value element is moved into the value tuple; `@nocopy` elements at last-use are auto-moved. An *owned* source element (an `Own[T]` param/return, or an owned local) bound into a tuple by NAME at its **last use** MOVES into the tuple, which then owns the element (storage form) -- the local analog of the scalar field/return auto-move; the moved tuple local then returns / passes by name into an `Own[T]` slot with no copy (`pair = (ob, 0); return pair` / `sink(pair)`). An owned source that is **not** at last use COPIES into the `Own[T]` slot with the same `copies ... into owned storage; use copy()` warning the scalar `T -> Own[T]` coercion gives (it stays usable after). A *borrowed* (param / attribute / non-last-use) reference element placed into an `Own[T]` return / call-arg slot requires explicit `copy()` -- rejected for both a tuple literal and a NAME-bound source (the latter via a construction-time hazard that propagates through alias / ternary / loop paths), mirroring the scalar `Own[T]` return rule. (Fields are storage form regardless of an `Own` annotation, so a reference element copied into a tuple *field* is the ordinary field-copy warning, not an `Own`-slot rule.)
- `Own[tuple[T, T, ...]]` -- outer Own. Equivalent to per-element Own for non-value elements; the value tuple owns its contents.
- `tuple[T, T, ...]` in field/owning-destination context -- last-use owned locals auto-move into the slot, matching scalar `field: T` semantics ("a tuple field of N objects behaves like N scalar fields"). An `Own[T]` annotation on field elements is rejected as redundant (the field owns its value regardless).
- `copy(t)` over a whole tuple with reference elements (a literal `copy((x, ref))` or a tuple local/field) produces an owned storage-form tuple, copying each element by value -- equivalent to copying per element (`(x, copy(ref))`). The element is stored by value, so the copy is independent of the source.

```python
def make_pair() -> tuple[Own[Handle], Own[Handle]]:
    a = Handle(int32(1))
    b = Handle(int32(2))
    return (a, b)              # std::tuple<Handle, Handle>{std::move(a), std::move(b)}

def make_pair_alt() -> Own[tuple[Handle, Handle]]:
    return (Handle(int32(1)), Handle(int32(2)))   # same codegen

class Container:
    pair: tuple[Handle, Handle]              # no Own[T] needed
    def __init__(self) -> None:
        a = Handle(int32(1))
        b = Handle(int32(2))
        self.pair = (a, b)                   # auto-moves at last-use
```

Restrictions:
- Index must be a compile-time integer literal (variable indexing is rejected)
- Bare `tuple` without type arguments is rejected (must use `tuple[T1, T2, ...]`)
- Returning a reference to a local or temporary as a tuple element is rejected (dangling reference check). Use `Own[T]` to return by value. This includes a fresh value coerced into a recursive-union-wrapper element (the element is borrow form `X&`), which needs `Own[X]` just like a fresh single-value return. The same rejection covers the *bound* form -- binding such a tuple to a local then returning/yielding it by name (`pair = (leaf, 0); return pair`, `leaf: Tree[int32]`) -- via a per-local owned-fresh-member fact set at the tuple-literal assignment. The fact is derived from the binding's provenance, so it also propagates through a tuple-local alias (`alias = pair; return alias`), an alias chain, a ternary of such locals, a one-branch alias (UNION merge), and a self-assignment. A *durable* (param / field / call-rooted) reference member is NOT rejected when the destination is a borrow-form tuple: the bound local is pointer borrow form (`std::tuple<..., T*>`), so returning/yielding it shares the member like CPython. (Returning / passing it into an `Own[T]` return / call-arg *slot* is a different boundary -- there the durable borrow would be copied into owned storage, so it requires explicit `copy()`; see the per-element-Own rules above.)
- Tuple element assignment (`t[0] = x`) is rejected (tuples are immutable)
- Nested unpacking (`a, (b, c) = ...`) is not yet supported. The reference-element aliasing of a tuple-literal unpack applies to **flat** unpacks at both function-body and module/REPL scope (a class-body tuple target is a field-declaration parse error, never an unpack). All element kinds alias correctly, including a ternary of reference lvalues and a reference-returning method call (both are borrow-aliases, not owned values).
- Non-value `Union` elements (`tuple[A | B, ...]`) borrow as a const pointer variant `::tpy::Union<const A*, const B*>` at the param/return/passthrough boundary (passing such a tuple and reading its non-union elements works); reading/narrowing the union element itself, and the storage/local/field directions, are not yet wired (see BUGS.md). `T | None` elements are fully supported (pointer-repr slots, see the Optional-element conversions above)
- A tuple bound to a local of a generator / async frame owns its elements PER
  ELEMENT, decided by what each INIT does with the element rather than by the
  tuple type (a literal's inferred type has no place to say `Own`, and
  annotating one is rejected as redundant). An element a **literal** binds
  from a fresh rvalue -- `A(1)`, an `Own[T]`-returning call, a local at its
  last use -- is storage the frame OWNS, as is every element an **owning
  call** hands over (`t = make_pair()`, returning `Own[tuple[...]]` or
  `tuple[Own[A], ...]`); an element bound from a live lvalue stays a pointer
  at that object, so a mutation the caller makes after the suspension is read
  back through the tuple (`t = (a, 2); yield t[0].v; a.v = 99; yield t[0].v`
  prints `1 99`, matching CPython). All-fresh gives
  `frame_slot<std::tuple<A, int32_t>>`, all-lvalue the plain borrow field
  `std::tuple<A*, int32_t>`, and a mix the mixed render
  `frame_slot<std::tuple<A, A*>>` -- the three frame kinds an `Own`-annotated
  tuple already used. The verdict is joined over EVERY init of the name, so a
  local reassigned in a loop keeps it, and a literal in one branch with an
  owning call in the other agrees (both hand the element over) rather than
  conflicting. Two shapes stay rejected because the element has no single
  form: an element whose value is a **ternary** over a fresh object and an
  existing one (owning it would COPY where CPython aliases), and an element
  one init hands over while another only borrows (a named diagnostic pointing
  at separate names) -- there the borrow would have to point into the owning
  init's dead temporary. A tuple loop **variable**
  over a container (`for t in xs:` where the element tuple holds a reference)
  is the sibling rule: the frame field is a pointer to the source element
  tuple, so the loop var aliases the container element like the sync
  `auto&&` binding does.
- An `Optional` *of* a pointer-repr tuple (`tuple[..., Box] | None`) is a nullable borrow-form tuple local -- `std::optional<std::tuple<..., T*>>`. The optional wraps the *borrow*-form inner tuple, so reassigning the local (`t = h.pair`) ALIASES the source's reference elements rather than copying them (matching CPython); a write through the narrowed local (`if t is not None: t[1].val = ...`) is visible on the source. `t = None` is `std::nullopt`. An owning-call init (`= make_pair()`, whose return is `tuple[..., Own[T]]`) works with this same plain annotation: the owning return materializes into a slot the local aliases. Annotating the local itself with `Own` (`tuple[..., Own[T]] | None`) is rejected as redundant -- the local is borrow form regardless.
- A value-repr `Optional` of a VALUE tuple (`tuple[float, int32] | None`, `tuple[str, int32] | None`) is `std::optional<std::tuple<...>>` by value at params, locals and the return slot: `return None`, a tuple literal and an un-narrowed value-tuple name all land in the return slot (the optional's converting ctor absorbs the bare tuple). A narrowed read (`if r is not None:`) derefs the optional, so `a, b = r` binds its holder to the contained tuple (`const auto& __tup_N = (*r);`, zero-copy -- a `str` element's view target aliases the tuple's owned element); the whole-optional read (the None test, `s: tuple[...] | None = r`) stays bare. A `str`/`bytes` unpack target is a view into the source tuple's element, and a later rebind of the source while the target is live (`a, b = r; r = find(2); print(a)`) is not diagnosed (BUGS.md#tuple-unpack-view-outlives-reseat). Not yet on a resumable frame's params/returns, at a method's param slot from a literal, in `__init__` from a literal, or in container-element / `dict.get` / walrus positions (BUGS.md#res-param-value-opt-tuple, BUGS.md#method-arg-value-opt-tuple-literal, BUGS.md#ctor-field-value-opt-tuple-literal, BUGS.md#value-opt-tuple-container-slots).
- A tuple local can mix an OWNING value (a call returning `Own[tuple[...]]`) with a reference to existing storage -- a rebind (`t = make_pair(); t = h.pair`), branch-mixed first bindings (`if c: t = make_pair() else: t = h.pair`), and the walrus form (`(t := make_pair())[0]; t = h.pair`). The local takes one fixed C++ shape, borrow form (`std::tuple<..., T*>`): an owning-call RHS materializes into a function-local `std::optional<std::tuple<..., T>>` slot the local aliases via `tuple_to_pointer`, a storage-form lvalue RHS lifts element-wise, a borrow RHS assigns directly -- so a rebound alias shares the source's elements like CPython (mutation through it is visible on the source). The declared per-element const-ness is the OR over all binding sources' const-ness (a const source -> `const T*`). `return t` is rejected only when the local is *possibly* owning at the return (the slot is function-local), via a flow-sensitive (snapshot + UNION-merged) owning fact. An owning tuple local in a generator/async body is backed by a `tpy::frame_slot<std::tuple<..., T>>` storage field. The owning binding may be an outer-`Own` call (`Own[tuple[...]]`), a per-element-`Own` call (`tuple[..., Own[T]]`), or an `await`-result lift temp (`a, b = await f()`) -- an awaited tuple is always owned, so even a reference-element result (`tuple[list[T], int]`) gets owning `frame_slot` storage rather than a borrow-form field, and is moved out at the unpack. A reassigned per-element-`Own` local collapses to the unified borrow type (each `Own[T]` element -> `T`) so the same borrow-slot path applies -- except a MIXED tuple, which has no unified form to collapse onto and instead keeps the mixed render (`std::tuple<A, B*>`) at every binding path, assigned straight with no slot. A REASSIGNED owning-call local inside a resumable body takes the owning frame slot at every init (each rebind emplaces fresh storage); mixing an owning init with an aliasing one there is rejected by name, since the two need different fields -- see BUGS.md.
- Comparison (`==`, `!=`) requires element-wise type compatibility; ordering (`<`, `>`, `<=`, `>=`) is lexicographic, but is rejected on tuples with an Optional element (CPython raises TypeError when `None` meets an ordering comparison)
- An inferred ref-tuple of `@nocopy` elements (`p = (a, b)` of two `@nocopy` locals, no annotation) is rejected with a clean diagnostic. The default ref-capture (`std::tuple<T*, T*>`) cannot be promoted to a value tuple later, which would otherwise produce cryptic C++ errors on use. Annotate `p: tuple[T, ...]` to consume the sources (last-use `@nocopy` locals auto-move into the value tuple), or place the literal directly at its consumer.

### Pointers/References
- **Working**: `Ptr[T]` -> `T*`
- **Working**: `Ptr[readonly[T]]` -> `const T*`
- **Working**: `Own[T]` -> `T` (ownership transfer for return values)
- **Working**: `Rc[T]` / `Weak[T]` -- pure-TPy non-atomic single-threaded shared-ownership smart pointer with a non-owning companion (`tplib/rc.py`). One heap allocation per `Rc.new`: the cell is generic `_RcCell[U]`, derived from a `@dynamic _RcCellBase` protocol, and holds strong/weak counters plus an inline `UninitStorage[U]` payload. Rc holds two pointers (`_cell: Ptr[_RcCellBase]` for refcount + virtual dispatch, `_payload: Ptr[T]` for fast deref -- aliases into the cell's inline storage). Cell virtuals are *fused* (`incr_strong`, `release_strong`, `try_incr_strong`, `incr_weak`, `release_weak`) so each Rc/Weak op dispatches at most one vcall; `release_strong` does the strong-zero -> drop_payload -> decrement-collective-weak chain in one call, preserving the invariant that a nested `Weak.__del__` triggered by the payload destructor sees `weak >= 2` and can't free the cell. Cell deallocation defers until the last `Weak` drops (weak reaches 0), so `Weak.upgrade()` can safely check `strong > 0` against still-valid memory and return `Rc[T] | None`. `@nocopy` at the TPy level: deliberate sharing is always explicit via `Rc.clone()`, `Rc.downgrade()`, `Weak.clone()`, or `Weak.upgrade()`. `Rc[T]` implements `Deref[T]` for transparent field/method access in TPy (`r.x`, `r.method()`); under CPython use `r.get().x` explicitly because the auto-deref protocol isn't simulated -- tests relying on `r.x` syntax need `no_cpython.txt`. `Weak[T]` deliberately does NOT implement `Deref` -- access must go through `upgrade()` so callers handle the "payload already dropped" case. `Rc[T]` is `Covariant[T]`: `Rc[Parrot]` -> `Rc[Pet]` works for inheritance conformers (the converting move ctor transfers `_cell` and upcasts `_payload` via standard C++ pointer upcast). For `@dynamic` protocol P, both structural and inheritance conformers work via the `Rc.new[U: T]` factory: `r: Rc[Pet] = Rc.new(Parrot(...))` (inheritance: cell is `_RcCell<Parrot>`, payload upcasts `Parrot*` -> `Pet*`) and `r: Rc[Pet] = Rc.new(Cat(...))` (structural: codegen substitutes the method-level type-arg `U -> Adapter<Pet, Cat>` so the cell is `_RcCell<Adapter<Pet, Cat>>`; Adapter inherits Pet, so `Adapter<Pet, Cat>*` upcasts cleanly to `Pet*`). The structural-conformer path is driven by a *representational-use* mark recorded on the canonical `FunctionInfo` during body analysis (the body coerces `Ptr[U] -> Ptr[T]` via `cell.storage.ptr()`) and read by codegen at the call site. Mutation through any clone is visible to all other clones; for shared-immutable use `Rc[readonly[T]]`. `_cell` is declared `unsafe_interior_mutable[Ptr[_RcCellBase]]` -- the refcount is bookkeeping outside the readonly boundary (the std::shared_ptr const-copy pattern) -- so `clone`/`downgrade`/`upgrade`/`Weak.clone` are `@auto_readonly`: callable on a `readonly[Rc[T]]` handle (e.g. cloning an `Rc` field inside a `@readonly` method), and from a readonly handle they yield a readonly-payload handle (`Own[Rc[readonly[T]]]`), so readonly can't be laundered into mutable `T`. Dunder surface on Rc mirrors `Box[T]`: `__str__`, `__repr__`, plus `__eq__` (content equality, delegates to `T.__eq__`, gated on `T: Equatable`), `__lt__`/`__le__`/`__gt__`/`__ge__` (gated on `T: Comparable`), and `__hash__` (gated on `T: Hashable`). Cycles between two strong `Rc` handles still leak (the canonical fix is to wire one edge of the cycle as `Weak`; see `weak_cycle_breaks` test). `Rc`'s refcount is non-atomic (single-threaded by construction, so it's `!Send`/`!Sync` for free from its raw-pointer fields); the atomic sibling is `Arc[T]` (`tplib/arc.py`, shipped). Construct via `Rc.new(value)`; the `Rc(other)` sharing-ctor shape is still blocked by a sema bug filed in `BUGS.md`. `dict[K, Rc[T]]` literal initialization works (`d = {"a": r.clone(), ...}` lowers via `tpy::make_ordered_map`, preserving move semantics for the @nocopy value). `Rc[T]` is rejected as a `set` element / `dict` key with a precise diagnostic -- hash-table-backed containers store keys in `std::pair<const K, ...>` and require copy-constructible K, which `@nocopy` cannot satisfy. Import: `from tplib import Rc` for the strong-handle surface; `Weak` lives only at `tplib.rc.Weak` (not re-exported flat) so the future `Arc[T]` companion at `tplib.arc.Weak` can take the same bare name without collision -- mirrors `std::rc::Weak` vs `std::sync::Weak` in Rust. Use `from tplib.rc import Rc, Weak` when both handles are needed.
- **Working**: `Arc[T]` / `Weak[T]` -- atomic-refcount sibling of `Rc`/`Weak` (`tplib/arc.py`), a near-clone whose only behavioral delta is that the strong/weak counters are `Atomic[uint32]`, so handles can be moved and shared across threads. `Arc[T]` is `Send + Sync` exactly when `T` is both (Rust's `Arc<T>` rule), via the conditional `@unsafe_send(if_params_send=True, if_params_sync=True)` / `@unsafe_sync(...)` override -- the handle's raw-pointer fields make it structurally non-Send, and the conditional lifts it only for the type args that make it sound. No constructor gate: `Arc[non-Send-Sync]` is a legal *non-Send* value (like Rust's `Arc<Rc<_>>`), rejected only where it crosses a thread boundary (`spawn`), with a why-not chain that names the offending type parameter and the marker it lacks. Refcount ordering follows `std::sync::Arc`: clone `fetch_add(1, Relaxed)`; drop `fetch_sub(1, Release)` + an `Acquire` fence on the final decrement before the payload destructor; `upgrade` a CAS loop with `Acquire`. Everything else -- the fused `@dynamic _ArcCellBase` cell, `UninitStorage[U]` payload, `Deref[T]`, `Covariant[T]`, the dunder surface, `@auto_readonly` clone/downgrade, `Weak.upgrade() -> Arc[T] | None` after the last strong drop -- mirrors `Rc` exactly. Composes with V1 `tpy.thread.spawn`: `arc.clone()` into a `@nocopy` task's field carries shared state across the spawn boundary with no closures (`tests/cases/threading/arc_spawn`). Import: `from tplib.arc import Arc, Weak` -- deliberately NOT re-exported from `tplib` (that would pull the atomic runtime into every `from tplib import ...` consumer, against the "pay nothing if you don't use threads" principle); `Weak` coexists with `tplib.rc.Weak`. Built on the general `Atomic[T: AnyFixedInt]` primitive (`tpy.atomic`).
- **Working**: `tpy.unsafe` -- unsafe pointer operations (`unsafe_ptr`, `unsafe_load`, `unsafe_store`, `unsafe_copy_n`, `unsafe_ptr_add`, `unsafe_ptr_diff`, `unsafe_cast`, `unsafe_const_cast`, `unsafe_str_view`, `unsafe_str_from_cstr`, `unsafe_cstr`, `unsafe_alloc`, `unsafe_alloc_n`, `unsafe_free`, `unsafe_init`, `unsafe_drop`, `unsafe_move_out`)
- **Working**: `tpy.mem` -- uninitialized storage primitives (`UninitArrayStorage[T, N]`, `UninitHeapStorage[T]`, `UninitStorage[T]`)
- **Working (internal)**: `Ref[T]` -- internal type for explicit reference semantics. Flows through the type system uniformly: auto-inserted on function params/returns, preserved on non-reassigned locals, returned by field access and subscript. Detects implicit copies when storing borrowed references into fields/containers (complemented by `needs_copy_warning` for owned lvalue copies). Also drives lambda trailing return types (`-> T&`) and `val_or_ref<T>` template args for iterator combinators. Not user-facing -- users see `T` in annotations, the compiler infers reference vs owned.
- **Planned**: `ConstRef[T]` -> `const T&` (explicit annotation; automatic const qualification via mutation inference already covers most cases -- see Parameter Passing Convention)

#### Pointer Coercions (Working)

Implicit conversions between records and pointers with safety checks:

| From | To | Constraints | Generated C++ |
|------|-----|-------------|---------------|
| `T` (record) | `Ptr[T]` | Mutable lvalue, not in return | `&expr` |
| `T` (record) | `Ptr[readonly[T]]` | Lvalue, not in return | `&expr` |
| `Child` (record) | `Ptr[Parent]` / `Ptr[readonly[Parent]]` | Lvalue, not in return; `Child` inherits `Parent` (record-to-record) or implements `Parent` (record-to-`@dynamic`-protocol) | `&expr` (C++ implicit base-pointer upcast) |
| `P` (`@dynamic` protocol value) | `Ptr[P]` / `Ptr[readonly[P]]` | Lvalue, not in return; identity (same protocol). Protocol value is already a `Base&` reference; `&` takes its address as `Base*`. Same coercion fires when a callee param `: P` is forwarded to a `Ptr[P]` sink or stored into a `Ptr[P]` field | `&expr` |
| `W` (generic type param) | `Ptr[W]` / `Ptr[readonly[W]]` | Lvalue, not in return; identity (same param). Lets a generic holder store `Ptr[W]` of its by-ref param (e.g. `csv.writer`). A value-bounded `W` (passed `const W&`) may only take the `readonly` form; a mutable `Ptr[W]` on a value-bounded `W` is rejected. `W: int` (INT param) is a value, not addressable | `&expr` |
| `Ptr[Child]` | `Ptr[Parent]` / `Ptr[readonly[Parent]]` | Same covariance criterion as above; mutable source coerces to either, readonly source only to `Ptr[readonly[Parent]]` (cannot launder const) | `expr` (identity; C++ upcasts implicitly) |
| `Ptr[U]` | `Ptr[B]` | Inside a generic body where `U` is declared with a subtype bound `U: B` and `B` is a **class** or a (sibling/enclosing) **type parameter** -- see note below | `expr` (identity; C++ upcasts implicitly) |
| `Ptr[T]` | `T` | Null-checked at runtime | `tpy::deref_check(expr)` |
| `Deref[T]` type | `T` | Via `__deref__()` | `expr.__deref__()` |
| `Ptr[T]` | `Ptr[readonly[T]]` | - | (implicit) |

**Safety rules:**
- Taking address requires an lvalue (variable, field, or subscript) - temporaries rejected
- Return statements cannot convert local records to pointers (dangling pointer prevention)
- `Span[readonly[T]]`/str elements cannot convert to `Ptr[T]` (read-only source)
- `Ptr[T]` → `T` includes runtime null check that panics if null

**Borrow + mutation semantics**: every implicit value-to-`Ptr[T]` coercion (the `T (record) -> Ptr[T]` family and the `Child -> Ptr[Parent / @dynamic Protocol]` upcasts) carries the same borrow tracking and mutation signal as an explicit `take_ptr(x)` call: the source storage is registered as a `Ptr` borrow, and (for mutable target) the source root is marked mutated so Phase 2 keeps it as `T&` (not `const T&`). The destination Ptr is also recorded as non-null (since `&lvalue` can't be null), enabling deref-check elision on subsequent field/method accesses.

**Bounded type-parameter coercion** (Working for class / type-param bounds): a type parameter may carry a *subtype* bound -- a class (`def f[U: Animal]`) or another (sibling/enclosing) type parameter (`def make[U: T]` on `class Holder[T]`) -- in addition to the existing protocol (capability) bounds like `T: Comparable`. Inside the generic body, `Ptr[U]` then coerces to `Ptr[B]` as a plain C++ pointer upcast (`U*` -> `B*`); the bound emits an unconstrained `typename U` template parameter (a class/type-param bound is not a C++ concept). The subtype is enforced *nominally* at the call site by `satisfies_bound` (which uses `is_subclass_of` for class bounds), so a non-subtype type argument is rejected with a clear diagnostic. A **protocol target** (`Ptr[U] -> Ptr[Pet]` for a `@dynamic` protocol `Pet`) is deliberately **declined**: a structural conformer satisfies `U: Pet` without inheriting Pet's C++ base, so the plain pointer upcast would be invalid -- those need the adapter path. This is the sema prerequisite for the single-allocation `Rc[@dynamic P]` migration. A bounded *factory* -- `@staticmethod def make[U: T](value: Own[U]) -> Own[Box[T]]` -- is callable by inference: `b: Box[Pet] = Box.make(Parrot(...))` resolves `U` from the argument and `T` from the LHS hint, validating the `U: T` bound against its *substituted* form (inference checks each inferred arg against its bound only after substituting already-inferred params into it; otherwise `U: T` would be checked against the raw `T`). The *explicit-type-args* path (`f[A, B](...)`, and the method form `obj.m[A, B](...)`) substitutes resolved sibling type parameters into the bound the same way before checking, so a bound naming a sibling -- `def f[R, T: Container[R]]`, or a method bound naming a class-level param `class C[R]: def m[T: Container[R]]` -- is validated as `Container[<resolved R>]`, not the raw `Container[R]`. Hint-free `x = Box.make(...)` still needs the deferred default-`T=U` rule -- see TODO.md.

**Associated-type inference** (Working): a sibling param that appears only inside a generic-protocol bound is solved from the concrete conformer -- `def unwrap[R, T: Container[R]](x: T) -> R` called as `unwrap(IntBox(...))` infers `T = IntBox` from the argument and then `R = int32` by unifying the protocol's member signatures (`get() -> R`) against `IntBox`'s concrete methods. The pass fires only for type params still unresolved after argument matching, so it never changes the outcome of a call that already infers. Type params are likewise inferred *through* the transparent `Send[T]`/`Sync[T]` marker wrappers (`def spawn[...](task: Send[Own[T]])` infers `T` from a bare task argument), and a marker on an argument's declared type no longer leaks into the inferred param. Together these make `tpy.thread.spawn(task)` fully inferred. A `-> None` conformer method satisfies a bound instantiated with `R = None` (the void-like binding canonicalizes to `None`; the generated concept accepts a void-returning conformer via `tpy::proto_result`). A bound naming a protocol with *two or more* own type params (`def take[K, V, T: Pair[K, V]](x: T)`) solves all of them positionally from the conformer's method signatures, distinct params tracked independently. The one remaining exclusion: a multi-param protocol reached via an *extends* declaration (protocol inheritance) rather than a direct conformer resolves only its first param -- see TODO.md. Note: the sibling-referencing bound spelling itself (`[R, T: Container[R]]`) is an intentional TPy extension -- current type checkers (mypy, pyright) reject a generic bound, while CPython erases annotations and runs the code unchanged.

#### Pointer Constructors (Working)

Explicit constructors for `Ptr[T]` and `Ptr[readonly[T]]`, as an alternative to implicit coercions:

```python
from tpy import Ptr, int32

def test() -> None:
    # Null pointers
    p: Ptr[None] = Ptr[None]()        # → void* (opaque-pointer idiom for C interop)
    q: Ptr[int32] = Ptr[int32]()      # → nullptr (typed null)

    # Address-of with take_ptr()
    x: int32 = int32(42)
    p: Ptr[int32] = take_ptr(x)              # → &x
    cp: Ptr[readonly[int32]] = take_ptr(x)   # → &x (coerces to const)
```

**`take_ptr(x)`** takes the address of a mutable lvalue, returning `Ptr[T]`. It can be coerced to `Ptr[readonly[T]]` at the assignment target.

**`Ptr[readonly[T]]`** is the canonical form for read-only pointers. Both spellings are equivalent; `Ptr[readonly[T]]` is the canonical form:

```python
from tpy import Ptr, readonly, int32, take_ptr

x: int32 = int32(42)
p: Ptr[readonly[int32]] = take_ptr(x)
```

**Safety rules:**
- Argument must be a mutable lvalue (`take_ptr(Point(1,2))` rejected -- temporary)
- Dangling detection works through `take_ptr` and intermediate variables:

```python
def bad() -> Ptr[int32]:
    x: int32 = int32(1)
    return take_ptr(x)     # ERROR: returned pointer would dangle

def also_bad() -> Ptr[int32]:
    x: int32 = int32(1)
    p: Ptr[int32] = take_ptr(x)
    return p                # ERROR: returned pointer would dangle

def ok(x: int32) -> Ptr[int32]:
    return take_ptr(x)     # OK: x is a parameter
```

#### Auto-Deref via `Deref[T]` Protocol (Working)

Types that implement `__deref__() -> T` conform to the `Deref[T]` protocol and support **auto-deref**: the compiler automatically resolves field access and method calls through `__deref__` chains.

`Ptr[T]` and `Ptr[readonly[T]]` conform to `Deref[T]`. User-defined types can also implement `__deref__`:

```python
from tpy import int32, copy

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> int32:
        return self.x + self.y

class Ref:
    _target: Point
    def __init__(self, target: Point) -> None:
        self._target = copy(target)
    def __deref__(self) -> Point:
        return self._target

def main() -> None:
    r: Ref = Ref(Point(10, 20))
    print(r.x)     # auto-deref: r.__deref__().x -> 10
    print(r.sum())  # auto-deref: r.__deref__().sum() -> 30
    r.x = int32(99)  # mutation through __deref__ works -- dual overloads are implicit
```

The plain `def __deref__` above implicitly gets mutable + const overloads because the return type is a reference type. Explicit `@auto_readonly` is no longer needed for `__deref__`, `__getitem__`, and `__span__` with reference-typed returns -- the compiler synthesizes the pair via `IMPLICIT_AUTO_READONLY_METHODS`. Write `@readonly` to opt back into a single const overload.

Multi-hop chains are supported — if `Box.__deref__() -> Ref` and `Ref.__deref__() -> Point`, then `box.x` resolves through both (max depth: 8). Auto-deref also works through `Optional` receivers (`Ref | None`).

**Deref coercion:** Types with `__deref__() -> T` also coerce to `T` in assignment, argument, and return contexts. For example, a user `Ref` with `__deref__() -> Point` can be passed where `Point` is expected — the compiler inserts `ref.__deref__()` automatically. `Ptr[T]` uses `tpy::deref_check()` for null-checked coercion.

**Pointer None semantics:**

- `None` can be assigned to `Ptr[T]` and `Ptr[readonly[T]]` (represents `nullptr`)
- `p is None` / `p is not None` work for `Ptr[T]` and `Ptr[readonly[T]]`
- `p == None` / `p != None` are rejected; use identity checks (`is` / `is not`)
- `Ptr[T] | None` is normalized to `Ptr[T]` at type construction (and the readonly variant likewise). The two spellings describe the same already-nullable `T*`. Equivalent spellings (`Optional[Ptr[T]]`, `None | Ptr[T]`, `Ptr[readonly[T]] | None`, etc.) are also collapsed. The compiler emits a warning at the redundant spelling site suggesting the plain form.
- `T | None` (for non-value `T`) and `Ptr[T]` interconvert at coercion boundaries (call args, returns, locals, field assignment) because both lower to `T*` at borrow positions. The two remain distinct types (semantically: `T | None` is "nullable T" with optional storage-form lifting; `Ptr[T]` is "explicit pointer to T" always in pointer form). Storage-form Optional sources -- field reads or subscripts of an `Optional[T]` field/container -- are lifted automatically via `tpy::optional_to_ptr` when flowing into a `Ptr[T]` slot (same machinery used for storage-form sources flowing into pointer-form Optional slots). The mutable-source -> readonly-destination inner-narrowing direction is allowed; the reverse (readonly -> mutable) is rejected.

**Null-safety:** Auto-deref through `Ptr[T]`/`Ptr[readonly[T]]` is null-checked at runtime via `tpy::deref_check()`. A null pointer access panics with "null pointer dereference" instead of causing undefined behavior. Pointers with known non-null provenance skip the null check and use direct `->` access. Non-null provenance is established by:

- `take_ptr`: `p = take_ptr(x)` (pointer to a local variable)
- Direct value-to-`Ptr[T]` coercion: `p: Ptr[T] = x` / `cp: Ptr[readonly[T]] = x` / `bp: Ptr[Parent] = child` (emits `&x` -- same provenance as `take_ptr`)
- Condition narrowing: `if p is not None:` / `if p is None: return` / `assert p is not None` / `while p is not None:`
- Field-path narrowing: `if self.ptr_field is not None:` / `if obj.field is not None:` (dotted paths at any depth)
- Assignment from a known non-null variable

Narrowing supports negation and `and`/`or` composition. Branch merging uses intersection (non-null only if all paths agree). Reassignment from an unknown source (e.g., function return) clears non-null provenance. Field-path narrowing is invalidated by method calls on the receiver object, field writes, or passing the object to a function by mutable reference -- and equally through any local statically known to alias the receiver.

Narrowing facts (and value-range facts driving bounds/div-zero check elision) are killed at every control-flow meet where some path may have invalidated them: loop body entry drops facts for names the body may write (the back-edge re-enters after the kill), `except` handlers drop facts the try body may have killed (an exception can be thrown at any point in it), `finally` bodies run under all-paths entry facts, and any call kills facts for names a previously-defined closure writes via `nonlocal` and for value-type module globals (any callee may rebind one via `global`; non-value globals are exempt since reassigning them from a function is rejected). A call embedded in the condition itself kills those same facts for the whole condition (evaluation order inside `and`/`or` is not tracked). Suspension points (`yield`, `await`, and the awaits inside `async for` / `async with`) kill every fact rooted in storage reachable from outside the resumable frame -- field-path facts, deref-view facts, and len-symbolic ranges -- since the caller (or another task) may mutate that storage before resume; plain-name facts on params and locals survive (frame-private bindings), except closure-written names and rebindable value-type globals, which die exactly as at a call site (the caller can rebind them while the frame is suspended). Dropped facts mean the runtime check is emitted (warning for Optional access) -- re-prove with a guard or `assert` inside the region to elide it; for a global, bind it to a local before the call (`v = G`) to keep the narrow, and for a field across a suspension, bind the value to a local before suspending.

**C++ interop:** User-defined types with `__deref__()` get `operator*()` generated in C++, enabling `*box` syntax from C++ code.

#### Unsafe Pointer Operations (Working)

Low-level pointer arithmetic for C interop and performance-critical code. These bypass bounds checking. The API uses free functions from `tpy.unsafe` (not method calls on pointers):

```python
from tpy import Ptr, int32, uint32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

# Get raw pointer to array data
arr: Array[int32, 4] = [int32(10), int32(20), int32(30), int32(40)]
p: Ptr[int32] = unsafe_ptr(arr)

# Indexed read/write (no bounds check)
val: int32 = unsafe_load(p, uint32(2))       # -> 30
unsafe_store(p, uint32(0), int32(99))        # arr[0] = 99

# Ptr[readonly[T]] has unsafe_load only (no store)
cp: Ptr[readonly[char]] = unsafe_ptr("hello")
```

Generated C++: `unsafe_ptr(x)` -> `x.data()`, `unsafe_load(p, i)` -> `p[i]`, `unsafe_store(p, i, v)` -> `p[i] = v`.

See the full `tpy.unsafe` API [below](#unsafe-memory-operations----tpyunsafe-working) for `unsafe_copy_n`, `unsafe_ptr_add`, `unsafe_ptr_diff`, `unsafe_const_cast`, and `unsafe_cast`.

#### Owned Return Values (Working)

Reference types are normally returned by reference (`T&`) to avoid hidden copies. But this creates a problem when returning newly constructed objects:

```python
def create_point() -> Point:
    p: Point = Point()
    p.x = 10
    p.y = 20
    return p  # ERROR: dangling reference to local variable
```

The compiler detects this as a dangling reference error. Use `Own[T]` to indicate the function returns a newly constructed object by value (with move semantics):

```python
from tpy import int32, Own, copy

def create_point() -> Own[Point]:
    p: Point = Point()
    p.x = 10
    p.y = 20
    return copy(p)  # OK: explicit copy for ownership transfer

def main():
    pt: Point = create_point()  # Own[Point] coerces to Point
    print(pt.x)  # 10
```

Returning an rvalue (like a constructor call) doesn't require `copy()`:

```python
def make_point(x: int32, y: int32) -> Own[Point]:
    return Point(x, y)  # OK: constructor call is an rvalue
```

A call is not automatically an rvalue, though: a method or function declared
`-> T` for a reference type `T` hands back `T&`, an alias of storage the
caller can still reach. Filling the by-value slot from it is a copy, and the
copy is observable -- CPython would hand back the very object -- so it needs
`copy()` like any other borrowed source:

```python
class Holder:
    p: Payload
    def borrow(self) -> Payload:      # returns Payload&
        return self.p

def take(h: Holder) -> Own[Payload]:
    return h.borrow()                 # warning: copies Payload into owned
                                      # storage; use copy() to make this
                                      # explicit
```

`readonly[Own[T]]` is the same owning return slot with a const view on top,
so it takes the same check -- the spelling is not a way past it.

The escape hatch is one step for either payload family and over any borrowed
source shape -- a method or free call, a field read, a ternary of two calls,
an awaited borrow: `return copy(h.bctr())`, `return copy(h.brec())`,
`return copy(await h.borrow())`.

Every slot that coerces to `Own[T]` treats this the same way -- the return,
a list `append` / `insert` / `__setitem__`, a `set` `add`, an `Own[T]`
parameter of a free function, method or constructor, and a `yield` at
`Iterator[Own[T]]`. Each takes a borrow-returning call as a borrowed source,
copies it, and reports the copy as the same warning the lvalue spelling
(`xs.append(h.p)`) already gets:

```python
xs.append(h.borrow())   # warning: copies Payload into owned storage;
                        # use copy() to make this explicit
```

A `@nocopy` payload upgrades that warning to a located error at every slot:
there is no copy for the warning to describe. Writing
`xs.append(copy(h.borrow()))` silences it and copies. The warning is not
always the last word: the
`Own[T]` parameter, `yield` and container-element slots warn and then
reject the same statement in code generation, so those spellings do not
compile at all today.

A call that already yields the slot's STORAGE form -- an `Own[list[...]]`,
`Own[dict[...]]` or `Own[set[...]]` return, or a by-value reference-type
return -- is the other side of that rule: it is admitted at every
container-element slot and MOVED in, never copied. `[make(i) for i in
range(n)]`, `[make(1), make(2)]`, `{k: make(k) for k in ks}` at an
annotated `dict[K, list[T]]` slot, a set literal, an `Array` slot, a
constructor member-init and a module-level literal all build from the
callee's fresh containers, the same way `out.append(make(i))` and
`d[k] = make(k)` always did; the element slot reads one storage-form
verdict shared with those argument positions. A moving literal renders
through the `make_vector` / `make_ordered_*` helpers, whose operands are C++
call arguments, so their evaluation order is the compiler's, not Python's
(see "Evaluation Order" under Expressions). The BORROW-returning call at the
same slot (`[h.rows() for h in hs]` with `rows() -> list[str]`) keeps the
warning-then-reject above. The unannotated dict comprehension whose value
is such a call infers `Own` into the value type and still rejects
(`BUGS.md#dict-comp-value-keeps-own`), and an empty nested literal cannot
share a literal with a moved element yet
(`BUGS.md#container-lit-empty-elem-under-emplace`).

One slot keeps the error: a per-element `Own[...]` inside a returned
`Own[tuple[...]]`. The whole-slot copy has a render; the per-element one does
not, so a borrowed element source is rejected rather than warned.

One rule, no lifetime reasoning: a borrow-returning call at an owning slot is
a borrowed source, whatever its receiver is. A temporary receiver does not
exempt it -- what the callee hands back can reach past its receiver
(`Wrapper(take_ptr(h.o)).get()` returns `h.o` through a pointer field), so
`Point(n).updated()` at an `Own[Point]` return copies and warns like any
other borrowed source. Declaration order cannot change the verdict, because
no callee-body fact enters it.

A slot whose `Own` payload is still an unresolved type parameter is no
exception: the copy is the same one the monomorphic twin makes, and `copy()`
spells it there (its readonly-source admission is what makes that possible).
The body reports it AT ITS OWN LINE, hedged, whatever anything instantiates
it at:

```
main.py:11: warning: may copy T into owned storage if not a value type; use copy() to make this explicit
```

This is the **copy contract of a generic body**, and it is deliberately a
declaration-time diagnostic rather than a per-instantiation one. A library
author writing `def cycle[T](it: Iterable[T])` has no instantiation to
consult, and copyable is TPy's default so no bound is ever required to write
the generic -- so the only place the contract can be stated is the
declaration. Two things silence it, and only two:

- **`copy()` at the slot** -- the author saying the copy is intended. It
  compiles at an open `T` (`xs.push_back(T(v))`), and costs nothing at a
  value instantiation, where `T(v)` is a no-op cast.
- **a `T: ValueType` bound** -- under it a reference-type copy cannot happen,
  so there is nothing to warn about.

`T: Copyable` does NOT silence it. Copyable is the default, so that bound
only rules out a non-copyable instantiation; it does not say a copy was
intended, and it must not stand in for `copy()`.

Instantiating at a **non-copyable** type -- `@nocopy`, or a record with a
`__del__` (which deletes the generated struct's copy constructor), or one
that transitively owns either -- PROMOTES the same line to the error the
monomorphic twin reports:

```
main.py:11: error: cannot copy non-copyable type 'Pinned' into owned storage (use Own[...] to transfer ownership, copy() if a copy is truly intended, or let auto-move apply at last use)
```

which replaces what used to be forty lines of libstdc++ template spew.
`@nomove` alone is not such a type: it forbids the implicit *move*, and a
`@nomove` payload with a usable copy constructor still takes the ordinary
`copies X into <sink>` warning and is copied.

Every route reaches the promotion -- function call, method call, constructor (including
inferred type args), a `class Child(Base[Pinned])` header, a chain of generic
bases, a call from another module, and the `tpyc -c` / stdin / REPL path.

**Accepted divergence from the monomorphic twin.** The twin names the type it
copies, once per concrete type; the generic hedges once, at its declaration,
including where the program only ever instantiates it at value types. That
trade buys the library author a contract they can read without a call site;
see `docs/PITFALLS.md#generic-equals-monomorphic-twin`.

Generated C++:
```cpp
Point create_point() {  // Returns by value, no &
    Point p{};
    p.x = 10;
    p.y = 20;
    return p;  // RVO/NRVO eliminates copy
}
```

Key points:
- `Own[T]` parameters use `T&&` in C++ for non-value types (zero-cost ownership transfer), or `T` by value for value types (int, bool, float, etc. where copy = move)
- Generic `Own[T]` where T is a type parameter uses `std::type_identity_t<T>&&` to prevent forwarding-reference deduction
- Relies on C++ move semantics and RVO/NRVO for efficiency
- Returning a borrowed source copies, with a warning; `copy()` makes the intent explicit and silences it. Borrowed means the source aliases storage that outlives the return: an lvalue (variable, field access, subscript) **or** a call whose declared return is a bare reference type (`def borrow(self) -> Payload`), which hands back `Payload&` -- including a bare type parameter (`def borrow(self) -> T`), which hands back that same reference at every reference-type instantiation. A call through a callable VALUE (`f(x)` where `f: Fn[[T], K]`) is NOT one today: its signature is the `Fn` type's rather than a declaration the compiler checked, so it reads as an rvalue -- generic and monomorphic alike. That is right for a callable that builds a fresh value and WRONG for one bound to a borrow-returning function, which copies at the owning slot with no warning (BUGS.md#callable-value-borrow-return-copies-unwarned). Designed: `docs/CLOSURES_CALLABLE_DESIGN.md` section VII reads a callable's result form off its spelled return by the same rule a `def` return obeys -- not started, so the behaviour above is what ships today
- Returning an rvalue is OK without `copy()`: a constructor call, an owning call (`-> Own[T]`), or a container factory (`list(xs)`, `bytearray(b)`). A borrow-returning call is NOT one, even on a temporary receiver (`make(x).updated()` warns and copies)
- `Own[T]` coerces to `T` when receiving the value

#### Copy Warnings for Inline Storage (Working)

Assigning a non-value-type lvalue to inline storage (record fields, container elements) creates a value copy in C++, but would create a shared reference in CPython. The compiler warns to make the semantic difference explicit:

```python
class Rect:
    corner: Point

    def set_corner(self, p: Point) -> None:
        self.corner = p           # WARNING: copies Point into field
        self.corner = copy(p)     # OK: explicit copy
        self.corner = Point(1, 2) # OK: rvalue, no existing owner
```

Container storage methods (`append`, `insert`, `__setitem__`, `extend`, `+=`, `update`, `|=`) and constructors (`list()`, `set()`, `dict()`) use `Own[T]` parameters to trigger the same warning:

```python
items: list[Point] = []
p: Point = Point()
items.append(p)           # WARNING: copies Point into owned storage
items.append(copy(p))     # OK: explicit copy
items.append(Point())     # OK: rvalue, no existing owner
items[0] = p              # WARNING: copies Point into container
items.insert(0, p)        # WARNING: copies Point into owned storage
more: list[Point] = [Point()]
items.extend(more)        # WARNING: copies Point elements
items += more             # WARNING: copies Point elements
items.extend(copy(more))  # OK: explicit copy
items2 = list(more)       # WARNING: copies Point elements
items2 = list(copy(more)) # OK: explicit copy
pts: set[Point] = set(more)       # WARNING: copies Point elements
pts = set(copy(more))             # OK: explicit copy
pairs: list[tuple[str, Point]] = [("a", Point())]
d = dict(pairs)                   # WARNING: copies tuple[str, Point] elements
d = dict(copy(pairs))             # OK: explicit copy
src: dict[str, Point] = {"a": Point()}
dst: dict[str, Point] = {}
dst.update(src)           # WARNING: copies Point elements
dst |= src                # WARNING: copies Point elements
dst.update(copy(src))     # OK: explicit copy
dst.update(make_dict())   # OK: rvalue, no existing owner
comp: list[Point] = [p for p in items]              # WARNING: copies Point into owned storage
comp = [copy(p) for p in items]                     # OK: explicit copy
comp = [Point() for _ in range(3)]                  # OK: rvalue element, no existing owner
dc: dict[str, Point] = {k: v for k, v in src.items()}  # WARNING: copies Point (value) into owned storage
```

The same per-element check covers list/set/dict comprehensions: a reference-type
(or value-tuple-with-reference-member) lvalue element stored into the owned
result container copies it where CPython would alias, so it warns identically to
the literal/`append` sinks; a `@nocopy` element is a clean compile error.

The check is recursive -- reference types nested inside tuples or other containers are detected:

```python
nested: list[tuple[str, tuple[str, Point]]] = []
d = dict(nested)   # WARNING: copies tuple[str, tuple[str, Point]] elements
```

For generic type parameters the body states the contract at its own line,
hedged, whatever the instantiations are (see "the copy contract of a generic
body" above):

```python
def build_dict[K, V](pairs: list[tuple[K, V]]) -> None:
    d = dict(pairs)  # WARNING: may copy tuple[K, V] elements if not a value type
```

The payload is checked recursively, so `tuple[str, V]` hedges on account of
`V` even though a tuple is itself a value type.

No warning is emitted for:
- **Value types** (int32, bool, str, etc.) -- copy-vs-share is unobservable
- **`T: ValueType` bounded type params** -- the bound guarantees value semantics
- **Rvalues that own their result** (constructor calls, `Own[T]`-returning
  functions, factories) -- no existing owner. A call that returns a BORROW
  (`-> T` on a reference type) does warn: the reference it hands back
  aliases the callee's storage, so the owning slot copies
- **`copy()` wrapped** -- intent already explicit
- **`copy_iter()` wrapped** -- intent already explicit (element-by-element copy)
- **Last use** -- source is dead after this point, no observable aliasing divergence

#### `copy_iter()` for Bulk Operations (Working)

`copy_iter(iterable)` explicitly acknowledges element-by-element copies for bulk
operations like `extend()`, `list()`, `set()`. It wraps a borrowing iterator in
a `CopyIter` adapter that copies each element directly into the destination:

```python
from tpy import copy_iter

a: list[Node] = []
b: list[Node] = [Node(1), Node(2)]

a.extend(b)              # WARNING: copies Node elements
a.extend(copy(b))        # OK: explicit copy of container
a.extend(copy_iter(b))   # OK: explicit element-by-element copy (no intermediate container)
```

`copy_iter(b)` is more efficient than `copy(b)` for `extend` -- it copies each
element directly into the destination without creating an intermediate container
copy. The source `b` remains valid after the call.

`copy_iter` borrows from its source -- mutating the source container while a
`CopyIter` is live would invalidate the iterator (the borrow tracker warns).

#### Scope Escape Detection (Working)

A local declared in an inner scope (a loop body) owns storage that dies with the iteration. Binding it to a name that outlives that scope hoists the source's storage to function scope, so the outer name never dangles — but both names then share **one** slot, so the outer name observes whatever the source is rebound to later. That diverges from CPython, so the compiler warns and names the two spellings that avoid it:

```python
def example() -> None:
    saved: Point = Point(0, 0)
    for i in range(10):
        p: Point = Point(i, i)
        saved = p            # WARNING: 'saved' will not keep the object it was given
        saved = copy(p)      # OK: an independent value
        saved = Point(0, 0)  # OK: an rvalue has fresh storage
```

The warning states the consequence, not the mechanism, because the mechanism does not tell you what value you will get:

```
'saved' will not keep the object it was given -- 'p' is rebound on each
iteration and both names share its storage; use copy(p) for an independent
value
```

`copy()` costs a copy and drops the aliasing: a later write through one name is invisible through the other. For a `@nocopy` source it is not available at all, and the warning says so rather than naming a remedy.

If the two names genuinely need to reach one object, that is a design change rather than a one-word fix — `Rc` gives shared ownership with CPython's aliasing, at a heap block per `Rc.new`. The compiler does not suggest it: a warning should not push allocation into a loop.

**This is an acknowledged divergence, not a fixed bug.** Code that ignores the warning compiles and produces a value CPython would not; the specific shapes are tracked in `BUGS.md`. Closing it properly needs per-iteration storage, which cannot be supplied without allocating, copying, or keeping a second slot alive behind the author's back — see `TODO.md` for two mechanisms that were prototyped and discarded.

The warning covers `for` and `while` bodies, nested loops, module scope, method bodies, and sources reached through a field or container element (`saved = o.inner` keeps `o`'s storage alive, so it is `o` that escapes). It is *not* exhaustive: the check runs at plain-name assignment, so an escape routed through a walrus binding or a `nonlocal` rebind inside a nested def is not seen — those diverge with no diagnostic at all, and are tracked in `BUGS.md`.

##### Rebinding under an alias: the alias-gated slot model

Rebinding a reference-typed local to a fresh object writes to one of two places, decided per rebind statement by sema's alias-rebind pass (`tpyc/sema/alias_rebind.py`):

- **In place**, when no other name holds the object the local currently refers to: the new object is constructed into the same storage and the superseded one is destroyed there, which is when CPython's refcount drops it.
- **Storage of its own**, when some name still holds a loan of the current object -- a whole-name alias, a field or element loan, a `Ptr` (copied or not), a borrow-returning call result, a generator or coroutine that captured it -- or when the pass cannot tell (a pointer that left through a call argument, a `nonlocal` name, a parameter). The old object lives on, so the alias keeps it, as under CPython. **This is a declared divergence in `__del__` timing:** the object's `__del__` runs at scope end (inside a loop, when the site's slot is reused on the next iteration) rather than when CPython's last reference to it dies; the value is never affected, and `del` on the alias once it is done drops the object there.

```python
def gen() -> Iterator[int32]:
    p = Point(1)
    alias = p
    p = Point(100)   # alias still holds Point(1): the rebind takes storage of its own
    alias.bump()
    yield alias.x    # 101 under TPy and CPython

def drop() -> None:
    r = Noisy("a")
    r = Noisy("b")   # nothing else holds "a": written in place, "drop a" prints here
```

A holder counts while it is bound, read again or not, because that is what keeps the object alive under CPython. The decision is flow-sensitive and loops iterate to a fixpoint, so a loan taken after the rebind on the previous iteration, or a borrow arriving over the back edge, is seen. The same rule applies in every position: sync functions and methods, generator and async frames (a local with an own-storage rebind becomes a pointer over per-site frame fields), nested defs, module level, `@error_return` bodies.

Borrowed tuple elements also count as holders. After `saved = (p,)`, a later
replacement of `p` preserves the object captured by `saved` if that element was
captured by reference. Tuple copies retain those references; scalar elements
are snapshots and last-use owning captures do not create a loan. The same
tracking applies to supported walrus bindings and tuple-returning calls.
Writes to ordinary numeric/character fields preserve storage and do not warn about
borrow invalidation; property setters can still invalidate storage.

One clobber remains, and it warns: a loan taken from a rebind site's own storage that is still read after that site runs again on the next iteration -- one slot per site cannot hold two iterations' objects:

```python
def loop_carried() -> None:
    p = Point(0)
    q = take_ptr(p)
    for i in range(2):
        p = Point(i)      # WARNING: 'q' will not keep the object it was given
        print(q.x)        # iteration 2: TPy 1, CPython 0
        q = take_ptr(p)
```

```
'q' will not keep the object it was given -- 'p' is rebound here and both
names share its storage; bind the new value to a name of its own
```

The warning keys on the site alone: a loop body's *first* bind of the name reuses its slot on the next iteration the same way and warns the same way, and so does a rebind some path reaches with storage the name does not own (a list element, a parameter's object) -- that site stays out of the in-place family, but its own slot is still the one being reused.

Three spellings avoid it, and none of them warn: `alias = copy(p)` (an independent object), `Rc.new` plus `.clone()` (identity and refcount preserved, at a heap block), or binding the new value to a name of its own (allocation-free). A rebind that only reseats a pointer -- `p = other`, `p = xs[1]`, `p = None` -- clobbers nothing and never warns, and neither does a value-typed local, whose rebind copies. The value stays wrong if the warning is ignored. Two shapes keep the single-storage clobber and are tracked in `BUGS.md`: a `nonlocal` rebind inside a nested def, and a rebound owning tuple local.

For `for-each` variables hoisting does not help — the variable is a reference into the container, so the reference itself would dangle. Those stay **hard errors**:

```python
def bad() -> None:
    saved: Point = Point(0, 0)
    for p in make_points():
        saved = p        # ERROR: reference to 'p' may outlive its storage
        saved = copy(p)  # OK: copy() creates independent storage
```

Detection uses scope depth comparison — each scope has a numeric depth, and variables track the depth where they were first declared; assigning an lvalue to a shallower-depth target is the escape. Assigning a loop-body local into a *field* or a *container* is a different mechanism entirely — those boundaries copy, with the pre-existing "copies X into ..." warning.

#### Definite-Assignment Analysis (Working)

The compiler verifies that every local variable is definitely assigned before use — preventing undefined behavior from uninitialized C++ variables.

```python
def example(cond: bool) -> None:
    if cond:
        x: Point = Point(1, 2)
    print(x)  # ERROR: variable 'x' may be used before assignment

    y: int32          # bare annotation, no init
    print(y)          # ERROR: variable 'y' may be used before assignment

    z: int32
    for i in range(n):
        z = i         # assignment inside loop doesn't count (loop may not execute)
    print(z)          # ERROR
```

**Rules:**
- Function parameters and `self` are assigned at entry
- `x = expr` marks `x` as assigned
- `x: int32` (bare annotation) does NOT mark as assigned
- **If/else merge**: intersection of both branches (a variable is assigned after `if/else` only if assigned in *both* branches)
- **Terminated branch**: if one branch returns/breaks/continues, the other branch's state is used
- **Loops**: a name first bound in a `for` or `while` clause -- body or `else` -- is visible after the loop (the loop-clause local hoist below), but **counts as assigned after the loop only when that clause provably ran and the binding is on every edge that leaves the clause: the fall-through into `else`, each `continue`, each `break`** -- otherwise the read after the loop is rejected as possibly unassigned (see "Loop clause locals, the zero-trip loop and `break`" below). A name bound before the loop and only reassigned inside keeps its pre-loop state
- **Top-level code**: skipped (globals have separate initialization semantics)

#### Owned Parameters (Working)

`Own[T]` can also be used for parameter types to receive values by-value:

```python
def take_point(p: Own[Point]) -> int32:
    # p is received by value (Point p in C++)
    return p.x + p.y  # Field access on Own[T] works

def main():
    # Pass Own[Point] return directly to Own[Point] param
    result: int32 = take_point(create_point(10, 20))
    print(result)
```

**Lending a fresh `Own[T]` rvalue to a borrow param**: passing an `Own[T]` return value (or any fresh `Own[...]` rvalue) directly to a function expecting `T` (by reference) is allowed -- the compiler materializes a named temp that outlives the call:

```python
def process(p: Point) -> None:  # p is Point& (reference)
    print(p.x)

def main():
    process(create_point(10, 20))  # OK: temp materialized, lent for the call, dropped after
```

The temp is an lvalue, so it binds to both `T&` and `const T&` params. This is create-lend-drop: the value is owned only for the duration of the call. (Ownership *transfer* still requires an `Own[T]` param, which moves instead of lending.)

**Note**: `Own[T]` selects an *owned* C++ shape, which only matters where the default is a borrow. It is rejected anywhere in a **local variable or field annotation** -- including nested (`tuple[..., Own[T]]`, `Own[dict[...]]` fields) and `Optional[Own[T]]` -- because a local or field owns its value inline regardless, so the `Own` is redundant; use the plain type (`T`, `tuple[..., T]`, `T | None`). A local `T | None` (non-value `T`) keeps the borrow form (`T*` + slot, so aliasing matches CPython) and moves its value out at its last use into owned sinks -- no `Own` needed. `Own[T]` stays valid in parameter and return types (the ownership-transfer boundaries), including per-element in tuples (`tuple[Own[T], ...]`).

#### Auto-Move at Last Use (Working)

When a local variable or `Own[T]` parameter is passed to an `Own[T]` parameter and it is the **last use** of that variable (not read again on any subsequent execution path), the compiler automatically emits `std::move()` instead of requiring `copy()`:

```python
def consume(p: Own[Point]) -> int32:
    return p.x + p.y

def main():
    p = Point()
    p.x = 10
    p.y = 32
    # p is at its last use -- auto-moved, no copy() needed
    result = consume(p)
    print(result)
```

Generated C++: `consume(std::move(p))`

Auto-move applies to:
- **Tier 1 locals** (rvalue-initialized, not reassigned)
- **`Own[T]` parameters** (caller gave up ownership)
- **Reassigned locals** when every assignment is a value-creating rvalue (emits `std::move((*p))` through the pointer)
- **`await`-bound locals** (`p = await make()`) at every sink -- call arg, container insert, field write, return. The binding lives in the coroutine frame, so it is a frame FIELD rather than a C++ local, but it is still an ordinary owned local to last-use analysis; the "field accesses" exclusion below is about reading `self.x`, not about where a local happens to be stored. A bind inside a loop body still moves: it is consumed within the same iteration and re-assigned on the next, which is not "used across loop iterations". This previously copied -- a silent extra copy for reference types, and an uncompilable one for a `@nocopy` payload.

Auto-move does NOT apply to:
- Regular parameters (borrowed by reference)
- T& reference locals (lvalue-initialized aliases)
- Reassigned locals with any borrow-source assignment -- not only a name/field/subscript lvalue, but also a reference-returning call or a ternary of reference lvalues (the source aliases existing storage, so a move would steal from it)
- Field accesses (`self.x`)
- Top-level (module scope) non-value-type variables
- Variables used across loop iterations
- Variables with a live borrower: a name/field/subscript alias still used later (`a = o.inner; take(o); a.read()`), a live result of a borrowing call (`n = first(xs)`), a live un-exhausted generator over the variable (`g = gen(xs)`), a by-reference nested-def capture used after the def, or a context manager consumed inside its own `with` body (`__exit__` still reads it). These consumes fall back to a copy with the `copies ... into owned storage` warning (`@nocopy` types error instead). A borrower that is itself dead before the consume does not suppress the move for name/field/subscript aliases.

The analysis is conservative: if unsure whether a variable is at its last use (e.g., used inside a loop body that may iterate multiple times), the compiler does NOT auto-move and requires explicit `copy()` as before. Last-use analysis covers `with` and `try`/`except`/`finally` bodies; a value read on an exception path stays live across the whole try body (never auto-moved early). One refinement: `return <name>` of a reference type under a non-suspending finally stays an auto-move regardless of how (or whether) the finally touches the name -- codegen defers the materialization until after the finally chain, so the finally observes the live value and the move happens last (see the exceptions section). A forward-referenced callee (defined below its caller) whose signature could return a borrow is treated conservatively: the consume copies with the warning; value-returning forward references keep the silent move. Known borrow-suppression residuals (cross-module import cycles, borrows hidden inside nested call arguments such as `asyncio.create_task(coro(xs))`) are tracked in BUGS.md.

**Branch handling**: If a variable is used in both branches of an if/else and not used after, both branches get auto-move:

```python
if cond:
    consume(p)  # auto-move on this path
else:
    consume(p)  # auto-move on this path
```

**Flow-sensitive early-return/break**: When an if-branch terminates (via `return`, `break`, or `raise`), post-if code is unreachable on that path. The compiler treats the terminating branch as having an independent live set, enabling auto-move even when the variable is used after the if on the non-terminating path:

```python
def test(cond: bool) -> Own[Handle]:
    h = Handle(1)
    if cond:
        return h   # auto-move: post-if code unreachable here
    print(h.fd)    # h is live here only on the non-return path
    return h       # auto-move: last use
```

Similarly, `break` inside an if-branch within a loop is treated as terminating:

```python
for i in range(n):
    if i == target:
        result = consume(p)  # auto-move: break exits loop
        break
```

**Return site auto-move**: When a function returns `Own[T]` and the return value is a local variable at its last use, `copy()` is not needed -- the compiler allows it directly (C++ NRVO/implicit move handles the rest):

```python
def make_point(x: int32, y: int32) -> Own[Point]:
    p = Point()
    p.x = x
    p.y = y
    return p  # last use -> allowed without copy()
```

**Unnecessary copy() warning**: When `copy(x)` is used but `x` is at its last use and would be auto-moved, the compiler warns:

```python
return copy(p)      # warning: unnecessary copy()
consume(copy(b))    # warning: unnecessary copy()
```

**Own[T] param forwarding**: An `Own[T]` parameter can be forwarded to another `Own[T]` parameter at its last use:

```python
def forward(p: Own[Point]) -> int32:
    return consume(p)  # auto-move of Own param
```

**Generic Own[T] parameters**: When `Own[T]` wraps a type parameter `T`, the compiler generates `std::type_identity_t<T>&&` in C++ -- this prevents forwarding-reference deduction while still using rvalue reference semantics for zero-cost ownership transfer:

```python
class Container[T]:
    items: list[T]
    def push(self, item: Own[T]) -> None:
        self.items.append(item)
```

Generated C++: `void push(std::type_identity_t<T>&& item) { this->items.push_back(std::move(item)); }`

At call sites, the compiler inserts `std::move()` at last use. For non-last-use, a copy is made (with a sema warning) and moved into the parameter.

**Own[T] param consumption warning (Working)**: When an `Own[T]` param is never consumed -- not stored in a field, forwarded to another `Own[T]` param, or returned as `Own[T]` -- the compiler warns:

```python
def borrow_only(b: Own[Box]) -> int32:
    return b.value  # warning: Own[Box] param 'b' is never consumed
```

This warning is suppressed for value types (`Own[int32]` -- copy equals move), `@nocopy` types (Own is the only way to pass them), and generic `T` bounded to `ValueType`.

#### @nocopy Types (Working)

Types decorated with `@nocopy` have their copy constructor and copy assignment deleted
in C++. Values can only be moved (via auto-move at last use), never copied:

```python
from tpy import int32, Own, nocopy

@nocopy
class Handle:
    fd: int32

def close(h: Own[Handle]) -> int32:
    return h.fd

def main():
    h = Handle()
    h.fd = 42
    print(close(h))    # last use -> auto-move (std::move)
```

**Key rules for @nocopy types:**
- `copy(h)` is a compile error -- copying is not available
- Passing to `Own[T]` parameter works at last use (auto-moved)
- Passing to `Own[T]` when NOT at last use is a compile error with a clear message
- Non-consuming uses (field access, method calls, pass by reference) work normally
- `alias = h` at `h`'s last use performs move-through: `alias` becomes the owner
  via `std::move(h)`, enabling return or consumption through `alias`
- `alias = h` when `h` is used later creates a `T&` reference (borrow); aliases
  created this way cannot be consumed
- Returning a @nocopy local at last use works (C++ NRVO/implicit move)
- Auto-move is suppressed when T& aliases of the source variable are still live
  (prevents dangling references through aliases)
- Detach-on-reassign: when the source is reassigned (`alias = h; h = new()`),
  alias points to old storage and no longer constrains moves of the new h
- `Own[T]` parameter forwarding works at last use

**Nocopy propagation:** Classes that contain non-copyable fields automatically become
non-copyable themselves, without needing an explicit `@nocopy` decorator. This works
transitively and with generic types:

```python
@nocopy
class Handle:
    fd: int32

class Container:       # implicitly nocopy (field 'handle' is @nocopy)
    handle: Handle

class Outer:           # implicitly nocopy (field 'c' contains nocopy Handle)
    c: Container

class Handles:         # implicitly nocopy (list[Handle] requires copyable T)
    items: list[Handle]
```

Propagation also applies to:
- Builtin nocopy types (`UninitHeapStorage`, `UninitArrayStorage`)
- `Optional[NocopyType]`, `Own[NocopyType]` fields
- Inherited parent types (if parent is nocopy, child is too)
- Generic type arguments (`list[T]`, `Array[T, N]`, etc.)

Error messages for implicitly-nocopy types explain the reason:
```
error: Cannot copy non-copyable type 'Container' (field 'handle' has
non-copyable type 'Handle'). Non-copyable values can only be moved
(pass directly at last use).
```

Generated C++ for @nocopy records includes:
```cpp
struct Handle {
  int32_t fd;
  Handle() = default;
  Handle(const Handle&) = delete;
  Handle& operator=(const Handle&) = delete;
  Handle(Handle&&) = default;
  Handle& operator=(Handle&&) = default;
};
```

**`__del__` default ctor suppression:** When a record has `__del__`, the auto `ClassName() = default;` is suppressed in any of these cases:
  - `@nocopy + __del__` (the author's "no safe default" declaration: `Box`, `Rc`, `Weak`).
  - `__del__ + __init__` with any required parameter -- the user explicitly opted out of a zero-arg ctor.
  - `__del__ + no __init__ + any own field that's indeterminate after C++ value-initialization` (raw `Ptr[T]`, primitive scalars like `int32`/`Bool`/`char`/floats without an in-class initializer). Such fields would be left in an indeterminate state by `T() = default;`, and the destructor would read them.

Empty-fields `__del__`-only records (the abstract-Base pattern: `class Base: def __del__(self): ...`) **do** keep their auto default ctor, so derived classes can value-init the base subobject via their member initializer list.

Without the default ctor, such types can only be constructed via a parameterized ctor, and enclosing records that hold them as fields must MIL-initialize (the field appears in the member initializer list), not default-init-then-assign in the body. The suppression cascades: a record holding a field of a `__del__`-suppressed type automatically loses its own auto `= default;` too. Field initializers whose RHS references a body-local variable cannot MIL-hoist and are rejected with a clean sema error; the recommended shape is a `@staticmethod` factory returning `Own[Self]` that bundles any multi-step or error-checked allocation. See `lib/tpy/re.py` (`_OwnedCode.make_compiled`, `_OwnedMatchContext.make_default`) for the canonical pattern.

**Generator locals rebound under an alias:** a generator or `async def` body takes the same alias-gated slot model as a sync body (see "Rebinding under an alias" above): a reference local that some rebind must keep alive for a loan is stored as a pointer over per-site frame fields, every other local keeps its owning frame slot and rebinds in place. A `nonlocal` rebind of an enclosing function's rebound local inside a nested def is rejected (the closure cannot give the new object storage that outlives the call); a `nonlocal` rebind of a plain enclosing local writes it in place -- see BUGS.md.

**`__del__` timing (drop at scope end, matching CPython; the two declared exceptions are below):** A `@nocopy` local's `__del__` runs when the enclosing block exits (C++ RAII at the closing `}`), which mirrors CPython, where a local lives until its frame tears down. Passing a value to a function does **not** drop it early: an `Own[T]` parameter is a `T&&` borrow, so the callee never destructs the argument (the `Optional[Own[T]]` spelling is the exception today: it is a by-value `std::optional<T>`, so a moved-in payload dies at the callee's return, `BUGS.md#opt-own-by-value-param-early-del`) -- a `dispose(x: Own[T])` helper that doesn't re-home `x` leaves the drop with the caller, firing at the caller's scope end. This too matches CPython (the local still holds the reference after the call returns). TPy does **not** adopt Rust-style drop-at-last-use; the timing follows Python's model. A local REBOUND from an rvalue (`r = T(a); r = T(b)`) drops the superseded value at the rebind when no other name holds it, as CPython does; when another name still holds it, the value lives in storage of its own until scope end -- a declared divergence: CPython drops it when the last reference dies (`docs/OWNERSHIP_DESIGN.md` covers the slot mechanics; `del` the alias to drop it earlier). A record local first declared inside a block lives in block-scoped C++ storage, so its last value drops at the block's closing brace where CPython drops it at function exit. For *prompt* cleanup, use an explicit spelling -- all three match CPython:
- `del x` -- drops `x` at that point (relocates into a scoped temp that dies immediately).
- `x.close()` (or any explicit disposal method) -- runs the cleanup synchronously, independent of drop timing.
- `with x as ...:` -- ties cleanup to block exit via `__enter__`/`__exit__`.

A value moved into an owning slot (a field, a container, `return x`, another `Own[T]` sink that stores it) relocates: its drop moves with the new owner and no longer fires at the original scope's end. See the `tplib.channel` `Sender` close semantics for a worked example (drop-based auto-close on the last sender, plus explicit `close()` for prompt closing).

**Exceptions escaping `__del__` (fail-fast):** A destructor is a `noexcept` boundary, so any exception that escapes it is **fatal** -- TPy treats a throwing `__del__` as a bug and aborts (`TurboPython panic: uncaught exception in __del__: <type>: <message>` on stderr, stdout flushed first), matching C++'s own noexcept-destructor rule and Rust's abort-on-panic-in-`Drop` -- **not** CPython's print-and-continue. This is a deliberate, declared divergence from CPython (which catches, prints `Exception ignored in:`, and keeps running). Mechanics: a literal `throw` -- which codegen emits for a `with`/`try`/`raise` in the body -- is a `-Werror=terminate` compile error in a `noexcept` function, so a body containing any of those constructs is wrapped (`try { <body> } catch (...) { report-and-abort; }`); a body with none stays wrap-free and a throwing *call* there aborts at the noexcept boundary just the same -- so **every** escape aborts, consistently. This still unblocks locked cleanup (e.g. a channel handle taking a `Mutex` to flip a flag + notify): the normal path runs the cleanup and returns; only an actual escaping exception aborts. A `raise` statement directly in `__del__` (not lexically under a `try`, which may catch it) additionally gets a **compile warning** pointing you to move it out; wrap fallible cleanup in `try`/`except` inside `__del__` if you want to handle it instead of aborting.

**`__copy__` escape hatch:** A class that would be implicitly nocopy (due to nocopy fields)
can define `__copy__` to remain copyable. The method takes no parameters (besides self) and
returns `Own[ClassName]`. The compiler generates a C++ copy constructor that delegates to
`__copy__()`:

```python
@nocopy
class Handle:
    fd: int32
    def __init__(self, fd: int32):
        self.fd = fd

class Container:      # would be implicitly nocopy, but __copy__ opts out
    handle: Handle
    def __init__(self, handle: Own[Handle]):
        self.handle = handle
    def __copy__(self) -> Own[Container]:
        return Container(Handle(self.handle.fd))

c = Container(Handle(1))
c2 = copy(c)          # works -- calls __copy__ under the hood
```

Rules:
- `__copy__` is implicitly `@readonly` (reads self to produce a copy)
- `@nocopy` classes cannot define `__copy__` (contradictory -- use `@nocopy` to forbid copies)
- Return type must be `Own[ClassName]` (or bare `ClassName`)
- No parameters besides `self`

**`__move__` escape hatch (Working):** A class that owns a non-movable member --
notably `UninitArrayStorage[T, N]`, which is `@nomove` (it has no liveness
information to relocate its elements on its own) -- can define `__move__` to
supply a relocating move constructor in pure TPy. The method takes the move
source as an `Own[Self]` parameter and returns `None`; codegen value-initializes
the destination's members (empty), inlines the `__move__` body to relocate the
live elements into `self`, then marks the source moved-from so its `__del__` is
skipped. It is consumed into the C++ move constructor (never emitted as a
callable method, like `__del__`), so moves stay implicit at every use site
(`xs = make()`, return-by-value, reassignment) -- only the library author writes
`__move__`, once. The batch primitive `UninitArrayStorage.relocate_from(other,
count)` does the relocation in one call (a `memcpy` for a trivially-relocatable
element, element-wise move otherwise):

```python
class Pool[T, N: int]:
    _storage: UninitArrayStorage[T, N]
    _size: uint32
    def __del__(self) -> None:
        self._storage.drop_n(uint32(0), self._size)
    def __move__(self, other: Own[Pool[T, N]]) -> None:
        self._storage.relocate_from(other._storage, other._size)
        self._size = other._size
```

Rules / notes:
- `__move__` takes exactly one `Own[Self]` parameter (the move source) and
  returns `None`; the source is consumed, so `__move__` must leave it
  destructible (`relocate_from` / `take` empty the source slots). The debug
  `alive_` bitset in `UninitArrayStorage` catches a `__move__` that leaves live
  slots.
- `__move__` requires the class to also define `__del__` (the relocating move
  pairs with custom destruction, and reuses its moved-from drop flag); it is a
  compile error otherwise. A class is movable member-wise unless it transitively
  owns a non-movable member (see the movability trait below); such an owner is
  non-movable until it defines `__move__`.
- `__move__` must be nothrow: its body is inlined into a `noexcept` move
  constructor, so a `raise` that reaches it terminates the program. An
  un-`try`-guarded `raise` in the body is a compile error. Indirect throws (a
  callee that raises) are not yet caught -- TPy has no nothrow tracking -- so a
  throwing call still terminates at runtime.
- `tplib.ArrayList[T, N]` uses this so it is movable for any element type
  (including a record with a `str` field). The relocation is O(N), inherent to
  inline storage; the heap-backed builtin `list` moves in O(1).
**Movability trait + `@nomove` (Working):** movability is a propagated trait
(sibling to Send/Sync): a type is movable unless it transitively owns a
non-movable member without supplying `__move__`. `@nomove` marks a type
non-movable regardless of its fields; it is library-declared (the compiler has
no built-in knowledge of which types are non-movable). The raw inline storage
`tpy.mem.UninitArrayStorage` is declared `@nomove` in its stub because it tracks
no liveness and so cannot relocate its elements on its own. Relocating a
non-movable value (returning a named local by value, etc.) is a **clean
compile-time error naming the offending field chain** rather than a raw C++
"use of deleted function" -- e.g. "`Pool` is not movable: field
`_storage: UninitArrayStorage[Item, 8]` is not movable (marked @nomove)";
define `__move__` to relocate the live elements, or `copy()` if copyable. An
owner of `UninitArrayStorage` is therefore non-movable for *any* element
(including a trivially-copyable one) unless it defines `__move__` -- there is no
element-conditional free move. Movability is re-evaluated per generic
instantiation (a wrapper is movable exactly when its substituted members are).
A *freshly constructed* prvalue return (`return Pool()`) stays legal (no move
occurs -- guaranteed elision). Currently enforced at by-value returns and
`Own[T]` argument passing; relocation at last-use rebind, container insert, and
comprehension elements is not yet checked in sema (it still surfaces as the C++
deleted-move error) -- see `TODO.md`. (`tplib.ArrayList`/`FixStr` define
`__move__`; trivial fixed scratch buffers use the movable `Array[T, N]` instead
of `UninitArrayStorage`.)

#### Unsafe Memory Operations -- `tpy.unsafe` (Working)

The `tpy.unsafe` module provides low-level pointer operations that bypass the compiler's safety checks. These functions require an explicit import -- `from tpy import *` does NOT include them. This forces a deliberate opt-in for unsafe code.

```python
from tpy import Ptr, int32, uint32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store
```

Import styles supported:
- `from tpy.unsafe import unsafe_ptr, unsafe_load` -- import specific functions
- `import tpy.unsafe` -- use as `tpy.unsafe.unsafe_ptr(...)`
- `import tpy.unsafe as m` -- use as `m.unsafe_ptr(...)`

**`unsafe_ptr`** -- get a raw pointer to the underlying data of a container or string:

```python
arr: Array[int32, 4] = [int32(1), int32(2), int32(3), int32(4)]
p: Ptr[int32] = unsafe_ptr(arr)          # Array[T, N] -> Ptr[T]

lst: list[int32] = [int32(10), int32(20)]
q: Ptr[int32] = unsafe_ptr(lst)          # list[T] -> Ptr[T]

s: str = "hello"
cp: Ptr[readonly[char]] = unsafe_ptr(s)     # str -> Ptr[readonly[char]]
```

The element type `T` is inferred from the argument. All three variants generate `.data()` in C++.

**`unsafe_load`** -- read through a pointer at an offset (no bounds checking):

```python
val: int32 = unsafe_load(p, uint32(0))   # Ptr[T], uint32 -> T
val2: int32 = unsafe_load(cp, uint32(1)) # Ptr[readonly[T]], uint32 -> T
```

Generates `p[offset]` in C++.

**`unsafe_store`** -- write through a pointer at an offset (no bounds checking):

```python
unsafe_store(p, uint32(0), int32(99))    # Ptr[T], uint32, Own[T] -> None
```

Generates `p[offset] = value` in C++. Only `Ptr[T]` is accepted (not `Ptr[readonly[T]]`).

**`unsafe_copy_n`** -- copy N elements from a source pointer to a destination pointer:

```python
from tpy.unsafe import unsafe_copy_n

src: Array[int32, 3] = [int32(10), int32(20), int32(30)]
dst: Array[int32, 3] = [int32(0), int32(0), int32(0)]
unsafe_copy_n(unsafe_ptr(dst), unsafe_ptr(src), uint32(3))  # Ptr[T], Ptr[T]|Ptr[readonly[T]], uint32 -> None
```

Generates `std::copy_n(src, count, dest)` in C++. The source can be either `Ptr[T]` or `Ptr[readonly[T]]`.

**`unsafe_ptr_add`** -- advance a pointer by a signed element offset:

```python
from tpy.unsafe import unsafe_ptr_add

p: Ptr[int32] = unsafe_ptr(arr)
q: Ptr[int32] = unsafe_ptr_add(p, int64(3))   # Ptr[T], int64 -> Ptr[T]
```

Generates `(p + 3)` in C++. The offset is in elements (not bytes). Negative offsets move the pointer backward. Works with both `Ptr[T]` and `Ptr[readonly[T]]`.

**`unsafe_ptr_diff`** -- compute the element distance between two pointers:

```python
from tpy.unsafe import unsafe_ptr_diff

d: int64 = unsafe_ptr_diff(p2, p1)   # Ptr[T], Ptr[T] -> int64
```

Generates `static_cast<int64_t>(p2 - p1)` in C++. Returns the number of elements between the two pointers (negative if `p2` precedes `p1`). Both pointers must point into the same allocation. Works with both `Ptr[T]` and `Ptr[readonly[T]]`.

**`unsafe_const_cast`** -- remove const from a pointer:

```python
from tpy.unsafe import unsafe_const_cast

cp: Ptr[readonly[int32]] = ...
p: Ptr[int32] = unsafe_const_cast(cp)    # Ptr[readonly[T]] -> Ptr[T]
```

Generates `const_cast<T*>(p)` in C++.

**`unsafe_cast`** -- reinterpret a pointer as pointing to a different type:

```python
from tpy.unsafe import unsafe_cast

p: Ptr[int32] = ...
q: Ptr[uint32] = unsafe_cast[uint32](p)  # explicit type arg (preferred)
q: Ptr[uint32] = unsafe_cast(p)          # target inferred from annotation
print(unsafe_load(unsafe_cast[uint32](p), uint32(0)))  # works inline too
```

Generates `reinterpret_cast<T*>(p)` in C++. `unsafe_cast` is a standard two-type-param generic (`T` = target pointee, `U` = source pointee). The target type can be specified via explicit type argument (`unsafe_cast[uint32](p)`, partial -- `U` inferred from arg) or inferred from context (`q: Ptr[uint32] = unsafe_cast(p)` -- both `T` and `U` inferred). The pointer kind (`Ptr`/`Ptr[readonly[...]]`) is preserved: `Ptr[U]` returns `Ptr[T]`, `Ptr[readonly[U]]` returns `Ptr[readonly[T]]`. Casting `Ptr[readonly[T]]` to `Ptr[T]` is rejected -- use `unsafe_const_cast` first.

**`unsafe_str_view`** -- create a `StrView` from a char pointer and length:

```python
from tpy.unsafe import unsafe_str_view

p: Ptr[char] = ...
sv: StrView = unsafe_str_view(p, uint32(5))   # Ptr[char], uint32 -> StrView
```

Generates `std::string_view(p, size)` in C++. Also accepts `ConstPtr[char]`. The caller must ensure the pointer remains valid for the lifetime of the returned view.

**`unsafe_str_from_cstr`** / **`unsafe_cstr`** -- convert between a TPy string and a null-terminated C string:

```python
from tpy.unsafe import unsafe_cstr, unsafe_str_from_cstr

def handle(p: Ptr[readonly[uint8]]) -> None:
    name = unsafe_str_from_cstr(p)      # const char* -> owned str (copies)
    reply = String("hi " + name)
    c_sink(unsafe_cstr(reply))          # String -> const uint8_t*, borrowed
```

`unsafe_str_from_cstr` copies up to the first `\0`, so the result is independent of the source buffer. `unsafe_cstr` takes `String` rather than `str` because only the owned form is null-terminated -- `unsafe_ptr` on a `str` yields a view pointer with no terminator. Its result borrows the argument's buffer and is untracked, like every `unsafe_ptr` overload: passing a `str` or a literal converts, so the buffer dies with the enclosing expression. These are the supported way to move strings across a `binding="C"` boundary, where the string types themselves are rejected.

**`unsafe_alloc`** / **`unsafe_alloc_n`** -- allocate raw memory for one or N elements:

```python
from tpy.unsafe import unsafe_alloc, unsafe_alloc_n
p: Ptr[int32] = unsafe_alloc()        # allocate space for 1 element
q: Ptr[int32] = unsafe_alloc_n(uint32(10))  # allocate space for 10 elements
```

**`unsafe_free`** -- free raw memory allocated by `unsafe_alloc`/`unsafe_alloc_n`:

```python
from tpy.unsafe import unsafe_free
unsafe_free(p)  # Ptr[T] -> None
```

**`unsafe_init`** -- placement-construct an object at a pointer location:

```python
from tpy.unsafe import unsafe_init
unsafe_init(p, int32(42))  # Ptr[T], Own[T] -> None
```

**`unsafe_drop`** -- call the destructor on an object at a pointer location (no-op for trivially destructible types):

```python
from tpy.unsafe import unsafe_drop
unsafe_drop(p)  # Ptr[T] -> None
```

**`unsafe_move_out`** -- move a value out of a pointer location without calling the destructor. The pointed-to memory is left in a moved-from state:

```python
from tpy.unsafe import unsafe_move_out
val: int32 = unsafe_move_out(p)  # Ptr[T] -> Own[T]
```

Generates `std::move(*p)` in C++. Used by `Box[T].take()` to extract the contained value before freeing the raw memory.

#### Uninitialized Storage -- `tpy.mem` (Working)

The `tpy.mem` module provides low-level uninitialized storage types for building containers. Elements are not default-constructed -- the caller manages element lifetimes explicitly via `init`/`drop`. Lifetime tracking that panics on misuse (double-init, use-after-drop, leak on destruction) is compiled in unless `NDEBUG` is defined; neither `tpy` build variant defines it, so both the default `-O3` build and `--debug` keep the checks.

```python
from tpy import int32, Ptr
from tpy.mem import UninitArrayStorage, UninitHeapStorage
```

**UninitArrayStorage[T, N]** -- inline (stack) storage for N elements. Uses a C++ union so elements are not default-constructed. The owner tracks which slots are live (a size/count/flag), so the storage carries no runtime liveness and cannot relocate its elements on its own -- it is therefore `@nomove` (non-movable for any element type). An owner that needs to be moved defines `__move__` and relocates the live prefix with `relocate_from(other, count)` (a `memcpy` for a trivially-relocatable element, element-wise move + destroy otherwise; the source is left empty). A movable single-value owner uses `UninitStorage[T]` instead; a trivial fixed scratch buffer uses the movable `Array[T, N]`.

```python
storage = UninitArrayStorage[int32, 4]()
storage.init(0, 10)        # placement-new at index 0
storage.init(1, 20)
print(storage.load(0))     # access element -> 10
storage.drop(0)            # destroy element at index 0
```

**UninitStorage[T]** -- owning storage for a single optional value, tracking its own liveness in one bit. Unlike `UninitArrayStorage`, it MOVES CORRECTLY element-wise (transferring the live payload and leaving the source empty), so it is safe for a non-trivially-relocatable payload (e.g. an SSO `std::string`). Use it for owners that are a single optional value -- `Poll`, the `Rc` cell payload, the asyncio `Task`/`Future` result slot, a channel send slot -- where its bit IS the owner's liveness (no separate flag). Surface: `construct(value)` (place a payload into an empty slot), `has()`, `get()`, `take()` (move the payload out and empty the slot), `reset()`, `ptr()`. `T` must be nothrow-move-constructible (a `static_assert` enforces it): every owner move-constructs the slot under a `noexcept` move ctor, so a throwing `T` move would `std::terminate` at the owner boundary -- a throwing-move `T` is rejected at compile time instead.

```python
slot = UninitStorage[int32]()
slot.construct(10)         # place the value
print(slot.has())          # -> True
print(slot.get())          # access -> 10
x = slot.take()            # move out, slot now empty
```

**UninitHeapStorage[T]** -- heap-allocated storage for a given capacity. Non-copyable, movable (move transfers pointer ownership). Always allocated -- constructor allocates, destructor deallocates.

```python
storage = UninitHeapStorage[int32](4)   # allocate capacity for 4 elements
storage.init(0, 100)
val: int32 = storage.take(0)            # move value out + destroy slot

# Type can be inferred from assignment target context:
s: UninitHeapStorage[int32] = UninitHeapStorage(1)  # T inferred as int32
```

**Shared API** (both types):

| Method | Description |
|--------|-------------|
| `init(index, value)` | Placement-construct element at index |
| `drop(index)` | Destroy element at index |
| `load(index)` | Access element by reference |
| `take(index)` | Move element out (load + drop in one operation) |
| `init0(value)` / `drop0()` / `load0()` / `take0()` | Shortcuts for index 0 (single-element usage) |
| `ptr()` | Raw `Ptr[T]` to underlying storage |

### User-Defined
- **Working**: Classes -> C++ structs
- **Working**: Auto-declare fields from `__init__`: `self.field = param` auto-declares a field using the parameter's type, no class-level annotation needed (CPython-compatible). Works for any class, including subclasses: a name already declared by an ancestor reuses the inherited slot (no shadow), a new name becomes an own field. An `Own[T]` param infers a `T` field (a field owns its value inline; the param is moved in). Only direct parameter assignments at `__init__` top level; computed expressions (`self.f = 0`, `self.f = g()`) still require an explicit class-level annotation. See `docs/CONSTRUCTOR_DESIGN.md`.
- **Working**: Two-section `__init__` model: the leading contiguous chain of `super().__init__()` calls and `self.field = expr` assignments (intervening docstring / `pass` also skipped) is the *init section* (maps to the C++ member initializer list); the first non-conforming statement closes the section and any `self.field = expr` after it stays in the *body section*, so its expression evaluates in source order with the preceding body statements. Fields not initialized in the init section are checked at the split point: no default constructor -> compile error; default-constructible -> warning; has class-level default -> silent. Assigning a `@nocopy` or `__del__` field inside control flow (`if`/`else`/`while`/`for`) in the body section is a compile error (move-only or destructor would run on the default-constructed value). See `docs/CONSTRUCTOR_DESIGN.md`.
- **Working**: Single class inheritance (`class Child(Parent)`). A subclass `__init__` must call `super().__init__(...)` as its first statement when the parent's C++ default constructor is implicitly deleted (e.g. when the parent has a `@nocopy`/`__del__` field, or any field whose type has no default ctor); otherwise the C++ build can't synthesize the base subobject init. Subclasses of parents whose fields are all default-constructible (`int32`, `str`, `list[T]`, etc.) may omit the super call -- C++ default-initializes the base subobject and the child can overwrite inherited fields by direct assignment.
- **Working**: Generic inheritance with forwarded type params (`class Child[T](Parent[T])`)
- **Working**: Explicit protocol implementation (`class MyList(Sequence[int32])`)
- **Working**: Enums -> `enum class` (base `Enum` with integer members, `auto()`, `.name`, `.value`, `==`/`!=`/`is`/`not`, truthiness, record field, `list[Enum]`, `Optional[Enum]`, cross-module import, iteration `for c in Color`, value lookup `Color(0)`, name lookup `Color["Red"]`, `try_parse(Color, "Red")` via `from tpy import try_parse`, `IntEnum` with arithmetic/ordering/int comparison, configurable underlying type via mixin `(int8, Enum)`). Both `from enum import Enum, auto` and `import enum` (qualified `enum.Enum`, `enum.auto()`) are supported. `.name` is typed `StrView` -- the value is a `string_view` into the enum's static member-name storage (safe to hold indefinitely), so view positions read it copy-free and owned-`str` sinks (an owned return, a `: str` annotated-and-mutated local, `list[str].append`, a `yield` at an `Iterator[str]` slot) insert the standard view->owned copy.
- **Working**: `@native` enums -> bind to existing C++ `enum class` via `@native("ns::E")`. Two value-declaration modes: `auto()` / `native_member("cpp_name")` leave the value implicit (C++ is the source of truth; `e.value` reads `static_cast<underlying>(e)`); explicit integer literals are verified against the C++ side via a per-member `static_assert` at compile time. `native_member("cpp_name")` also aliases a TPy-side member name to a C++-side enumerator (for Python keywords like `None` or naming-convention mismatches). No `enum class` declaration is generated and no `operator<<` is emitted (print/repr route through `EnumUtil` via runtime templates). See `docs/NATIVE_INTEROP.md#enums`.

### Protocols (Partial)

Protocols enable structural subtyping (compile-time duck typing). A type matches a protocol if it has the required methods, without explicit inheritance. This is Python's `typing.Protocol` (PEP 544), similar to Go interfaces or Rust traits.

#### Working: Built-in `Sized` Protocol

The built-in `Sized` protocol is available from the `typing` module:

```python
from typing import Sized
from tpy import int32

def count(items: Sized) -> int32:
    return len(items)

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    print(count(nums))  # Works! Prints 3
```

Generated C++:
```cpp
template<tpy::Sized T_items>
int32_t count(const T_items& items) {
    return tpy::__len__(items);
}
```

The `tpy::Sized` concept uses the `tpy::__len__()` free function, which has overloads for `std::vector`, `std::array`, `std::span`, and `std::string_view`, plus a default template for user types with `__len__()` method.

#### Working: Built-in `Truthy` Protocol

The built-in `Truthy` protocol is available from the `tpy` module. Types that implement `__bool__()` conform to `Truthy`, and `bool()` dispatches to `__bool__()` for user-defined types:

```python
from tpy import Truthy

class Container:
    count: int
    def __bool__(self) -> bool:
        return self.count != 0

def is_truthy(x: Truthy) -> bool:
    return bool(x)
```

Generated C++ uses `::tpy::__bool__()` free function dispatch, with a default template forwarding to user-defined `__bool__()` methods. The compiler generates a `Truthy` concept to constrain generic parameters.

Implicit truthiness is supported: `if obj:`, `while obj:`, `not obj`, `and`/`or` all call `__bool__()` automatically for types that define it. Built-in containers (`list`, `str`, `Array`, `Span`) use `__len__() != 0` for truthiness, matching Python semantics where empty containers are falsy. The same lowering applies in every boolean context, including a `match` case guard (`case p if obj:`) and a condition inside a generator or `async def` frame -- a value-repr `Optional[T]` there is truthy only when engaged *and* its payload is truthy, as in CPython. A type with no truthiness hook at all (a plain enum, a user record defining neither `__bool__` nor `__len__`) is always truthy, as in CPython -- but the operand is still evaluated, so `if make():` runs `make()` and only then takes the branch, and `if a.b.c:` still performs `a.b`'s null check. The generated condition reads `(static_cast<void>(<operand>), true)`: a constant value over an operand that keeps running.

#### Working: Built-in `Hashable` Protocol

The built-in `Hashable` protocol is available from the `tpy` module. Types that implement `__hash__() -> uint64` conform to `Hashable`. The `hash()` builtin dispatches to `tpy::__hash__()`:

```python
h = hash("hello")    # uint64
h = hash(42)          # uint64
h = hash(3.14)        # uint64
h = hash(True)        # uint64
```

All primitive types (str, int, fixed-width ints, float, bool, char), `bytes`, `BytesView`, and Enum types are hashable. `bytearray` is not hashable (mutable). Dict key / set element validation requires conformance to BOTH the `Hashable` and `Equatable` protocols (ordered_set / ordered_map use std::hash AND std::equal_to). Conformance walks the inheritance chain, so a subclass inherits its parent's dunders.

Generated C++ uses `tpy::__hash__()` free function dispatch with overloads for built-in types (`std::integral`, `double`, strings, bytes, `BigInt`, enums) and a default template forwarding to user-defined `__hash__()` methods.

#### Working: User-Defined Protocols

You can define your own protocols, but **prefer using existing CPython protocols** (like `Sized` from `typing`) when possible. This ensures compatibility with both CPython and TurboPython, and avoids duplicating standard definitions.

Example of a custom protocol:

```python
from typing import Protocol
from tpy import int32

class Measurable(Protocol):
    def __len__(self) -> int32: ...

def count(items: Measurable) -> int32:
    return len(items)
```

Generated C++:
```cpp
template<typename T>
concept Measurable = requires(const T& t) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
};

template<Measurable T_items>
int32_t count(T_items& items) {
    return tpy::__len__(items);
}
```

**Readonly concept generation**: When all methods in a user-defined protocol are readonly (either explicitly via `@readonly` or implicitly for dunders like `__len__`, `__getitem__`, etc.), the concept uses `const T&` instead of `T&`. This allows the concept to accept const references and is consistent with how builtin protocols like `Sized` and `Sequence` work.

**`@readonly` on protocol methods**: Protocol methods can be annotated with `@readonly` to require that implementing methods are also readonly. This is enforced during conformance checking -- if a protocol method is readonly, the record's method must also be readonly (explicitly or implicitly):

```python
from typing import Protocol
from tpy import int32, readonly

class Readable(Protocol):
    @readonly
    def read(self) -> int32: ...

class GoodReader:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value

    @readonly
    def read(self) -> int32:
        return self.value

class BadReader:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value

    def read(self) -> int32:  # Not readonly
        return self.value

def use(r: Readable) -> int32:
    return r.read()

use(GoodReader(1))  # OK
use(BadReader(1))   # ERROR: BadReader does not conform to Readable
```

Dunders in the implicit readonly set (`__bool__`, `__len__`, `__getitem__`, `__eq__`, arithmetic operators, etc.) are automatically treated as readonly in protocol signatures, matching the behavior for record methods. Use `@readonly(False)` to opt out.

**Method and constructor parameters**: Protocol types work in method and constructor parameters, not just free functions. The compiler generates template headers on the method/constructor:

```python
class Container:
    count: int
    def __init__(self, items: Sized) -> None:
        self.count = len(items)
    def update(self, items: Sized) -> None:
        self.count = len(items)
```

Generated C++ (constructor gets `const T&`, method gets `T&`):
```cpp
struct Container {
    tpy::BigInt count;
    template<tpy::Sized T_items>
    explicit Container(const T_items& items) : count(tpy::BigInt(tpy::__len__(items))) {}
    template<tpy::Sized T_items>
    void update(T_items& items) { this->count = tpy::BigInt(tpy::__len__(items)); }
};
```

**`Optional[Protocol]` parameters**: Parameters typed `Optional[Protocol]` (e.g., `items: Optional[Sized] = None`) use pointer repr (`const T*`) in C++ with a default template argument of `std::nullptr_t`. Narrowing (`if items is not None:`) uses `!= nullptr` and dereference (`(*items)`) works automatically. Protocol operations inside narrowing bodies are wrapped in `if constexpr (!std::same_as<T, std::nullptr_t>)` to prevent instantiation when the argument is omitted or `None`.

```python
class Container:
    count: int
    def __init__(self, items: Optional[Sized] = None) -> None:
        if items is not None:
            self.count = len(items)
        else:
            self.count = 0
```

All call patterns work: concrete values (`Container(nums)` -> `&nums`), explicit `None` (`Container(None)` -> typed nullptr), and omitted optional args (`Container()` -> delegates to template ctor with nullptr). This applies to constructors, methods, and free functions.

#### Working: Protocol Inheritance

Protocols can inherit from other protocols, creating combined protocols that require all methods from parent protocols:

```python
from typing import Protocol, Sized
from tpy import int32

class Printable(Protocol):
    def to_str(self) -> str: ...

# PrintableAndSized inherits from both Printable and Sized
class PrintableAndSized(Printable, Sized, Protocol):
    pass  # Inherits to_str() from Printable, __len__() from Sized

class Message:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text

    def to_str(self) -> str:
        return self.text

    def __len__(self) -> int32:
        return int32(5)

class Container[T: PrintableAndSized]:
    value: T

    def describe(self) -> None:
        print(self.value.to_str())  # From Printable
        print(len(self.value))       # From Sized
```

Generated C++:
```cpp
template<typename T>
concept Printable = requires(const T& t) {
    { t.to_str() } -> std::convertible_to<std::string_view>;
};

template<typename T>
concept PrintableAndSized = requires(const T& t) {
    { t.to_str() } -> std::convertible_to<std::string_view>;
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
};

template<PrintableAndSized T>
struct Container {
    T value;
    void describe() {
        std::cout << this->value.to_str() << "\n";
        std::cout << tpy::__len__(this->value) << "\n";
    }
};
```

**Key points:**
- Child protocols inherit all methods and fields from parent protocols
- A type conforming to a child protocol automatically conforms to all parent protocols
- Bounded type parameters (`T: PrintableAndSized`) can use methods from all ancestor protocols
- The generated C++ concept includes requirements from all parent protocols
- Multiple inheritance is supported (e.g., inheriting from both `Printable` and `Sized`)
- Generic parent protocols are supported: `class Counted[T](Iterable[T], Protocol)`
  inherits the parent's methods/fields with the parent's type parameters
  substituted by the child's. The arity of the parent reference must match
  the parent's declared type parameters.

#### Working: Protocol Fields

Protocols can require fields in addition to methods:

```python
from typing import Protocol
from tpy import int32

class HasValue(Protocol):
    value: int32

class Point:
    value: int32
    def __init__(self, v: int32):
        self.value = v

def get_value[T: HasValue](item: T) -> int32:
    return item.value  # Access protocol field

p = Point(42)
print(get_value(p))  # Output: 42
```

Generated C++ concept:
```cpp
template<typename T>
concept HasValue = requires(const T& t) {
    { t.value } -> std::convertible_to<int32_t>;
};
```

Protocols can combine fields and methods:
```python
class Container(Protocol):
    count: int32
    def is_empty(self) -> bool: ...
```

Generic protocols can use type parameters in fields:
```python
class Holder[T](Protocol):
    item: T

def get_item[T: Holder[int32]](h: T) -> int32:
    return h.item
```

**Conformance**: A type conforms to a protocol with fields if it has all required fields with matching types. Field type mismatches cause inference failures.

#### Working: Self Type in Protocols

The `Self` type can be used in protocol method signatures to refer to the implementing type. This enables patterns where the return type or parameter type should match the concrete type:

```python
from typing import Protocol, Self
from tpy import int32

class Addable(Protocol):
    def __add__(self, other: Self) -> Self: ...

def add_values(x: Addable, y: Addable) -> None:
    result = x + y
    print(result)

a: int32 = 21
b: int32 = 21
add_values(a, b)  # int32 conforms: __add__(int32) -> int32
```

Generated C++:
```cpp
template<typename T>
concept Addable = requires(const T& t) {
    { t + std::declval<T>() } -> std::convertible_to<T>;
};

template<Addable T_x, Addable T_y>
void add_values(T_x& x, T_y& y) {
    auto result = (x + y);
    std::cout << result << "\n";
}
```

When checking protocol conformance, `Self` is substituted with the actual type being checked. For example, `int32` conforms to `Addable` because `int32.__add__(int32) -> int32` matches the protocol signature.

`Self` can also be used inside wrapper types like `Own[Self]`:

```python
from __future__ import annotations
from typing import Protocol, Self
from tpy import int32, Own

class Addable(Protocol):
    def __add__(self, other: Self) -> Own[Self]: ...

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: Point) -> Own[Point]:
        return Point(self.x + other.x, self.y + other.y)
```

`Point` conforms to `Addable` because `Point.__add__(Point) -> Own[Point]` matches `__add__(Self) -> Own[Self]` after substituting `Self` with `Point`.

A protocol may also reference its own name (or another protocol, mutually) in a method signature -- e.g. `def half(self) -> Chainable: ...` inside `class Chainable(Protocol)`. Conformance checking is cycle-guarded (coinductive), so recursive and mutually-recursive protocol signatures work for structural protocols; `Self` remains the preferred spelling where it fits.

#### Working: Self Type in Record Methods

`Self` can be used in return types and parameter types of record (class) methods. It resolves to the current class type, enabling method chaining and builder patterns:

```python
from typing import Self
from tpy import int32

class Builder:
    name: str
    value: int32

    def set_name(self, name: str) -> Self:
        self.name = name
        return self

    def with_offset(self, other: Self) -> int32:
        return self.value + other.value

b = Builder("start", int32(0))
b.set_name("hello").set_value(int32(42))  # method chaining
```

For generic classes, `Self` resolves to the full parameterized type:

```python
class Stack[T]:
    def push(self, item: T) -> Self:   # -> Stack[T]
        self.items.append(item)
        return self
```

**Restrictions**:
- `Self` cannot be used as a field type (would create infinite-size struct)
- `Self` cannot be used in `@staticmethod` methods (no `self` to refer to)
- `Self` cannot be used in free functions (must be inside a class)
- `Self` in `@dynamic` protocols is not allowed (vtable dispatch can't vary return types)
- `self: Own[Self]` cannot be combined with `@readonly` or used on `__init__`/`__del__`

**Inheritance note**: `Self` resolves to the class that *defines* the method, matching C++ static dispatch semantics. A parent method returning `Self` returns the parent type, not the subclass type.

#### Working: Consuming Methods (`self: Own[Self]`)

Methods annotated with `self: Own[Self]` take ownership of the receiver. After calling a consuming method, the variable is consumed and cannot be used again. This generates a C++ rvalue-qualified method (`&&`), and call sites emit `std::move(obj).method()`.

```python
from typing import Self
from tpy import Own

class Wrapper:
    _value: int

    def __init__(self, value: int):
        self._value = value

    def take(self: Own[Self]) -> int:
        return self._value

w = Wrapper(42)
result = w.take()   # w is consumed, moves self
# w.get()           # ERROR: w was consumed
```

**Rules**:
- Can only be called on local variables and temporaries (not fields, not `self`)
- Calling through `Ptr[T]` is an error (pointers don't own pointees)
- Use after consume is a compile error
- Reassignment revives a consumed variable
- Branch-aware: consuming in one `if` branch makes the variable consumed after the `if`
- Field accesses on `self` inside the method body are automatically moved on return
- `self` itself is owned too, so `return self` at an `Own[Self]` return MOVES the receiver (`return std::move((*this));`) -- the builder-step idiom `def updated(self: Own[Self]) -> Own[Self]: ...; return self` needs no `copy()` and compiles for a `@nocopy` class. Reading `self` again on the same path after it has been moved into an owning slot is the ordinary use-after-move error
- For classes with `__del__`, the compiler auto-inserts destructor suppression (`__tpy_owned_ = false`) at the top of the consuming method body, preventing double-free of moved-from objects

**Real-world example**: `Box[T].take()` uses consuming methods to safely extract the contained value:

```python
from tplib.box import Box
from tpy import int32

b = Box(int32(42))
val = b.take()      # Moves out value, destroys box
print(val)          # 42
# b is consumed -- any further use is a compile error
```

#### Working: `auto_own[Self]` -- Borrowing + Consuming Overloads

`auto_own[Self]` on a method's `self` parameter generates two overloads from a
single definition: a borrowing overload and a consuming overload. `auto_own[T]`
in the return type becomes `T` in the borrowing clone and `Own[T]` in the
consuming clone. This mirrors `auto_readonly` for const-vs-mutable overloads.

```python
from typing import Self
from tpy import auto_own

class Pair[T]:
    first_val: T
    second_val: T

    def first(self: auto_own[Self]) -> auto_own[T]:
        return self.first_val
```

The compiler clones this into:
- Borrowing: `first(self) -> T` (returns reference to field)
- Consuming: `first(self: Own[Self]) -> Own[T]` (moves field out of consumed struct)

C++ generates `&`-qualified and `&&`-qualified overloads. Overload resolution
selects consuming when the receiver is at its last use.

In consuming method bodies, field access on `self` yields `Own[FieldType]`
(ownership propagation through fields), allowing fields to be returned as
owned values without explicit `copy()`. `self` carries the same ownership:
it is the frame's own value, so its last use relocates it and `return self`
renders the move.

#### Working: Auto-Consuming Iteration at Last Use

For-loops automatically use consuming iteration when the iterable is at its
last use. This avoids element copies for `list`, `set`, and `dict` (keys):

```python
items: list[Node] = [Node(1), Node(2)]
for x in items:  # items at last use -- auto-consumes (vector moved into OwnIter)
    print(x.val)

s: set[str] = {"a", "b"}
for x in s:  # s at last use -- auto-consumes (set moved into OwnIterSet)
    print(x)
```

When the container is still needed after the loop, borrowing iteration is used
automatically. `own_iter(container)` forces consuming iteration (list only);
the compiler warns if the container is not at its last use, since the
underlying vector is moved and subsequent uses are undefined behavior.

User-defined types can opt in via `auto_own[Self]` on `__iter__`.

**Limitations**: Consuming `dict.items()` is not yet supported -- the
`Own[Self]` overload changes the return type, breaking non-iteration callers.
See `docs/CONSUMING_ITERATION_DESIGN.md` for details.

#### Working: Regular Method Calls on Protocol Types

You can call any method defined in a protocol on a protocol-typed value:

```python
from __future__ import annotations
from typing import Protocol, Self
from tpy import int32, Own

class Duplicable(Protocol):
    def duplicate(self) -> Own[Self]: ...

class Value:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def duplicate(self) -> Own[Value]:
        return Value(self.x * 2)

def double_it(d: Duplicable) -> None:
    result = d.duplicate()  # OK: calls method via protocol

double_it(Value(21))  # OK: temporaries work via auto-generated temp vars
```

**Note on temporaries**: When passing a constructor expression (like `Value(21)`) to a protocol-typed parameter, the compiler generates a temporary variable. This is necessary because protocol parameters use references (`T&` or `const T&`) in C++, which cannot bind directly to temporaries (for the mutable case) or to rvalue temporaries with non-trivial lifetimes.

#### Working: Generic Protocol `Sequence[T]`

The `Sequence[T]` generic protocol is available for types that support `len()` and indexing:

```python
from typing import Sequence
from tpy import int32, Array

def first(items: Sequence[int32]) -> int32:
    return items[0]

def sum_all(items: Sequence[int32]) -> int32:
    total: int32 = 0
    i: int32 = 0
    while i < len(items):
        total += items[i]
        i += 1
    return total

def main() -> None:
    nums: list[int32] = [1, 2, 3]
    print(sum_all(nums))  # 6

    arr: Array[int32, 3] = [10, 20, 30]
    print(sum_all(arr))   # 60
```

Generated C++:
```cpp
template<tpy::Sequence<int32_t> T_items>
int32_t sum_all(const T_items& items) {
    int32_t total = 0;
    int32_t i = 0;
    while (i < tpy::__len__(items)) {
        total = tpy::int32_add(total, items[i]);
        i = tpy::int32_add(i, 1);
    }
    return total;
}
```

**Important**: Generic protocols require explicit type arguments. Bare `Sequence` without type args is a compile error:
```python
def bad(items: Sequence) -> int32:  # ERROR: Generic protocol 'Sequence' requires type arguments
    return len(items)
```

**Conforming types**:
- `list[T]`, `Array[T, N]`, `Span[T]` - built-in containers
- `str` conforms to `Sequence[char]` - strings are sequences of characters
- User records with `__len__` and `__getitem__` methods (see below)

**Temporaries**: Passing temporaries (list literals, constructor calls) to protocol-typed parameters works. The compiler generates temporary variables automatically since protocol parameters may use mutable references (`T&`) in C++.

**User records as Sequence**: Records with `__len__` and `__getitem__` automatically conform to `Sequence[T]`. The compiler generates `operator[]` from `__getitem__`:
```python
class IntWrapper:
    data: list[int32]

    def __len__(self) -> int32:
        return len(self.data)

    def __getitem__(self, index: int32) -> int32:
        return self.data[index]

def sum_seq(s: Sequence[int32]) -> int32:
    # Works with IntWrapper, list, Array, etc.
    ...

wrapper: IntWrapper = IntWrapper(nums)
sum_seq(wrapper)  # OK - IntWrapper conforms to Sequence[int32]
```

`__getitem__` may take a **non-integer key** (mapping types): `obj[key]` dispatches to a user `__getitem__` whose key param matches the index type (e.g. `def __getitem__(self, key: str) -> int32`), not only integer-indexed sequences. The index is validated against the key param.

**Mixing protocols**: Generic and non-generic protocols can be used together:
```python
from typing import Sized, Sequence

def process(items: Sequence[int32], container: Sized) -> int32:
    return items[0] + len(container)
```

#### Planned: `MutableSequence[T]` (read/write sequences)

`Sequence[T]` currently only requires `__len__` and `__getitem__`. For full mutable sequence support:

Planned design:
- Introduce `MutableSequence[T]` that models read/write access.
- `MutableSequence[T]` will require additional methods beyond `__len__` and `__getitem__` (likely `__setitem__`, and possibly `append`/`pop` for full Python parity).

Current limitations:
- **User records**: a reference-returning `__getitem__` (`-> Rec`) generates dual `operator[]` overloads (const + mutable via the implicit `@auto_readonly` cloning) that alias the returned storage, so `items[i].method()` mutates through -- but a `MutableSequence`-style protocol surface (`__setitem__` requirement, `append`/`pop`) is still undesigned.

#### Working: `@dynamic` Protocol Declaration and Dispatch

The `@dynamic` decorator marks a protocol for runtime dispatch support. When applied, the compiler generates C++ artifacts for virtual dispatch:

```python
from tpy import dynamic
from typing import Protocol

@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...
```

This generates:
1. **Concept** (`__Pet_Concept__`) -- for internal use (adapter constraints); `T: Pet` bounds in user code also use this
2. **Abstract base** (`Pet`) -- base class with virtual methods, gets the protocol's clean name
3. **Owning adapter** (`tpy::Adapter<Pet, T>`) -- concept-constrained wrapper storing `T inner` by value
4. **Ref adapter** (`tpy::RefAdapter<Pet, T>`) -- zero-copy wrapper storing `T& inner` by reference

**Object safety rules** -- `@dynamic` protocols must be:
- **Self-free**: no `Self` type in method params or return types

Markerless `@dynamic` protocols (no methods, no fields) are allowed -- they act as
phylum tags for the polymorphism predicate that gates `Optional[ConcreteRoot]` class
dispatch via `isinstance`/`dynamic_cast`. The stdlib's `Throwable` is the canonical
example: it has no methods and roots the `BaseException` tree so the M2 dispatch
machinery activates for exception isinstance checks in `__exit__` bodies.

Generic `@dynamic` protocols are supported (e.g., `@dynamic class Container[T](Protocol)`). Each instantiation `Container[int32]`, `Container[str]` has an independent vtable; the concept, base class, and adapter partial specializations are emitted once as C++ class templates. See `docs/DYNAMIC_PROTOCOL_DESIGN.md` for the codegen shape.

Two additional constraints apply to generic `@dynamic` protocols:
- **Reserved type-parameter name `__tpy_Impl`**: the adapter codegen uses `__tpy_Impl` as the concrete-impl template parameter on its partial specializations. A user-declared protocol type param of the same name is rejected at codegen time. The `__tpy_` prefix is the project's reserved namespace; users should not pick names that start with it.
- **Direct inheritance + `T` in method-parameter position**: a class that explicitly inherits a generic `@dynamic` protocol whose methods take a parameter typed as the protocol's type parameter (e.g. `def put(self, val: T) -> None`) is rejected at sema. Use structural conformance instead (drop the explicit base; the adapter path handles parameterized parameters correctly). This is an interim guard while a codegen fix is pending; see `BUGS.md`.

**Per-method type parameters** -- protocol methods cannot declare their own type parameters (`def method[U](self, ...)`). This applies to all protocols, not only `@dynamic`. Type parameters belong on the protocol class. The current diagnostic for an attempted per-method type param surfaces as "Unknown type" on the parameter name.

Static dispatch (`T: Pet`) works identically for both `@dynamic` and regular protocols:

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> str:
        ...

def speak[T: Pet](animal: T) -> None:
    print(animal.make_noise())  # static dispatch via template
```

**Dynamic dispatch** -- protocol-typed variables, function parameters, method parameters, and constructor parameters use runtime polymorphism:

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> str: ...

class Dog(Pet):
    def make_noise(self) -> str: return "Woof"
class Cat(Pet):
    def make_noise(self) -> str: return "Meow"

# Protocol-typed local variable
pet: Pet = Dog()
pet.make_noise()    # virtual dispatch -> "Woof"
pet = Cat()         # rebind to different concrete type
pet.make_noise()    # virtual dispatch -> "Meow"

# Function parameter
def greet(pet: Pet) -> None:
    print(pet.make_noise())

greet(Dog())        # implicit upcast (Dog inherits Pet base class)
greet(pet)          # already-erased value passed directly

# Method and constructor parameters
class Recorder:
    message: str
    def __init__(self, pet: Pet) -> None:
        self.message = pet.make_noise()
    def update(self, pet: Pet) -> None:
        self.message = pet.make_noise()
```

**Three dispatch paths** -- the compiler chooses the optimal path based on how the type relates to the protocol:

| Scenario | C++ mechanism | Overhead |
|----------|---------------|----------|
| Class explicitly inherits protocol (`class Dog(Pet)`) | Direct C++ inheritance (`struct Dog : Pet`), implicit upcast to `Pet&` | Zero -- same as hand-written virtual dispatch |
| Structural conformance, lvalue arg (`greet(parrot)`) | Ref adapter (`tpy::RefAdapter<Pet, Parrot>{parrot}`) | One indirection, zero copy |
| Structural conformance, rvalue arg (`greet(Parrot())`) | Owning adapter (`tpy::Adapter<Pet, Parrot>{Parrot()}`) | Owns the value, no dangling |
| Local protocol variable (`pet: Pet = Dog()`) | Stack slot + `Pet*` pointer-local | Zero (direct inheritance) or adapter (structural) |

When a class explicitly inherits a `@dynamic` protocol, the compiler generates C++ struct inheritance with `override` on matching methods:
```python
class Dog(Pet):                    # -> struct Dog : Pet {
    def make_noise(self) -> str:   # ->   std::string make_noise() override { ... }
        return "Woof"
```

Structural conformance (satisfying the protocol without inheriting it) uses adapter wrapping at dispatch points. The ref adapter ensures lvalue arguments are passed by reference (mutations visible to caller), while the owning adapter is used for rvalues.

**Conditional/loop reassignment** -- when a dynamic protocol variable is declared or reassigned inside an `if`/`else` branch, the adapter slot is hoisted to function scope via `std::optional` so the value outlives the block:

```python
def choose_pet(cond: bool) -> None:
    if cond:
        pet: Pet = Dog()
    else:
        pet = Cat()
    print(pet.make_noise())   # safe -- slots live at function scope
```

**Return types** -- returning a `@dynamic` protocol type is allowed when the value provably outlives the caller (globals, parameters). Returning a locally-constructed value is an error (the stack adapter would be destroyed):

```python
def echo(pet: Pet) -> Pet:
    return pet             # OK: parameter outlives caller

def bad() -> Pet:
    return Dog()           # ERROR: local adapter destroyed on return
```

**`Optional[Pet]`** -- `Optional` of a `@dynamic` protocol is allowed at a **free-function parameter position** and as an **init-only function-local** (`p: Optional[Pet] = Dog()`): both lower to a `[const] Pet*` borrow (may be nullptr) pointing at the materialized rvalue, `is None` narrows it, and `isinstance(p, Sub)` dispatches via `dynamic_cast` (inheritance conformer) or `tpy::dyn_adapter_cast` (structural conformer). A **record-method or constructor** param of the same spelling is still rejected -- the pointer-repr allowance is threaded from the free-function registration path only (`BUGS.md#opt-dyn-protocol-method-param`). The init-only local's **rvalue rebind** (`p = Cat()`) is rejected -- the shared slot can't be retyped per rvalue. It remains a sema error in field / return / container / owned / global positions and as a no-init local, where the abstract base has no value representation -- use `Box[Pet] | None` (a nullable owning slot) for those.

**`Own[Optional[Polymorphic]]` rejection** -- `Own[Optional[E]]` where `E` is a concrete class that inherits a `@dynamic` protocol (e.g. `Own[Optional[BaseException]]`) is a sema error in params, return types, record fields, and any nested position reached by `validate_type` (e.g. as a tuple element). The Own-Optional slot is laid out for the base class alone, so storing a derived instance would slice the dynamic type, and `isinstance` dispatch on the slot has no valid lowering today. Use `Optional[E]` (borrowed pointer that may be None) or `Box[E]` (owned, polymorphism-preserving) instead. Diagnostic is emitted from `tpyc/sema/type_ops.py::validate_type` so every position reports the same message via one helper. The broader "polymorphic + wrappers + isinstance" landscape (including this rejection's eventual lifting) is tracked in TODO.md.

**`Optional[Own[Polymorphic]]` rejection** -- the *other* ordering, `Optional[Own[Pet]]` (nullable owned-by-transfer of a `@dynamic` protocol or polymorphic class), is also a sema error. A polymorphic `Own[Pet]` lowers to `std::unique_ptr<Pet>`, so the optional slot is `std::optional<std::unique_ptr<Pet>>` -- a double indirection the member-access / call-site / isinstance lowerings do not thread today (a *concrete* `Own[T]` collapses to `std::optional<T>` by value and stays supported). Use `Optional[Box[Pet]]`, the idiomatic nullable owned-polymorphic form. Plain `Own[Pet]` (non-optional) params accept subclass/conformer rvalues normally.

**`Box[P]` for `@dynamic` P -- heap-owned erased dynamic-protocol values.** `Box[Pet]` accepts any conformer (inheritance or structural) and stores it through the C++ abstract base:
- **Inheritance path:** `Box[Pet](Parrot(...))` where `Parrot(Pet): ...` emits `std::unique_ptr<Pet>(new Parrot{...})`. Trivial pointer upcast through Parrot's inherited vtable.
- **Structural path:** `Box[Pet](Dog(...))` where `Dog` has the required methods but doesn't inherit Pet emits `std::unique_ptr<Pet>(new tpy::Adapter<Pet, Dog>{...})`. The Adapter wraps Dog and routes virtual calls.

Same machinery in both paths once the Box is constructed: `Pet&` access via `box.get()`, virtual dispatch on method calls, virtual destructor cleanup on `box`'s drop. Unblocks `list[Box[Pet]]` (heterogeneous polymorphic containers), record fields of erased-protocol type (`class Owner: pet: Box[Pet]`), and Send-tagged storage via `Box[Send[Pet]]` (see `docs/SEND_SYNC_DESIGN.md` OQ5).

The same wrapping rule fires anywhere a `Own[P]` parameter receives a concrete conformer: codegen emits the heap-owning `unique_ptr<P>` wrapper at the call site. Sema's `infer_type_params_for_record` prefers the LHS hint's `T=P` over the arg-inferred concrete T when the arg doesn't inherit P (structural case); when the arg DOES inherit (e.g. `Box[Pet] = Box(Parrot())`), the existing `Covariant[T]` path handles uplift and the wrapping doesn't fire.

**Inward LHS-hint propagation in generic calls and constructors.** When a generic call (free function, instance method, static method) or a generic *record constructor* is analyzed with a known LHS hint, sema first matches the hint against the call's "return shape" -- the function's return type, or `NominalType(record.name, [TypeParamRef(tp) ...])` for a constructor -- to pre-bind any type params it determines, then substitutes those into each parameter type to form an `expr_type_hint` for the argument's first analysis pass. This lets nested generic chains resolve in one pass at any depth: `r: Rc[Box[Pet]] = Rc.new(Box(Dog("Rex")))` is the two-level case (function-call seed -> constructor LHS-hint preference); `r: Rc[Box[Box[Pet]]] = Rc.new(Box(Box(Dog(...))))` is the three-level case where each Box constructor in turn seeds its own arg from its incoming hint. Explicit positional type args (including partial / wildcard forms) override the seed at their positions; arg-derived inference still has the final say where the seed leaves a param unbound. Overloaded calls (`@overload` on free functions and methods) participate in the same propagation when **exactly one** candidate's return shape matches the LHS hint: overload-probe pre-analysis seeds each arg from that candidate's params, so nested ctor/call args see the right contextual hint on their first pass and the LHS-matching overload is preferred. With 2+ candidates' returns matching the LHS hint, probe-seeding is skipped to avoid splicing hints across overloads (which could mislead inner generic-ctor T-binding through the post-selection cache short-circuit) and overload resolution falls back to unhinted arg analysis.

**Method support for abstract P:**
- `box.get()`, `box.__init__`, `box.__del__` -- work for any P.
- `box.set(value)` -- works for abstract P via `tpy::heap_replace<T>` (concrete branch: in-place destroy + placement-new; abstract branch: free + take).
- `box.take()` -- works for abstract P via `tpy::transfer_ownership<T>` (returns `own_return_t<T>`, which is `unique_ptr<P>` for abstract P).
- `box.clone()` -- gated on `T: Copyable` (per-method shadow bound). Abstract `@dynamic` P doesn't conform to Copyable (no usable copy ctor), so `Box[Pet].clone()` is rejected at sema with a clean diagnostic (`Method 'clone' requires type parameter 'T' to satisfy 'Copyable'`).
- `box.__eq__` / `__lt__` etc. -- require `T: Equatable` / `T: Comparable`; abstract P doesn't satisfy these, so the methods are unavailable for `Box[Pet]`.

**`Own[P]` plain function params and returns work end-to-end.** For abstract `@dynamic P`, `Own[P]` lowers to `std::unique_ptr<P>` at both the parameter and return slots; member access on the param renders as `p->method()`, and returning a concrete conformer wraps it as `std::make_unique<Adapter<P, Concrete>>(...)` (or `std::make_unique<Concrete>(...)` for inheritance conformers). This includes methods *on the protocol itself* whose return type names the protocol -- e.g. `def replicate(self) -> Own[P]` (or `Own[P[T]]` for a generic `@dynamic P[T]`); the abstract-base struct is forward-declared before its concept so the `unique_ptr<P>` constraint resolves, and adapter overrides reference the protocol via its module-qualified name to bypass dependent-base name lookup in the `tpy::` namespace.

**Cross-module** -- `@dynamic` protocols can be defined in one module and imported in another. The compiler generates fully qualified C++ names (e.g., `::tpyapp::pets::Pet`).

**Protocol inheritance** -- a `@dynamic` protocol can extend another `@dynamic` protocol. The base class inherits from the parent's base (`struct NamedPet : Pet`), so a `NamedPet`-typed value can be passed to a `Pet`-typed parameter via implicit C++ upcast:

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> str: ...

@dynamic
class NamedPet(Pet, Protocol):
    def name(self) -> str: ...

class Dog(NamedPet):
    def make_noise(self) -> str: return "Woof"
    def name(self) -> str: return "Rex"

def greet_pet(pet: Pet) -> None:
    print(pet.make_noise())

greet_pet(Dog())  # Dog -> Base_NamedPet -> Base_Pet (transitive upcast)
```

See [docs/DYNAMIC_PROTOCOL_DESIGN.md](DYNAMIC_PROTOCOL_DESIGN.md) for the full design.

#### Working: `ValueType` Marker Protocol

The `ValueType` marker protocol declares that a user-defined record has value semantics -- it is small, cheaply copyable, and behaves like a built-in value type (int32, bool, etc.). Value types are **immutable**: like their built-in counterparts (and frozen dataclasses), their fields are set in `__init__` and never reassigned afterward. Immutability is what makes copy-vs-alias unobservable, so value types stay CPython-portable (mutating a copied value would otherwise diverge). Import it from the `tpy` module:

```python
from tpy import int32, ValueType

class Vec2(ValueType):
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
```

**Effects of `ValueType`:**

- **C++ `tpy::is_value_type` specialization** -- generated code specializes the trait so the runtime recognizes the type as a value type
- **Copy warning suppression** -- no "copies X into field" warnings for `ValueType` records, since copy-vs-share is unobservable for value types
- **`T: ValueType` bounds** -- generic type parameters bounded by `ValueType` also suppress copy warnings:

```python
class Box[T: ValueType]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value  # No warning: T is guaranteed to be a value type
```

**Validation rules:**

- All fields must themselves be value types (or `ValueType`-bounded type params). Non-value-type fields cause a compile error.
- `@nocopy` and `ValueType` are mutually exclusive -- a `@nocopy` class cannot be a `ValueType`.
- A `ValueType` class cannot inherit from a non-`ValueType` parent.
- Fields are immutable: assigning a field anywhere other than the class's own `__init__` is a compile error (`Cannot assign to field '...' of immutable value type '...'`). This covers plain assignment, augmented assignment, and assignment to `self.<field>` in any non-`__init__` method.
- An explicit `__init__` is required -- there is no synthesized aggregate constructor, since immutable fields can only be set during construction. (`@native` value types are exempt: they are constructed on the C++ side.)

**Implicit conformance:** Built-in value types (int32, bool, float, etc.) implicitly conform to `ValueType`, so they can be used as arguments for `T: ValueType` bounded type params without explicit declaration.

#### Working: `Default` Marker Protocol and `make_default()`

The `Default` marker protocol declares that a type supports zero-argument default construction. It maps to the C++20 `std::default_initializable` concept.

**Implicit conformance** -- the following types conform to `Default` without explicit declaration:
- All primitives: fixed-width integers, `float`, `bool`, `str`, `String`, `StrView`, `char`, `int`
- `list[T]`, `dict[K, V]`, `Span[T]` (empty container is default)
- `Optional[T]` (`None` is default)
- `Ptr[T]` (default-constructs to `nullptr`)
- `Array[T, N]` if element `T` satisfies `Default`
- `tuple[T1, T2, ...]` if all element types satisfy `Default`
- User records whose `__init__` has no required parameters; aggregates (no `__init__`) only when every parent and field type also satisfies `Default`. Records whose default ctor codegen suppresses are never `Default` (see `del_suppresses_default_ctor` in `tpyc/typesys.py`): `@nocopy + __del__` (e.g. `Box`, `Rc`), `__del__` with required-param `__init__`, and `__del__` with an indeterminate-init own field.

**`make_default()`** is a portable function for default-constructing generic types:

```python
from tpy import int32, make_default

# Explicit type argument
x = make_default[int32]()    # -> int32(0)

# Inferred from context
y: str = make_default()      # -> ""

# With type parameter bounds
def create[T: Default]() -> T:
    return make_default()    # T inferred from return type
```

`make_default[T]()` compiles to `T{}` in C++. Unlike `T()` (which also works in TPy but is not valid CPython), `make_default()` is portable -- it works in both TPy and CPython via the `lib/cpy/tpy/` stubs.

**Per-method bounds:** Methods on generic classes can add bounds to inherited type params. This enables methods that require `Default` without restricting the entire class:

```python
class ArrayList[T, N: int]:
    def append_default[T: Default](self) -> None:
        self._storage.init(uint32(self._size), make_default())
        self._size += 1
```

A per-method bound on a shadowed class type param is interpreted as **"this method is only callable when the class type satisfies the bound"** -- i.e. `ArrayList[NotDefault]` cannot call `append_default`. Sema enforces the rule at every dispatch site (direct method calls, operators including comparisons/arithmetic/augmented assignment/unary, `in`/`not in`, `hash()`, and any protocol-conformance check), producing a diagnostic like `Method 'append_default' requires type parameter 'T' to satisfy 'Default', but 'NotDefault' does not conform`. The C++ output also carries the same rule as a `requires` clause; users see the TPy diagnostic before any C++ error on every site except the subclass-receiver operator gap noted below.

Shadowed-bound methods resolve through **subclass receivers** too: for a method inherited from a generic base (`class IntBag(Bag[int32, 4])` calling `contains[T: Equatable]`), the base instantiation binds the shadowed param, so the call dispatches without inference and the bound is checked against the inherited binding (`tests/cases/generics/method_bound_shadow_subclass*` and `error_method_bound_shadow_subclass*`). Known limitation: on the *operator* dispatch sites (`in`, binops, `hash()` / protocol conformance), the sema-level bound check currently fires only for direct generic receivers -- through a subclass a violation is still caught, but by the C++ `requires` clause instead of the clean TPy diagnostic (see BUGS.md).

**`T()` deprecation warning:** Using `T()` for default construction of type parameters emits a warning recommending `make_default()` instead, since `T()` is not supported in CPython.

#### Working: `Copyable` Marker Protocol

The `Copyable` marker protocol declares that a type's C++ representation is copy-constructible. It maps to the C++ `Copyable` concept (`std::is_copy_constructible_v<T>`).

**Implicit conformance** -- any type that is not `@nocopy`, does not define `__del__` (or inherit one), and does not contain a `@nocopy` field, conforms automatically. A class defining `__copy__` is treated as Copyable even if it would otherwise be rejected (the `__copy__` escape hatch overrides the `@nocopy` / `__del__` / nested-`@nocopy` propagation rules), mirroring how every "copies X into Y" diagnostic in the compiler reads `__copy__`.

The primary use is as a **per-method shadow bound** on generic types whose methods are only valid when `T` is copyable. The canonical example is `tplib.Box[T].clone`:

```python
@nocopy
class Box[T](Deref[T], Covariant[T]):
    ...

    @readonly
    def clone[T: Copyable](self) -> Own[Box[T]]:
        return Box(self.get())
```

`Box[Rc[int32]].clone()` is rejected with a TPy diagnostic (`Method 'clone' requires type parameter 'T' to satisfy 'Copyable', but 'Rc[int32]' does not conform`) instead of the cryptic C++ template error the compiler used to surface from `box.hpp`. `Rc[T]` itself does **not** need the bound on its `clone()`, because Rc just bumps a refcount and never touches T's copy constructor.

#### Working: `NativeIterable[T]` (C++ range-for optimization marker)

`NativeIterable[T]` is a **marker protocol** for types that support C++ range-based for loops. It's defined in the `tpy` module (not `typing`) because it maps to C++ `begin()`/`end()` iteration rather than Python's `__iter__`/`__next__` protocol. Built-in containers extend it (via `extends` in their stubs); the compiler uses this to select direct range-for on concrete types like `list`, `dict`, `set`, `Span`, `Array`, `str`, `bytes`.

**Use `Iterable[T]` (from `typing`) for ordinary function parameters.** It's the Python-standard convention and the compiler handles the common cases.

**Advanced: manual fast-path via isinstance narrowing.** Power users who want explicit control over the iteration strategy (e.g. different loop bodies per branch, or deterministic opt-in to range-for for a specific hot function) can narrow a plain `Iterable[T]` parameter:

```python
from typing import Iterable
from tpy import NativeIterable, int32

def sum_fast(it: Iterable[int32]) -> int32:
    total: int32 = 0
    if isinstance(it, NativeIterable):
        # Narrowed to NativeIterable[int32] -> C++ range-for (begin/end).
        for x in it:
            total += x
    else:
        # Iterable[int32] -> universal __iter__/__next__ loop.
        for x in it:
            total += x
    return total
```

The element type is threaded through the parent-protocol relationship (`NativeIterable[T]` extends `Iterable[T]`), so the emitted `if constexpr` carries both template args. The older `Iterable[T] | NativeIterable[T]` union form still works but is no longer needed. This pattern is an escape hatch, not the recommended default -- ordinary code should stick with plain `Iterable[T]`. Automatic dispatch inside `Iterable[T]`-typed templates is tracked as a future codegen improvement.

```python
from typing import Iterable
from tpy import int32

def sum_all(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

# All built-in containers work:
nums: list[int32] = [1, 2, 3]
print(sum_all(nums))  # 6
```

**Key characteristics**:
- **Marker protocol, native-only**: `NativeIterable[T]` extends `Iterable[T]`
  and has no direct methods of its own. It is reserved for `@native` types --
  its C++ concept requires `std::ranges::begin(t)`/`end(t)`, which TPy can
  only guarantee for built-ins backed by hand-written C++ ranges. Non-`@native`
  records that declare `implements NativeIterable` are rejected at sema with a
  suggestion to use `Spannable[T]` or `Iterable[T]`. User types reach the
  fast path via `isinstance(it, NativeIterable)` narrowing on a plain
  `Iterable[T]` parameter (see the example above).
- **Codegen-side `begin()/end()` synthesis**: User records with `__span__()` get compiler-synthesized `begin()/end()` (delegating to the returned span) so they satisfy `std::ranges::input_range` for C++ interop. Skipped when the type also has a `__iter__` that's not `SpanIter[T]` -- otherwise comprehensions and range-for would silently diverge from for-loops (which use `__iter__/__next__`). The span's iterators are raw `T*` into the underlying storage owned by self, so the prvalue span dying is harmless.
- **Built-in conformance**: All built-in container types (`list`, `dict`, `set`, `Span`, `Array`, `str`, `bytes`, etc.) extend both `NativeIterable[T]` and `Iterable[T]`.
- **Codegen optimization**: For concrete built-in NativeIterable types and for `NativeIterable[T]` / `Spannable[T]` protocol parameters, the compiler emits C++ range-based-for (`for (auto x : c)`). Everything else falls to the universal `::tpy::__iter__()` + `__next__()` loop.
- **API methods use `Iterable[T]`**: `str.join()`, `list()` constructor, `dict()` constructor accept `Iterable[T]`; `list.extend()` and `list +=` accept `Iterable[Own[T]]` (triggers copy warnings for reference-type elements).

See [docs/PROTOCOL_DESIGN.md](PROTOCOL_DESIGN.md) for the full design rationale.

#### Working: Element-Type Widening at Protocol Positions

When a container satisfies a protocol parameterized by an element type (e.g. `list[A]` vs `Iterable[B]`), the element conformance uses **widening only** -- iteration widens each element into the target element type, but narrowing is rejected:

```python
from typing import Iterable
from tpy import int32, int64, uint8

def take_int32(it: Iterable[int32]) -> None: ...
def take_int64(it: Iterable[int64]) -> None: ...
def take_uint8(it: Iterable[uint8]) -> None: ...

xs: list[int32] = [1, 2, 3]
take_int32(xs)        # OK: exact element match
take_int64(xs)        # OK: int32 widens to int64
take_uint8(xs)        # Error: int32 does not widen to uint8 (narrowing)
```

**Unannotated integer list literals resolve to the configured default int type** (typically `int32`). `[1, 2, 3]` passed to a function expecting `Iterable[uint8]` does *not* match, regardless of whether the literal values would fit -- the concrete list is `list[int32]`, not `list[uint8]`. To target a narrow iterable, annotate the source (`xs: list[uint8] = [1, 2, 3]`) or construct explicitly.

Supported element widenings: fixed-width int -> wider fixed-width int (same signedness, strict bit growth; or unsigned -> strictly wider signed), fixed int -> `BigInt`, fixed/`BigInt` -> `float`/`float32`, `float32` -> `float`. `readonly[T]` is transparent at element positions (the const qualifier is carried by the container, not the element-type match).

#### Working: Lazy Iteration via `Iterator[T]`

All iterators use the `Iterator[T]` protocol (`__next__(self) -> T` with `@error_return(StopIteration)`), returning `std::expected<T, StopIteration>` in C++. This applies to both user-defined iterators and built-in iterators (Range, SpanIter, generator expressions).

```python
from typing import Iterator
from tpy import int32

class Counter:
    current: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.current = 0
        self.limit = limit

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

# Direct use in for-loop
for x in Counter(5):
    print(x)

# Pass to function taking Iterator[int32]
def sum_iter(it: Iterator[int32]) -> int32:
    total: int32 = 0
    for x in it:
        total += x
    return total

print(sum_iter(Counter(5)))        # 10
```

**Codegen**: `for i in range(...)` is optimized to a C-style counter loop. When the body can rebind `i`, a private counter advances the range and binds the user target at each iteration; body writes do not alter iteration. Targets that need no enclosing storage and have no possible write reported by conservative analysis retain the direct counter, avoiding an extra BigInt copy. `range()` accepts all fixed-width integer types (int8, int16, int32, int64, uint8, uint16, uint32, uint64) as well as BigInt, preserving the element type in the loop variable.

Bare integer literals use the configured default integer type (`--default-int`, default: `int32`), so `range(10)` uses `Range<int32>` by default. For CPython-like behavior, use `--default-int=BigInt`.

```cpp
// range(int8(0), int8(10)) -> int8_t loop
for (int8_t i = 0; i < 10; ++i) { ... }

// range(int32(0), int32(100), int32(3)) -> int32_t loop with upfront overflow check
tpy::range_check_overflow<int32_t>(0, 100, 3);
for (int32_t i = 0; i < 100; i += 3) { ... }
```

Step +/-1 uses `++i`/`--i` with no overflow check. Other steps use an upfront `range_check_overflow` that verifies the final increment won't overflow, then uses unchecked `i += step` in the hot loop. Variable steps use a ternary condition (`step > 0 ? i < stop : i > stop`). All iterators (user-defined and built-in) with `__next__` + `@error_return(StopIteration)` use a direct loop:

```cpp
auto& __iter_0 = it;
for (;;) {
    auto __r_0 = __iter_0.__next__();  // std::expected<T, StopIteration>
    if (!__r_0.has_value()) break;
    int32_t x = *__r_0;
    // body
}
```

**Key characteristics**:
- **Unified protocol**: All iterators use `__next__() -> std::expected<T, StopIteration>`
- **Lazy evaluation**: Values produced one at a time, no container allocation
- **break/continue**: Work naturally in both counter-loops and while-loops

#### Iterator Roadmap

| Phase | Status | What |
|-------|--------|------|
| 1. Iterator protocol | **Working** | User-defined iterators via `__next__()` + `@error_return(StopIteration)` -> `std::expected` |
| 2. Counter-loop optimization | **Working** | `for i in range(...)` -> C-style loop; private induction when the target is writable or needs enclosing storage |
| 3. Range as NativeIterable | **Working** | `Range[T]` is an immutable container with `begin()`/`end()`, supports `list(range(...))` |
| 4. Iterator[T]/Iterable[T] | **Working** | Built-in protocols from `typing`, for-loop support, `iter()` builtin |
| 5. `__span__` protocol | **Working** | `__span__() -> Span[T]` for implicit Span coercion; iteration requires `__iter__()` |
| 5b. `Spannable[T]` protocol | **Working** | Readonly protocol for types with `__span__()`, for-loop and ReadOnlySpan coercion |
| 6. Generator expressions | **Working** | `(expr for x in iterable)` → lazy `make_generator` wrapper, satisfies `Iterable[T]`. A bare reference element (`(n for n in nodes)`) is yielded as a live borrow (`val_or_ref<T>` slot, zero-copy, mutation propagates); a freshly-constructed reference element (`(Node(x) for x in xs)`) is rejected -- use a list comprehension `[...]` to materialize owned storage. Value / `Optional` / tuple elements keep the value slot; a `readonly` element borrows too, through the same `val_or_ref<const T>` spelling a `def`-generator uses. |
| 7. Generator functions | **Working** | `yield` in functions and methods -> a state-machine struct (`__gen_<name>` + `__next__`) implementing `Iterator[T]`, emitted by the shared resumable-frame `while/switch` emitter that also powers `async def`. Every generator takes it, single-yield or not -- free or method, with or without params, generic (explicit `[T]` type params) or not, with or without a tuple yield, body of leaf statements and/or `if`/`while`/`for` (including `for`/`while`-`else`; the for-loop range/begin_end peepholes are preserved), `try`/`except`/`finally` (including `yield` in `finally`, and `return` in a suspending `finally` -- the return parks into the same pending-return slot used for try-body returns, and overrides any pending exception per Python semantics; the one restriction is a `ReturnException`-tier `try` -- an `except StopIteration` catching an `@error_return` call in its own body -- which must not need frame states of its own, see the iterator rules below), and `with`. Resumable generator frames are iterable in list/set/dict comprehensions (`[x for x in gen()]`), not just for-loops: the frame struct carries `begin()/end()` via the `next_iter_mixin` CRTP base, the same bridge generator-expression wrappers use (a generator *expression* `(x for x in gen())` takes a separate iterator path that does not yet drive resumable sources -- see BUGS.md). Generic generators emit their templated struct + `__next__` + factory inline in the header; tuple-unpack for-loops (`for a, b in ...`) are supported, with reference elements aliasing the live container element so mutation propagates (CPython-correct); the standalone unpack of an lvalue chain (`a, b = pairs[0]` / `= h.pair` / `= hs[0].pair` / `= self.o.h.pair` -- any number of field/subscript hops off a captured param, `self`, a frame-resident local, a pointer-form alias local or a loop var) aliases the same way -- the tuple lifts through `tuple_to_pointer` off the source lvalue, so the frame's element pointer survives the suspension and writes through it reach the container, and a `readonly` root spells the element pointers `const T*`. A chain with a CALL in it stays rejected: the result is a temporary the frame's alias field would outlive. **Yield ABI is declaration-driven (like a function return):** `Iterator[T]` for a non-value `T` yields a *borrow* -- `__next__` hands out a live reference via a `val_or_ref<T>` slot, so consumer mutation propagates to the source (CPython semantics); each `yield` must root in frame-held storage (a parameter, `self` field, a frame-resident local, or loop var over a frame-held container) -- a fresh `yield Node(1)` is rejected and pointed at `Iterator[Own[T]]`. A generator's own local is frame-resident (it lives in the resumable-frame struct, stable for the generator's life), so yielding a borrow of it is sound and the consumer's in-place mutation is visible to the generator on resume -- the `os.walk` topdown-pruning contract (`for root, dirs, files in walk(p): dirs[:] = [...]`). A *loop-body-declared* local is frame-resident too, so its borrow may be yielded on the same footing. A bare fixed-size list-literal local (`buf = [1, 2, 3]`) is forced off the `std::array` optimization to a real `list` when its borrow is yielded (the slot hands out a reference the consumer may resize). A yielded local that is itself a value type (an explicit `Array`, a tuple of values) stays rejected -- it has no borrowable reference storage. The same per-member check runs on a tuple yield: a fresh non-value tuple member (`yield (i, Box(i))` under `Iterator[tuple[int, Box]]`) is rejected and pointed at the element-scoped fix `Iterator[tuple[int, Own[Box]]]` (the tuple slot is borrow form `std::tuple<int, Box*>`, so a fresh member would dangle); a non-value member nested inside an *inner* tuple is rejected outright (codegen's flat tuple conversion can't represent the nested borrow slot -- nested value / `Own` members are fine). The same *fresh*-member rejection covers the *bound* form: binding a tuple with a freshly-constructed non-value member to a local and yielding/returning it by name (`t = (i, Box(i)); yield t`, or a fresh recursive-union-wrapper member) is rejected -- pointed at the `Own[...]` element fix; the fact is derived from the binding's provenance, so it propagates through a local-to-local alias (`u = t; yield u`), an alias chain (`s = r = t`), a ternary of such locals, a one-branch alias (UNION merge), and a self-assignment. A *durable* reference member SHARES: the bound local is pointer borrow form (`std::tuple<int, Box*>`), so `t = (i, b); yield t` (and the param-sourced `def gen(p: tuple[int32, Box]): yield p` and call-returned `u = make(); yield u` forms) hand out a live borrow of `b` -- post-boundary mutation reaches the original object, matching CPython aliasing. `Iterator[Own[T]]` yields an *owned* value moved out of the frame (fresh values fine). A generator method yielding a mutable borrow of `self`'s elements is emitted non-`const` (it exposes mutable access, like `def f(self) -> T: return self.field`). The bound borrow is *ephemeral* -- valid only until the next `__next__()` -- so the consumer is barred from retaining it past the iteration step where unsound (re-yielding it onward in an outer generator, or capturing it by reference in a nested `def`); storing it into value storage (a field / container / global) copies it (safe), and returning or stashing it past its scope is caught by the existing dangling/lifetime checks. Residual: a *caller* that structurally mutates or frees the borrowed container while the generator is live is still only a non-fatal warning (see BUGS.md). Nested suspending `finally` works: the inner `AsyncFinallyExit` forwards any pending exception or pending return into the outer's parking slots before transitioning to the outer's finally entry, so both bodies run on every exit path. **Abandonment cleanup:** dropping a suspended generator (e.g. `break` out of a `for` over it) runs its pending non-suspending `finally` bodies and `with.__exit__`s via the frame destructor, innermost-first -- the analog of CPython's GeneratorExit at refcount drop; `__exit__` receives a `GeneratorExit` as `exc_val` (the CPython close contract: an exceptional exit), though its suppression return value is discarded (nothing can resume inside a destructor, so post-`with` code does not run on suppression the way CPython's would until the next yield); a `for` over a *temporary* generator destroys it at loop exit (brace-scoped), a *named* generator at its scope end, both matching CPython's drop points. Two acknowledged divergences: (1) a `finally` that itself contains `yield` cannot run inside a destructor and is skipped on abandonment, with a compile-time warning at the `yield` site (CPython runs it up to the next yield, then prints and ignores `RuntimeError("generator ignored GeneratorExit")`); (2) cleanup code that raises during destruction panics (`tpy_panic`) instead of CPython's print-to-stderr-and-continue -- fail-fast by design; (3) when several named generators with pending cleanup share one scope, their destructors run in C++ reverse-declaration order, while CPython finalizes in creation order (an implementation detail of refcounting) -- cleanup side effects that depend on cross-generator ordering will differ. Async coroutine frames share the same destructor (a Task abandoned mid-suspension runs its pending helper-based finallies); an async `finally` containing `await` is normally run via the cancellation path, but is skipped if the frame is dropped without cancellation (see BUGS.md). A `yield`/`await` inside a `match` works: the CFG builder decomposes the `match` (arm bodies become states while the type-aware dispatch -- switch / if-elif / variant-index -- is reused unchanged), and pattern-bound names (capture / field / `as`) are frame fields so they survive a suspension in the arm; the arm's subject narrowing (a bare-name subject narrowed to the matched type) is likewise re-established at the resume case. An `isinstance`-narrowed binding inside an `if`/`while` body (a non-value union param or a frame-resident union local, or polymorphic `self`) also survives a suspension: the condition's narrowing facts are stamped on every resumable block built under the narrowed region, and the cast / `std::get` is re-established at each resume case (so post-suspension subclass/arm-specific member access reads the narrowed type); the narrowing's `std::get` / `holds_alternative` unwraps the `frame_slot<variant<...>>` for a union local. Protocol-typed params (`def gen(it: Iterable[T])`) are supported: the param is captured as the deduced template arg `T_<pname>`, and a direct for-loop over it types its iterator frame field against `T_<pname>` (not the un-instantiable C++ concept) and *borrows* the iterable; when the runtime iterable is itself a generator (a self-iterator -- `__iter__` returns `*this`), the frame drives its `__next__()` in place rather than copying it into a second iterator slot (a generator is move-only, so the copy would be a deleted-ctor build error), so `def take(it: Iterable[T]): for x in it: ...` consumes a passed-in generator (`take(repeat(7, 5), 3)`) -- this is what lets `itertools.islice`/`takewhile` wrap `repeat`/`cycle`; aliasing such a param into a local once (`xs = it`) and iterating that local across a suspension is also supported -- the alias is a compile-time forward to the captured param (no frame field of its own), so it reuses the same `T_<pname>` deduction. Reassigning the alias is rejected with a clean diagnostic. An *owned* static-protocol param (`Own[Iterable[T]]`) rides the same capture: the frame field is the deduced `T_<pname>` by value and the call renders `std::move(...)`, so the frame owns rather than borrows; forwarding an argument into a sub-future at such a slot on an `await` is not yet supported. A **`@dynamic`** protocol param (`def gen(s: Src)` / `readonly[Src]`, with `@dynamic class Src(Protocol)`) is supported on a generator frame: the field is the erased base reference (`Src&` / `const Src&`) the sync param already spells, so mutation through it is visible to the caller. The `async def` twin is not yet supported -- a task outlives the call site's `RefAdapter` temp that the frame would borrow -- and rejects with a construct-not-supported diagnostic (see BUGS.md). An explicit **view** param (`Span[T]` and its `readonly` flavours, `StrView`, `BytesView`, a `*args` pack) is captured borrow form on a generator or `async def` frame -- the field is the same view the sync signature spells, ctor-moved -- so it aliases the caller's storage across a suspension where `str`/`bytes` params are copied owned; a view LOCAL in a frame body still rejects. `Fn` (callable) params work in a generator: the concrete callable type is deduced as an `F_<pname>` template arg on the frame (same mechanism as protocol params), so a generator can take and call a predicate/callback (`def takewhile(pred: Fn[[T], bool], it): ...`). Generator methods supported (`__iter__`, custom methods), including on generic classes (`class Box[T]: def items(self) -> Iterator[T]: ...`), with unbounded or protocol-bounded type params (`class Box[T: Iterable[int32]]: ...`). |
| 8. Iterator combinators | **Working** | `enumerate()`, `zip()`, `reversed()`, `map()`, `filter()`. `map` supports 1-5 iterables. `filter(None, iterable)` for truthiness filtering |

See [docs/ITERATOR_DESIGN.md](ITERATOR_DESIGN.md) for the full iterator design document.

#### Working: User-Defined Iterators (`__iter__`/`__next__`)

Two patterns for user-defined iterators:

**Pattern 1: Python-compatible** — `__next__(self) -> T` + `raise StopIteration` (runs in both tpyc and CPython).
The compiler auto-applies `@error_return(StopIteration)`, producing `std::expected<T, StopIteration>` in C++:

```python
from __future__ import annotations
from tpy import int32

class Counter:
    current: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

for x in Counter(5):
    print(x)
```

**Container → Iterator separation** via `__iter__()`:

```python
class NumberRange:
    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)
```

**Rules**:
- Direct `obj.__next__()` calls require `try/except StopIteration` (compile error if unhandled, since `__next__` is `@error_return(StopIteration)`)
- `raise StopIteration` is only allowed inside `__next__` methods (or functions with `@error_return(StopIteration)`)
- `next(it)` builtin supported -- calls `it.__next__()`, requires `try/except StopIteration` like direct `__next__()` calls
- Inside a generator or `async def`, the `try` that is a compile error today is the one meeting BOTH conditions at once: it needs frame states of its own (it holds an `await`/`yield`, or a `break`/`continue` leaving it) AND it holds a call its `except StopIteration` (or any other `ReturnException` type) handler would catch. The frame has no edge for that failing unwrap to take (BUGS.md#frame-try-next-error-return-miscompiles); move the handled call into a helper function the frame calls, or keep the transfer inside the `try`. Either condition alone is fine: a `try` holding a handled call but no suspension and no transfer leaving it compiles, including one nested inside a decomposed outer `try`; and a `try` that only *wraps* suspending code without calling anything the handler catches (`try: yield x / except StopIteration: pass`) is unrestricted -- nothing can enter that handler, since TPy never throws a `ReturnException`

#### Working: `Iterator[T]` and `Iterable[T]` Protocols

`Iterator[T]` and `Iterable[T]` are built-in protocols from the `typing` module, matching standard Python iterator protocol semantics.

**Protocol definitions**:
- `Iterator[T]`: has `__next__(self) -> T` and `__iter__(self) -> Self`
- `Iterable[T]`: has `__iter__(self) -> Iterator[T]`

```python
from typing import Iterator, Iterable
from tpy import int32

def sum_iter(it: Iterator[int32]) -> int32:
    total: int32 = 0
    for x in it:
        total += x
    return total

def sum_all(items: Iterable[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total
```

**`iter()` builtin**: Calls `x.__iter__()`, returns `Iterator[T]`. Works on any type with an `__iter__` method.

```python
it: Iterator[int32] = iter(Counter(5))
```

**Explicit `__next__()` calls**: Since `__next__` is `@error_return(StopIteration)`, direct calls require `try/except`:

```python
it = iter(Counter(5))
try:
    val = it.__next__()
except StopIteration:
    print("exhausted")
```

**Inherited iterators**: Child classes that inherit `__next__` from a parent satisfy `Iterator[T]` protocol and work with `list()`/`set()`/`dict()` constructors. For example, `DoubleCounter(Counter)` can be passed to `Iterator[int32]` params even though `__next__` is defined on `Counter`.

**Missing `raise StopIteration` warning**: If a `__next__` method contains no `raise StopIteration` on any code path, the compiler emits a warning. Such iterators never signal termination and will loop forever when used in `for` loops without an explicit `break`.

**Auto-synthesis of `__iter__`**: Types that define `__next__` but not `__iter__` automatically get `__iter__` synthesized, returning `self`. This matches Python's convention where iterators are their own iterables.

**Built-in `Iterable[T]` conformance**: All standard container and string types conform to `Iterable[T]`: `list[T]`, `Array[T, N]`, `Span[T]`, `Span[readonly[T]]`, `Range[T]`, `str`, `String`, `StrView` (as `Iterable[char]`), `dict[K,V]` (as `Iterable[K]`), `dict_keys`, `dict_values`, `dict_items`. This enables passing any builtin container to generic functions accepting `Iterable[T]`, calling `__iter__()` explicitly, and using the `iter()` builtin. Direct `for` loops over these types still use fast range-based C++ iteration (`NativeIterable`) as an optimization.

#### Working: `__span__` Protocol (Zero-Cost User-Defined Iteration)

Types that define `__span__(self) -> Span[T]` or `__span__(self) -> Span[readonly[T]]` get zero-cost
C++ range-based for iteration, avoiding iterator object allocation:

```python
from tpy import int32, Span

class IntBuffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [10, 20, 30]

    def __span__(self) -> Span[int32]:
        return self._data

buf = IntBuffer()
for x in buf:       # compiles to: for (int32_t x : buf.__span__()) { ... }
    print(x)
```

**Implicitly readonly**: `__span__` is in `IMPLICIT_READONLY_METHODS`, so it can be called from
readonly contexts (e.g., inside `__getitem__` which is also implicitly readonly). This enables
patterns like `self.__span__()[start:stop]` inside overloaded `__getitem__(slice)`.

**Dual overloads**: When `__span__` is decorated with `@auto_readonly`, the compiler
generates two C++ overloads -- a non-const overload (mutable body) and a const overload
(const body). The const return type is specified via explicit `auto_readonly[T]`
annotation on the element type: `Span[auto_readonly[T]]` produces `std::span<T>` for
the mutable overload and `std::span<const T>` for the const overload. This applies uniformly
to all reference types (`Span`, `Ptr`, `SpanIter`, user-defined generics) -- no special cases.
When `__span__` returns `Span[readonly[T]]`, only a single const overload is generated (no
`@auto_readonly` needed). The same pattern applies to `__iter__()`.

**Implicit dual overloads for `__deref__`, `__getitem__`, `__span__`**: when one of these
three dunders has a reference-typed return, the compiler synthesizes the dual mutable/const
overload pair automatically (`IMPLICIT_AUTO_READONLY_METHODS`). Plain `def __span__(self) -> Span[T]:`
without any decorator produces both halves. Value-typed returns stay single-overload.
Use `@readonly` to opt back into a strict const-only contract.

**Usage-dependent receiver const-ness**: a parameter read only *through* an `@auto_readonly`
accessor (`Box.get`, `Rc.get`, `Deref`) keeps a `const` receiver -- `def read(o: Outer) -> int32: return o.b.get().v`
emits `const Outer& o`. A mutation through the result (`o.b.get().v = 9`, `o.b.get().bump()`)
demotes it to `Outer&`. C++ overload resolution picks `get()` vs `get() const` from the
receiver. (Binding the result to a local, `x = o.b.get()`, keeps the receiver mutable for now;
see `docs/READONLY_DESIGN.md`.)

Explicit `@auto_readonly` is still required for other methods that want dual overloads (e.g.,
`__iter__`, plain `get()` accessors, or user-defined non-dunder accessors):

```python
from tpy import Span, SpanIter, auto_readonly

class MyType:
    # No decorator needed -- __span__ in IMPLICIT_AUTO_READONLY_METHODS.
    def __span__(self) -> Span[T]: ...

    @auto_readonly
    def __iter__(self) -> SpanIter[auto_readonly[T]]:
        return SpanIter(self.__span__())
```

**Requirement**: User types must define `__iter__()` to be iterable in `for` loops. Types with
only `__span__()` and no `__iter__()` are not iterable. Span-backed types should define
`__iter__(self) -> SpanIter[T]` wrapping `self.__span__()` for both iteration and CPython
compatibility.

**Implicit coercion**: A type with `__span__()` coerces to `Span[T]` or `Span[readonly[T]]`
when passed as a function argument. `Span[readonly[T]]` return cannot coerce to mutable `Span[T]`.

**CPython compatibility**: `__span__` is not meaningful in CPython. Span-backed types should
define `__iter__(self) -> SpanIter[T]` (returning `SpanIter(self.__span__())`) to provide
CPython-compatible iteration. `SpanIter[T]` is a builtin type in the `tpy` module that wraps
a span and implements `Iterable[T]`/`Iterator[T]`/`NativeIterable[T]`.

#### Working: `Spannable[T]` Protocol

`Spannable[T]` is a readonly protocol for types that expose contiguous storage via
`__span__()`. It enables writing generic functions that accept any span-producing type:

```python
from tpy import int32, Span, Spannable

class Buffer:
    _data: list[int32]
    def __init__(self) -> None:
        self._data = [1, 2, 3]
    def __span__(self) -> Span[int32]:
        return self._data

def sum_all(c: Spannable[int32]) -> int32:
    total: int32 = 0
    for x in c:
        total += x
    return total

# Works with user types, list, Array, Span, Span[readonly[T]]
print(sum_all(Buffer()))           # 6
data: list[int32] = [10, 20, 30]
print(sum_all(data))               # 60
```

**Conformance**: Any type with `__span__() -> Span[T]` or `__span__() -> Span[readonly[T]]`
structurally conforms. Builtins (list, Array, Span, Span[readonly[T]]) also conform
via explicit `extends` declarations. User types implementing `__span__() -> Span[T]` satisfy
the readonly protocol via covariant return (the compiler auto-generates a const overload).

**Coercion**: `Spannable[T]` values coerce to `Span[readonly[T]]` (not mutable `Span[T]`).

**CPython mixin**: In CPython stubs, `Spannable` is a base class that auto-generates
`__iter__` from `__span__()`. User types inheriting it get iteration for free in CPython.

**Bounded type parameters**: `Iterator[T]` and `Iterable[T]` can be used as type parameter bounds:

```python
from typing import Iterable
from tpy import int32

def sum_generic[T: Iterable[int32]](items: T) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total
```

**Structural protocol-to-protocol conformance**: `Iterator[T]` structurally satisfies `Iterable[T]` because Iterator's method set is a superset of Iterable's. This enables passing iterators where iterables are expected (e.g. `list(some_iterator)` matches the `Iterable[Own[T]]` constructor) without explicit inheritance declarations.

**For-loop support**: `for x in expr` works when `expr` has type `Iterator[T]` (direct `for(;;)` loop calling `__next__()` and checking `has_value()`) or `Iterable[T]` (calls `__iter__()` first, then iterates the resulting iterator). All iterators use the direct `std::expected` loop path. **Reference semantics are preserved**: for non-value types (records, containers), the loop variable is a reference to the original container element, so mutations are visible in the container -- matching CPython behavior. This is implemented via `val_or_ref<T>` in `native_iterator.__next__()`, which stores non-value types by pointer and value types by value. Inside a resumable frame (a multi-yield generator or an `async def`) the loop variable becomes a frame field, and the guarantee holds there too -- including for a *generic* element (`Iterable[T]` on a generic generator), where whether `T` is a value or a reference type is settled only when the C++ template instantiates. How the field gets there depends on what the iteration source can promise. Over a container (`list`/`dict`/`set`, or a direct `Iterator[T]`) the source always lends a real element, so the field is simply a borrow (`T*`) -- except when the source is const-rooted (a `readonly` container), where that emit does not yet compile and the element falls back to a copy (see BUGS.md). Over an arbitrary `__iter__`/`__next__` source -- a protocol-typed parameter, a user-defined iterable -- it may equally lend an element or return a fresh one, and the two are indistinguishable in the compiler, so the field is spelled from the source (`tpy::frame_slot<tpy::for_elem_next_t<Src>>`) and C++ resolves it: a lent element aliases, a fresh one is owned. A concrete value element keeps a plain field, since copying it is the only correct storage. One case still diverges, and it is not the loop variable's storage: `async for` over a manual `__aiter__`/`__anext__` iterator copies the iterator *source* into the frame, so the loop cannot mutate the object it iterates (BUGS.md). The generic *yield* does not diverge that way: a generic generator lends what it yields wherever every yield source outlives a suspension; see the generic-generator entry below for the rule and what it leaves open.

#### Working: Span coercion via `Spannable[T]`

Types extending `Spannable[T]` (which provides `__span__() -> Span[readonly[T]]`) can be implicitly coerced to `Span[T]`. This unifies span coercion with the `Spannable` protocol -- no separate marker protocol is needed.

```python
from tpy import int32, Span, Array

def sum_span(values: Span[int32]) -> int32:
    total: int32 = 0
    for v in values:
        total += v
    return total

# All these work - Array, list extend Spannable[T]
arr: Array[int32, 3] = [1, 2, 3]
sum_span(arr)  # OK

lst: list[int32] = [4, 5, 6]
sum_span(lst)  # OK

# str does NOT extend Spannable - this is an error
# s: str = "hello"
# takes_span(s)  # ERROR: str does not extend Spannable
```

**Key points:**
- **Built-in conformance**: `list[T]`, `Array[T, N]`, `Span[T]` extend `Spannable[T]`
- **str excluded**: `str` iterates over `char` but doesn't extend `Spannable` (design choice)
- **Zero overhead**: Uses C++ `std::span` implicit construction from contiguous ranges

#### Compiler Traits Summary

Protocols serve as **compiler traits**—letting the compiler discover type capabilities without hardcoded type checks:

- `len(x)` works on any type conforming to `Sized` ✓ (working)
- `Sequence[T]` for types supporting `len()` and indexing ✓ (working)
- `for` loops work on `Iterable[T]`-typed parameters ✓ (working)
- `for` loops work on `NativeIterable[T]`-typed parameters (typically via
  `Iterable[T]` + `isinstance(x, NativeIterable)` narrowing) ✓ (working)
- `for` loops work on `Iterator[T]`-typed parameters ✓ (working)
- Implicit coercion to `Span[T]` works on types extending `Spannable[T]` ✓ (working)
- `for` loops work on `Iterator[T]`-typed parameters ✓ (working)
- `list.extend()`, `str.join()`, `list()`, `dict()` accept `Iterable[T]` params ✓ (working)

See [docs/PROTOCOL_DESIGN.md](PROTOCOL_DESIGN.md) for the full design, including implementation phases and C++ codegen strategies.

### Runtime Type Discrimination (isinstance / match / cast)

Three surfaces ask "what concrete type is behind this value at runtime?" They share one
mechanism layer; the per-case rules live in the sections below (Union/Optional, Any) and in
`MATCH_CASE_DESIGN.md`, `DYNAMIC_PROTOCOL_DESIGN.md`, `ANY_TYPE_DESIGN.md`.

TPy has no general RTTI: runtime type identity exists only where the language already pays
for it -- `@dynamic` vtables, union variant tags, and `Any`'s `typeid`. A plain value type
carries no hidden type tag, so it cannot be downcast.

| Subject | isinstance | match | Lowering |
|---------|-----------|-------|----------|
| union value `A \| B` | narrow to member | switch / `holds_alternative` | variant index |
| `Any` | check vs typeid | rejected (subject) | `typeid` / `any_cast_or_panic` |
| polymorphic class / `@dynamic` proto | downcast + narrow | downcast + narrow | `dynamic_cast` / `dyn_adapter_cast` |
| structural protocol | compile-time test | -- | `if constexpr (Concept)` |
| plain (non-polymorphic) class | folds True/False | record-name equality | static fold |
| `Optional[T]` | None-narrow | None-narrow | -- |

By-design limits (consequences of value semantics, not gaps to fill):
- **Plain-class downcast** (`isinstance(base_value, Derived)`) folds to False + warning -- the
  value upcast already sliced the derived fields; use `@dynamic` for runtime type checks.
- **Subscripted-generic targets** (`isinstance(x, list[int])`, `cast(P[T], x)`) are rejected --
  the type arg is erased at runtime (matches CPython's `TypeError`).

Surface split: `isinstance` is a bare-name test-and-narrow usable in any boolean context;
`match` takes arbitrary expression subjects (`match self.pet:` / `match xs[i]:`) and adds
destructuring -- prefer `match` when the subject is not a bare local. `typing.cast(T, x)` is a
no-op for non-`Any` sources and a checked `any_cast_or_panic` when the source is `Any`.

### Union/Optional
- **Working**: Union types `A | B | C` → `::tpy::Union<A, B, C>`
  - **Pointer-variant representation**: non-value unions (containing records) use a two-layer representation for zero-copy semantics:
    - Storage (fields, containers, rvalue slots): `::tpy::Union<Dog, Cat>` (the TPy type that owns Python's comparison rule by value)
    - Reference (params, returns, locals): `::tpy::Union<Dog*, Cat*>` (borrow form, passed by value)
    - Read borrow: `::tpy::Union<const Dog*, const Cat*>` for every NON-MUTATING union parameter -- an explicit `readonly[...]`, a `@readonly` or inferred-readonly method, and a plain free-function / constructor / generator / `async def` parameter the body only discriminates. The const is DEEP (on the pointees): on the by-value union it would freeze the borrow and nothing it points at, and no const-bound source could reach the slot
    - Conversion into that read borrow, by how the SOURCE is bound: a storage-form name (a `list[A | B]` element, an `Own[A | B]` param) takes `::tpy::to_const_ptr_variant(x)`, a mutable borrow takes the type's own `x.as_const()`, and a name already bound with const pointees passes bare -- two helpers and the bare pass, because a narrower union at a wider union slot never reaches a render (it rejects at the argument gate)
    - Conversion out of storage into the mutable borrow: `::tpy::to_ptr_variant()`
    - **Limitation**: a `readonly[A | B]` argument is still rejected at a plain `A | B` parameter (`Cannot pass readonly[A | B] as mutable A | B`) although the two now render the same deep-const borrow -- the readonly rule reads the annotation, not the rendered form (`BUGS.md#readonly-union-arg-at-inferred-const-slot`)
    - Value-type unions (`int32 | str`) use `::tpy::Union<...>` everywhere (no pointers)
  - Two-way, three-way, and n-way unions in annotations (function params, returns, local variables)
  - Canonical member ordering (sorted by type name, `None`/`std::monostate` always first)
  - `A | None` with single non-None type still produces `Optional[T]` (backward compatible)
  - Member type compatibility: `T` assignable to `T | U`, `T | U` assignable to `T | U | V`
  - Which member a value lands in never depends on the canonical order: a typed value takes the member equal to its type, else the fixed width it widens to, before `int` (`int32` at `int32 | int` stays `int32`, `int32` at `int64 | int` becomes `int64`). A literal renders bare and follows the C++ variant's non-narrowing rule: an `int` literal takes `int32` or `int64` when it fits, any narrower or unsigned width loses to `int` (`uint8 | int = 5` holds an `int`); a float literal is a double, so `float` wins over `float32`. Pinned by `union/fixed_int_literal_member` and the union sections of `float/float32_cpy_precision`.
  - `make_union()` normalizes: flattens nested unions, deduplicates, collapses single-type unions
  - **Protocol unions**: all non-None members must be static protocols (2+ protocols, optionally with None)
    - `Sized | Sequence[int]` -- required protocol union, generates template with disjunctive concept constraints
    - `Sized | Sequence[int] | None` -- nullable protocol union, pointer repr with `if constexpr` guards
    - `isinstance(x, Protocol)` dispatches via `if constexpr (Concept<T_x>)` inside protocol union bodies
    - Mixed protocol + concrete types in unions is a sema error
    - `@dynamic` protocols cannot appear in unions (sema error)
    - **Limitation**: `isinstance()` on protocol unions not yet supported in ternary expressions (use if/elif/else statements)
  - Mixed `readonly`/non-`readonly` in unions is a parse error
  - Generic functions returning `T | U` where `T == U` at instantiation produce a sema error (duplicate variant members)
  - `isinstance(x, T)` narrowing in if/elif/else branches: narrows union variable to member type
  - `isinstance(x, (A, B))` tuple form narrows `x` to `A | B`, composes with `and`/`or`/`not` and match-case guards
  - `isinstance(x, T)` where `x` is already narrowed to a concrete non-union type folds to a compile-time bool (no runtime `holds_alternative`), covering nested redundant checks, exhaustive elif tails, and post-assignment-narrowing checks
  - `isinstance(child, Parent)` on user-record inheritance hierarchies folds at compile time by walking `Child`'s base chain. True for same-type and any ancestor; False for unrelated types and downcasts. Downcast checks (`isinstance(parent, Child)`) emit a warning since static dispatch means slicing at the boundary has removed the Child fields -- use `@dynamic` for runtime type checks. `Ptr[T]` folds the same as `T` (static pointee type is authoritative) **unless** the pointee is a `@dynamic` dispatch inner, which is handled by the runtime-dispatch rule below
  - `isinstance(x, C)` on a **generic type-parameter subject** bounded by a *non-polymorphic class* (`def f[T: Dog](x: T)`) lowers to a per-instantiation compile-time trait `::tpy::isinstance_static<C, decltype(x)>()` (`std::is_base_of_v || std::is_same_v`, normalizing ref/pointer/cv). Because a generic body is compiled once but instantiated per concrete type, the C++ compiler constant-folds it per instantiation -- correct for ancestor/equal/descendant checks alike (`isinstance(x, Puppy)` is True when `T=Puppy`, False when `T=Dog`) at zero runtime cost. This is a pure boolean test (no narrowing of `x` to `C`). Subjects the static trait can't resolve soundly are **rejected** with a clear diagnostic rather than silently mis-folded: an *unbounded* `T`, a `T` bounded by a *@dynamic protocol / polymorphic class* (needs `dynamic_cast`), and a *structural-protocol*-bounded `T` (could instantiate to a union / `Any`). The per-instantiation runtime dispatcher that would lift those is tracked in `BUGS.md`
  - `isinstance(p, Subclass)` on dynamic-dispatch sources -- a *dispatch inner* is either a polymorphic class (one transitively inheriting a `@dynamic` protocol) or a direct `@dynamic` protocol. Sources: `Optional[inner]` (lowered to `T*`), `Ptr[inner]` (lowered to `T*`), a bare dispatch-inner parameter (lowered to `T&` -- a polymorphic class or a `@dynamic` protocol borrow), `self` inside an instance method (lowered to `this`; `const T* this` in `@readonly` methods; `&__self` inside an `async def` or generator method body, where the resumable frame holds self as a reference field), an **owning wrapper** (`Box[Pet]` / `Rc[Pet]`) via deref-view narrowing (below), and any form wrapped in `readonly[]`. All lower to a runtime cast. **Structural conformers** of a `@dynamic` protocol are supported: behind a `Pet*` a structural `Cat` is physically an `Adapter<Pet,Cat>` / `RefAdapter<Pet,Cat>`, so the check lowers to `tpy::dyn_adapter_cast<Pet,Cat>` (tries both adapter shapes, projects `.inner`) instead of `dynamic_cast<Cat*>`; codegen picks the cast by `directly_implements_dynamic(checktype, protocol)` (inheritance -> `dynamic_cast`, structural -> `dyn_adapter_cast`). A check type that neither inherits nor structurally conforms is rejected (can never match). The cast input picks the right shape per binding: bare name for pointer-shaped bindings (Optional params, `Ptr`, pointer globals, pointer-locals, imports), `&name` for reference params, `this` (regular method) or `&__self` (async/generator body) for `self`. (Narrowing `self` -- or any polymorphic source / union param -- inside an `if`/`while` block, then accessing subclass-/arm-only state *after* a suspension inside that block, works: the narrowing is re-established at the resume case from facts stamped on the resumable block.) The check accepts the inner type itself and any subclass; tuple form (`isinstance(p, (A, B))`) lowers to an OR of casts. The variable's type is narrowed to the subclass in the true branch via cast-and-cache codegen (`Sub& __var = *dynamic_cast<Sub*>(<src>);` once at branch entry -- a structural conformer uses `*tpy::dyn_adapter_cast<Base,Sub>(<src>)` instead; subsequent reads route through the typed reference). Subclass-only field access works (`e.code`), inherited methods still virtual-dispatch through the cached typed view. Tuple form does not narrow (no single subclass type to read through). Const-ness of the cast matches the source's readonly status -- `readonly[T]` / `readonly[Optional[T]]` params and `@readonly` methods (where self is `const T* this`) all emit `const Sub*` casts
  - **Deref-view narrowing** (owning wrappers `Box[Pet]` / `Rc[Pet]`): `isinstance(rc, Dog)` narrows the *polymorphic payload* reached through the wrapper's reference-returning `__deref__`, not the wrapper variable. In the true branch `rc.bark()` (a `Dog` method, not on the `Pet` protocol) resolves against `Dog` -- codegen casts the deref payload pointer `&(rc.__deref__())` -- while `rc.clone()` stays an `Rc` method (the wrapper's own type is never narrowed). Structural conformers compose through the deref too (via `dyn_adapter_cast`). A non-owning handle (`Weak[Pet]`) has no deref view and is rejected with an `.upgrade()`-first hint. (Wrapper isinstance diverges from CPython, which checks the wrapper's own type -- tests need `no_cpython.txt`.)
  - `assert isinstance(x, T)` narrowing: `std::get<T>` extraction persists for the rest of the scope
  - Early-return narrowing: `if isinstance(x, T): return ...` (or `raise`) with no else block extracts the narrowed value at the outer scope so code after the guard uses the narrowed type. Covers plain unions narrowed to a single remaining member (`auto& __x = *std::get<B*>(x)` -- a `A | B` whose `A` arm returned), recursive-union variants (`std::get<T>` extraction), and polymorphic-class facts on `Optional[Polymorphic]` / bare polymorphic / `readonly[]` sources (`Sub& __x = *dynamic_cast<Sub*>(<src>);`). The negative-guard form (`if not isinstance(p, Sub): return`) is the natural shape for the polymorphic case
  - Compound conditions: `isinstance(x, T) and x.field > 0` narrows `x` on the RHS of `and`
  - `isinstance(x, T) or x.other_field > 0` narrows `x` to remaining members on `or` RHS
  - Negative (else-branch) narrowing: remaining union members after isinstance check
  - Chained elif isinstance for multi-way branching (3+ member unions) -- full codegen support for 2-way, 3-way, and n-way elif chains
  - Narrowed variables can be used for field access, method calls, and passed to functions expecting the member type
  - ... and passed on where the UNION itself is expected (`if isinstance(v, A): total(v)` with `total(u: A | B)`): the narrowed binding is the member-typed extraction alias, so a pointer-variant slot re-lifts its address (`::tpy::Union<A*, B*>{&(__v)}` -- the same lift a plain member-typed name takes, and the address-of the union RETURN position already emits). The callee therefore aliases the caller's object, matching CPython. Covers the isinstance, `match`-arm and inline-ternary narrowings, mutable and `readonly[...]` slots, and free-function / constructor / method-body / module-level / comprehension / `with` / `try`-`finally` / `@error_return` / generator / async positions -- a generator or async frame re-establishes the alias per resume state, so the lift renders on both sides of a `yield` or `await`. A VALUE union (`int32 | float64`) takes the same fact through its own form: the narrowed name is the member-typed alias and the variant's converting constructor absorbs it, so the arg passes BARE with no `std::variant<...> __tmp_N` temp (the `if` and `match` forms; the inline-ternary form of a value union still rejects). Two value-union shapes stay out: the inline-ternary narrowing (BUGS.md#inline-narrowed-value-union-arg-rejects) and a `str` member, which never reaches the bare-pass row at all -- the narrowed alias is a `std::string_view` while the variant member is an owned `std::string`, so the arg is rejected outright rather than mis-rendered (BUGS.md#value-union-str-view-insert). A METHOD's pointer-variant parameter has no member-lift row (loud reject). A `lambda` capturing the narrowed name binds the ALIAS in its capture list, and the two lanes differ by what that entry takes: the non-escaping `Fn` lane binds it BY REFERENCE, so the closure aliases the subject and reads a mutation made after it was built, while the ESCAPING (`Callable`) lane would take it by value -- over a REFERENCE-typed narrowed member that turns the un-narrowed capture's alias into a silent snapshot, so it rejects with a located `expr.lambda` (BUGS.md#escaping-capture-of-narrowed-subject). A VALUE-typed narrowed member snapshots exactly as the un-narrowed union capture would, so it compiles on both lanes. A poly-narrowed subject, read as `(*__p_ptr)`, still rejects on either lane; a nested def capturing the narrowed name does not lower at all -- see BUGS.md
  - Implicit union wrapping at call sites: passing `A` to a parameter of type `A | B` auto-wraps into `std::variant`, including rvalue constructors (e.g. `f(Dog("Rex"))` materializes a temp slot for the rvalue)
  - `std::get<T>` extraction emitted once at block entry for efficient narrowed access
  - `while isinstance(x, T)` narrows `x` to `T` inside the loop body (same extraction as if-blocks)
  - Assignment narrowing: `v: A | B = A(...)` narrows `v` to `A` so field access works without isinstance; uses inline `std::get<T>()` at access points (not aliased, so `v` can still be passed to functions expecting the full union)
  - Assignment narrowing is cleared on reassignment (`v = B(...)` clears the `A` narrowing)
  - Value-type semantics: all-value unions (`int | bool`) pass as `const&`; unions with records use the borrow form `::tpy::Union<T*...>` (by value, zero-copy)
  - Nullable unions: `A | B | None` maps to `::tpy::Union<A, B, std::monostate>` at a storage position, `::tpy::Union<A*, B*, std::monostate>` at a borrow one
  - **Comparison is BY VALUE, across alternatives** (CPython semantics): `same(a: int32 | float64, b: int32 | float64)` returning `a == b` answers True for `same(1, 1.0)`. The type carries the rule, not the compiler: every union at a STORAGE position renders `::tpy::Union<...>` -- a value union everywhere, a REFERENCE union at a field, a container element, an `Own` slot or a return -- a `std::variant` that declares all six comparison operators over a per-alternative-pair leaf, so the compare emits the bare `(a == b)` at every position. The leaf is the monomorphic twin's answer for that pair -- a mixed-sign integer pair goes through `std::cmp_equal` -- so a union never disagrees with the same comparison written on plain variables, with one exception: an INTEGER against a float compares EXACTLY (the float is decomposed, as CPython does, so `2**53 + 1` differs from `2.0**53`) where the twin still rounds the integer to a double (`BUGS.md#int-float-compare-rounds-bigint`). That holds for `int` and for every fixed width, at no allocation cost -- the exception is the leaf being RIGHT, so the monomorphic side is what must move. Because the ELEMENT compares correctly, every container does too with no help: `list`, `Array`, `tuple`, nested combinations and `dict` values all answer Python's way through their own standard operators. The recursive-alias wrapper's defaulted `operator==` compares a `::tpy::Union` member and inherits the same rule. A REFERENCE union's container compare inherits it too: `list[Dog | int32 | float64]` holding 1 equals one holding 1.0, where the bare variant compared the alternative INDEX first and answered False. A reference union at a BORROW position -- a parameter, a local, a comprehension or a generator frame -- answers through its own type, `::tpy::Union<A*, B*>`, whose per-alternative-PAIR leaf recurses into the same value leaf ON THE POINTEES and falls back to IDENTITY for a same-type pair whose type defines no `__eq__`. That fallback is Python's own, and it is representable only there: a storage slot holds a COPY, so its address is not the object's, and such a pack stays a build-time refusal naming the missing `__eq__`. Both operands must be BOUND as the borrow form -- a `for e in xs` / comprehension loop variable binds the container's storage element, which is a different C++ type, and that pair still rejects (`BUGS.md#ref-union-loop-var-vs-borrow-compare`). An ordering whose alternative pair Python cannot order (`int32 | str` holding an int against a str) raises `TypeError` at runtime, as CPython does; a pair of one type that defines no equality at all does not compile. At the type level a union still has no `Equatable` / `Hashable` conformance, so a union against one of its own members, `in` over `list[union]`, and a union dict key are all rejected (see `BUGS.md#value-union-no-equatable-conformance`)
  - `v is None` / `v is not None` on nullable unions: `std::holds_alternative<std::monostate>(v)`
  - `is not None` narrows to remaining non-None members; chained isinstance further narrows
  - Field assignment: `obj.field = local` where field is value-variant and local is pointer-variant auto-converts via `::tpy::to_value_variant()` (copies the active member into field storage, emits copy warning). The wrap is gated on the source being structurally a pointer-variant (ptr_variant local, union param, function call returning a non-value union) -- bare alternative sources (constructor `A(1)`, field access of a value-variant field, etc.) construct the value-variant directly and skip the wrap.
  - `None` assignment to nullable union field: `obj.field = None` emits `std::monostate{}` (not `nullptr`). Holds even after the field is narrowed to non-None earlier in the same scope (e.g. `if self.f is None: return; ...; self.f = None`) -- the write uses the declared field type, not sema's narrowed view.
  - `copy()` on pointer-variant union locals: variant-aware deep copy via `::tpy::to_value_variant()`, preserving the full union type even when narrowed
  - `v == None` / `v != None` on nullable unions errors with hint to use `is`/`is not`
  - Type aliases: `Shape = Circle | Rect` (old-style assignment) and `type Shape = Circle | Rect` (Python 3.12 `type` statement)
  - Aliases resolve eagerly at parse time to the underlying union type; sema and codegen see the expanded type
  - Old-style aliases support forward references (alias before class definitions)
  - Aliases can include `None` members: `MaybeShape = Circle | Rect | None`
  - Emits C++ `using Shape = std::variant<Circle, Rect>;` in the header
  - `isinstance(x, T)` against a union narrows to the matching member. `T` may be a concrete member type, a tuple `(A, B)`, an **inline union** `A | B` (`isinstance(x, A | B)` -- a `types.UnionType` at runtime, which CPython accepts), or a **bare container generic** (`isinstance(d, dict)` / `list` / `set` -- the CPython spelling), which narrows to that member (e.g. `dict[str, JsonValue]`). A bare generic matching two members of the same kind (`list[int] | list[str]`) is rejected as ambiguous. A **PEP 695 `type`-alias name** (`type Shape = A | B; isinstance(x, Shape)`) is rejected -- CPython rejects a `TypeAliasType` at runtime in every version (intentional), so the diagnostic points at the inline-union / tuple spellings. The *parameterized* form `isinstance(d, dict[str, V])` (or an alias of it) is likewise rejected, matching CPython's runtime "cannot be a parameterized generic". A `None` operand in an inline union (`A | None`) is not yet supported -- narrow None with `x is None` (see `BUGS.md`). An annotated assignment of an `Own[union]` result auto-moves into the union local (`d: JsonValue = json.loads(s)`).
  - Type aliases can be imported cross-module: `from shapes import Shape`
  - **Recursive type aliases**: `type Tree = int | list[Tree]` -- self-referencing union aliases compile to a C++ wrapper struct with a `.value` variant field and a forwarding constructor. Safety: every recursive path must go through an indirecting container. The compiler treats two sources as indirecting: (a) `Optional`/`Ptr` structural wrappers; (b) types declared with `@native(..., indirecting=True)` (covers `list`/`dict`/`set` plus user opt-ins). User TPy records (e.g. `tplib.Box`) auto-deduce indirection structurally -- any record whose field set contains a `Ptr`-typed (or otherwise indirecting) field breaks the cycle without compiler-side hard-coding. Direct recursion (`type Bad = int | Bad`) and fixed-size recursion (`type Bad = str | tuple[Bad, int]`) are rejected. A union naming `None` directly (`type Nest = None | list[Nest]`) is rejected at parse time as well, although the generic spelling of the same type -- `Tree[None]` for `type Tree[T] = T | list[Tree[T]]` -- compiles and runs; the two spellings disagreeing is a known gap, see `BUGS.md`. The alias name is callable as a constructor: `Tree(42)`. isinstance narrowing and match/case work on recursive unions. A recursive union is usable as a record field, constructor argument, and function / method / structural-protocol-method parameter; the parameter's C++ const-ness is inferred like a record param (read-only use -> `const X&`, returned-by-reference or mutated -> `X&`), so `def wrap(v: V) -> V: return v` works without a `readonly[V]` annotation. Returning a non-generic recursive union by value from a *record method* is a known codegen-ordering gap -- see `BUGS.md`; free-function returns work.
  - **Annotation-driven inference**: Nested list and dict literals infer their element types from the target annotation. `x: Tree = [1, [3, 4]]` infers `[3, 4]` as `list[Tree]` rather than `list[int]`; `d: JsonValue = {"a": 1, "b": {"c": 2}}` infers the inner dict as `dict[str, JsonValue]`. Works with variable annotations, `list[Tree]`/`dict[str, JsonValue]` annotations, and function parameter types. Supports arbitrary nesting depth.
  - **Generic non-recursive aliases**: `type Pair[T] = tuple[T, T]`, `type Result[T, E] = T | E`, `type DictView[K, V] = dict[K, V] | None`. Substitution happens at parse-resolution: a use site `Pair[int32]` is rewritten to the expanded body (`tuple[int32, int32]`) for sema and codegen. The alias name has no C++-level identity; generated C++ uses the expanded form directly. Works in every position (local, parameter, return, field, container element, nested `Pair[Pair[T]]`, alias-of-alias composition `IntPair = Pair[int32]`). Cross-module imports work in both short-name (`from lib import Pair`) and qualified (`lib.Pair[int32]`) forms. Diagnostics: arity mismatch (`Pair[A, B]` for a single-arg alias) and PEP 695 bound syntax (`Pair[T: Hashable]`) are rejected at parse time. `isinstance(x, Pair)` on a bare generic alias is rejected -- generic aliases have no runtime identity; narrow against the expanded members instead.
  - **Generic recursive aliases**: `type Tree[T] = T | list[Tree[T]]`, `type DictTree[K, V] = V | dict[K, DictTree[K, V]]`. Unlike non-recursive generic aliases (expanded at use sites), a generic *recursive* alias has C++-level nominal identity: codegen emits one `template<typename ...> struct Tree { std::variant<...> value; ... };` per alias name, and a use site `Tree[int32]` renders `Tree<int32_t>`. The default ctor and `operator==` are `requires`-constrained so an instantiation with a non-default-constructible / non-comparable arg loses just that member (clean use-site error) rather than an ill-formed struct. v1 supports **identity recursion only**: each recursive position must reuse the alias's declared type parameters positionally (`Tree[T]`, not `Tree[list[T]]`, `Tree[int]`, or swapped `Pair[V, K]`) -- violations are rejected with a precise diagnostic. Construction (`t: Tree[int] = [1, [2, 3], 4]`), `match`/`case` dispatch (`case list()` + `case _` for the leaf), recursion, equality, and printing all work; multiple instantiations of one alias (`Tree[int]`, `Tree[str]`) share the single template. Cross-module use works via all import forms -- `from m import Tree`, aliased `from m import Tree as T2`, and qualified `import m; m.Tree[...]`; a local alias and a same-short-named imported one stay distinct (identity is keyed by the defining module's qualified name, so the renders never collide). A generic recursive alias instance is usable as a record field, constructor argument, and method / structural-protocol-method parameter and return (the protocol must be defined in a *separate* module from the alias -- a same-module protocol, structural or `@dynamic`, whose method is typed with the alias currently fails the C++ build because the protocol's concept / base / adapter is emitted before the wrapper struct; see `BUGS.md`). **Return convention** (reference-type, matching `list` / `dict` / record): a bare `-> Tree[T]` return lowers to `Tree<T>&`, so a fresh value must be returned as `Own[Tree[T]]` (lowers by value / move) -- returning a fresh value bare is rejected at compile time. A field / parameter accessor (`def get(self) -> Tree[int]: return self.t`) returns the reference directly; `readonly[Tree[T]]` returns `const Tree<T>&`. A wrapper parameter's const-ness is inferred like a record param (read-only use -> `const Tree<T>&`; returned-by-reference or mutated -> `Tree<T>&`), so a wrapper param can be returned by reference (`def f(e: Tree[T]) -> Tree[T]: return e`). **Limitations**: use `match`/`case` for dispatch -- `isinstance(x, Tree)` on the recursive form is rejected (generic aliases have no runtime identity) and bare `isinstance(x, list)` narrowing does not reach a *generic* recursive-alias subject (its isinstance path stays on the non-union branch; bare-generic narrowing does work for plain and non-generic recursive unions). Matching/binding the bare type-parameter leaf needs a concrete-leaf class pattern (`case int()`) -- but only where the leaf is a single type: when the type argument is itself a union (`Tree[int32 | str]`), no member of that leaf union can be named in a class pattern, because the wrapper's variant carries one slot for the whole leaf rather than one per member (see `BUGS.md`). A fully generic `[T]` traversal uses `case _` for the leaf (no value bound). Generic-class constructor inference through a nested `Box[Tree[T]]` arg (annotate explicitly) and mutual recursion across generic aliases (rejected) are follow-ups; see `BUGS.md` and `docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md`. The non-generic recursive union case (`type Expr = Lit | BinOp`) follows the same return and param conventions: `def f(e: Expr) -> Expr: return e` works (the param const-infers to `Expr&` because it is returned by reference), and a self-mutating method returning the param compiles too. **Locals & `match` subjects bind by reference (no copy, aliasing works):** a wrapper local from a reference source (`v = h.view()`, `g = h.get()`) binds `Tree<T>&` (not a copy of the variant tree), and a `match h.get()` subject binds by reference -- so reads avoid the copy and caller mutations through the local / `match` arm reach the field (CPython aliasing), exactly like `list` / `dict` / record. A fresh value (`t = [1, 2]`) still binds by value.
  - **Mutual recursion** (D20): Union aliases can reference classes whose fields reference back through any indirecting container (`list`/`dict`/`set` via the `@native(indirecting=True)` flag; `Optional`/`Ptr` structurally; user records like `tplib.Box` that own a `Ptr`-typed field). This enables AST-style data structures:
    ```python
    from tplib import Box
    type Expr = Lit | BinOp
    class Lit:
        value: int
    class BinOp:
        left: Box[Expr]
        right: Box[Expr]
    ```
    The compiler detects cross-type cycles, validates indirection, and generates a C++ wrapper struct that can be forward-declared. `Box(Lit(1))` auto-coerces to `Box[Expr]` via the wrapper's implicit constructor. Works with `isinstance`, `match`/`case`, and mixed unions (primitives + records). Both source orderings supported (alias first or classes first). Cross-module mutual recursion is not yet supported.
  - **Cross-module argument coercion**: a function whose parameter is a recursive-union alias defined in another module (e.g. `json.dumps(obj: JsonValue)`) accepts a container *literal* without the caller importing the alias -- `json.dumps([1, 2, 3])` / `json.dumps({"a": 1})` work from `import json` alone, including empty `[]`/`{}`, `None` elements, and arbitrary nesting (`json.dumps([1, None, {"k": None}])`). A concrete container *variable* (`d: dict[str, int32]`, `xs: list[int32]`) is **intentionally NOT** implicitly converted: it has a different C++ representation (`ordered_map<string, int32_t>` vs the wrapper's `ordered_map<string, JsonValue>`) that cannot alias, so accepting it would mean a silent element-wise **O(n) deep copy** at the call boundary -- a hidden cost TPy declines to insert (and an aliasing reference type would also diverge from CPython's pass-by-reference). The compiler rejects it with a diagnostic pointing at the explicit alternatives: build it as the alias directly (`d: JsonValue = {...}`) or pass a container literal. This is a deliberate design choice, not a missing feature; the zero-copy answer is to serialize the typed container in place (planned -- see `TODO.md`), which avoids the wrapper entirely. See `BUGS.md` for the tuple input limitation.
  - **Limitations**: Non-generic mutual type-alias recursion (`type A = list[B]; type B = list[A]`) is silently accepted by the compiler but generates C++ that fails to compile -- each `using` declaration references the other before it is defined. Use a recursive-union wrapper (as in the `Expr` / `Lit` / `BinOp` example above) for mutually-recursive types instead.
  - **Working**: Recursive *records* through an indirecting container -- a class whose field cycles back to itself (or mutually, `A`/`B`) via `list`/`dict`/`set`/`Ptr`/`Rc[T] | None` compiles (a tree node `children: list[Node]`; `A.bs: list[B]` + `B.as_: list[A]`). The container provides the size indirection the cycle needs. Storing a borrowed value into such a field copies it into the container (the standard value-into-container copy warning); for shared mutation use `Ptr`/`Rc`. The **inline** self-embedding forms -- a direct `nxt: Node` field, or `opt: Node | None` (an inline `std::optional<Node>`, which stores `Node` by value) -- are infinite-size and not yet cleanly rejected (they crash or emit invalid C++; see `BUGS.md`); use a container, `Ptr`, or `Rc` to break the cycle.
  - **Not yet supported**: `isinstance(x, Protocol)` on concrete-typed variables
  - **Working**: `match`/`case` pattern matching on union subjects (see Control Flow > Other)
  - **Working**: Overloading, with two decorators that name the two forms. **`typing.overload`** is CPython's shape: bodyless stubs (`...` or `pass`) declare the signatures, one trailing implementation of the same name carries the body, and the compiler specializes that body per stub via dead-branch elimination. **`tpy.dispatch`** (`from tpy import dispatch`) is TPy's own: every same-named variant is its own implementation -- a Python body, a `@native` binding or a `@cpp_template` -- and the set has no trailing implementation. Both register one overload set and share call-site resolution; each overload compiles to a specialized C++ function with no runtime dispatch. The decorators never mix under one name: a `@overload` stub with a body or a `@native` binding, a `@dispatch` variant without either, a `@dispatch` set followed by an implementation, or a `@overload` set without one are all compile errors that name the other decorator.
    - Works for free functions and methods, including cross-module imports
    - A free-function overload set may mix generic and non-generic overloads under one generic impl (e.g. `@overload pick[T](xs: list[T])` alongside `@overload pick(xs: str)`); each overload emits a C++ definition per its own type params, and a template overload (e.g. a `Iterable[Own[T]]` protocol-param stub) is instantiable across module imports. Methods are more restricted: a generic overloaded method is rejected (`Overloaded generic methods are not supported`), and a method whose overload impl param is a `Protocol | Concrete` union is rejected (`Cannot mix protocol types ... in a union`) where the same shape on a free function is accepted -- see TODO/BUGS for the parity gaps.
    - A free-function overload set whose impl is a generator (the body has `yield`, return type `Iterator[T]`) is supported: the impl's yield type is derived from its `Iterator[T]` return even though the impl is not separately registered. The same shape on a *method* is not yet supported (see BUGS/TODO).
    - `@overload`: stubs require `...` (Ellipsis) or `pass` body. Stub parameter names must prefix-match impl parameter names.
    - `@overload` arity variation: stubs may have fewer parameters than the implementation; every missing trailing impl parameter must have a default. Each short-arity stub emits a C++ function taking only the stub's params, with the omitted impl params emitted as locals initialized to their defaults at the top of the body. When the default's type narrows the impl param (`None` for `Optional[T]`, literal for non-union), dead-branch elim strips the non-matching paths. Short-arity stubs disallow keyword-only, `*args`, or `**kwargs` on the impl.
    - `@dispatch`: each variant has its own body, or is a `@native` / `@cpp_template` binding; bodied and native variants coexist in one set. Motivating use case: `math.log(x)` is a checked native binding, `math.log(x, base)` is a bodied variant that delegates to the single-arg form. The whole builtin stub surface (`bytes.__init__`, `unsafe_ptr`, ...) is written this way.
    - Stub parameter types must be subsets of the implementation's union members (`@overload`)
    - Exhaustiveness check: stubs must cover all union variants per parameter when all stubs include that parameter (missing variants are a sema error). Short-arity stubs that skip a parameter are covered by the impl's default.
    - `isinstance(x, T)` checks in if/elif/else are statically resolved to `true`/`false` per overload
    - `match`/`case` on union subjects selects only the matching arm per overload
    - Call-site overload resolution (the same for both decorators) is tier-ranked in two passes (`sema/overloads.py`, see `docs/OVERLOAD_DESIGN.md#call-resolution` for the full algorithm). First pass: each candidate classifies every arg into a `(tier, widening_cost)` pair, strongest-first tiers being `EXACT_CONCRETE > EXACT_GENERIC_SHAPE > PROTOCOL_EXPLICIT > PROTOCOL_STRUCTURAL > GENERIC_PROTOCOL_EXPLICIT > GENERIC_PROTOCOL_STRUCTURAL > GENERIC_WILDCARD`; candidates are sorted by aggregate tier counts with widening cost as the tiebreaker, and genuine ties become an `Ambiguous overload for 'f': ...` diagnostic rather than a declaration-order pick. Second pass (coercion fallback) runs only when no strict match exists. Concrete overloads always rank above equally-matching generics, so stub order cannot change the winner. Empty-container literals carry an `UnknownElementType` placeholder for their element type; ranking against multi-overload builtins (e.g. `sum([])`) treats it as `default_int_type` so `sum([]) == 0` picks the `int32` overload at cost 0. Single-generic builtins (`sorted`, `all`, `any`, `iter`, `enumerate`, `reversed`) and user-defined generics use a different mechanism -- see `@type_param_default` below.
    - `Literal["r", "w", ...]` parameter annotations for literal-value-based dispatch: `open(path, "rb")` can resolve to a different return type than `open(path, "r")`. Supports string, integer (including negative), and bool values. Multiple values per `Literal[...]` annotation supported. Mixed value types in a single `Literal[...]` are rejected. Literal arguments and `Literal`-annotated locals dispatch to `Literal` overloads (`x: Literal["rb"] = "rb"; pick(x)` picks the Literal specialization, and the dispatch survives branch joins where every assigned value stays in the declared set); unannotated variables fall through to plain type overloads. Out-of-set assignments to a `Literal`-annotated local are rejected at compile time (`m: Literal["r", "w"] = "wb"` and the reassignment counterpart both error). `Literal[str]`-annotated locals also use view-storage (`std::string_view`) when every bound value is a string-literal AST node -- view inference widens to owned `std::string` when a Literal-returning function call (or other non-view-safe Literal-typed source) is bound. Non-Literal-typed RHS (`m: Literal["r", "w"] = some_str_func()`) is rejected at compile time: sema cannot prove the runtime value is in the declared set, and accepting it would miscompile via Literal-specialized dispatch. Augmented assignment on a `Literal[...]`-annotated local (`m += "x"`) is rejected outright -- the result is rarely in the declared value set and the local's view storage couldn't hold a new owned string anyway. `Literal[str]` in return position also emits `std::string_view` (every value is a static-lifetime literal), so callers binding the return into a `Literal[...]` local get view storage end-to-end with no heap allocation. Equality narrowing on `Literal`-typed parameters: `if mode == "rb":` narrows to `Literal["rb"]`, enabling dispatch to more specific stubs within branches. `match`/`case` on `Literal`-typed parameters with exhaustiveness checking and subject narrowing per arm. Literal overload flattening: each stub gets a per-literal C++ specialization with name mangling and dead branch elimination, enabling different return types per literal value for both functions and methods. Multi-value dead branch elimination: `or`/`and` chains (`mode == "r" or mode == "w"`) and `in`/`not in` operators (`mode in {"r", "w"}`) are folded when all values of a multi-value `Literal` are covered or contradicted. Requires `from typing import Literal`.
    - **Not** supported on `__init__` of a user-defined class: codegen emits a single constructor, so an overloaded `__init__` on a `@dataclass`/class silently keeps only the first stub (a known bug, see BUGS.md). Use a single constructor or a classmethod factory. (The builtin exception family overloads `__init__` via `@dispatch` + `@cpp_template` variants, a separate `@native` path.)
    - `from typing import overload` / `from tpy import dispatch` import required
    - CPython compatible: `@overload` is stock CPython (stubs are no-ops, the implementation runs with its `isinstance` checks), and is the only form type checkers accept. `@dispatch` under CPython is a real decorator in the `tpy` stub package that accumulates the variants and dispatches by arity, then `isinstance` on the annotated parameter types (a `Fn`-typed parameter is matched on the callable's arity; a plain `int` argument matches any fixed-int annotation it fits, as a literal does in the compiler); mypy and pyright flag the same-name redefinitions, as they do for `multipledispatch`. Sets with `@native` variants skip the CPython phase since `@native` has no CPython implementation.
- **Working**: `T | None` for non-value types (records, lists, arrays) → nullable pointer (`T*`)
  - Locals, parameters, returns: `T*` (nullable pointer)
  - Class fields and container elements: `std::optional<T>` (storage form). Boundary conversions emitted via `tpy::ptr_to_optional` / `tpy::optional_to_ptr`.
  - `x is None` / `x is not None` for null checks
  - Field/method/subscript access on unproven optional values emits a warning and inserts a runtime null check
  - Guarded paths (`if x is not None`) and `assert x is not None` narrow `x` to `T`
  - Returning narrowed optional values: `if x is not None: return x` correctly unwraps to `T`
  - Narrowed optional values flow into write sinks unwrapped to `T`: `list.append(x)` / `set.add(x)` and subscript-assign values (`d[k] = x`, `lst[i] = x`)
  - Field narrowing: `if obj.field is not None:` narrows `obj.field` to `T` in the guarded scope
  - Reads of narrowed value-Optional fields work in all consumer positions: aug-assign (`self.f += 1`), subscript-LHS (`self.f[i] = x`, `self.f[i] += 1` on `list[T] | None`/`dict[K,V] | None`), and ostream-print of `int | None` (BigInt) and other inners. The storage-form `std::optional<T>` is unwrapped at the read site so the consumer sees `T`.
  - Reads of an ELEMENT through a None-narrowed `Optional[container]` work at every sink the plain receiver has: a method call on the element (`d["o"].append(3)`), binding it (`row = d["o"]` -- a live `T&` alias, so a later mutation is visible through the outer container), a nested read or write (`d["o"][0]`, `m[0][0] = 9`), a call argument, a `for` iterable, `print`, `len`, and an element field write (`d["k"].n = v`). The receiver renders through the narrowing deref (`::tpy::__getitem__((*d), "o")`), so nothing is copied. Both a narrowed NAME and a narrowed `self.field` receiver are covered, and so are the element's own FIELD sinks (`d["k"].rows.append(3)`, `d["k"].n += 1`, `d["k"].kids["a"] = 3`, `d["k"].sub = None`) and a nested subscript off either receiver (`self.d["o"][0]`).
  - Container literals (`[...]`, `{...}`, `{k: v}`) and comprehensions flowing into `Optional[container]` positions: local-init (`lst: list[int] | None = [1, 2, 3]`), call-arg (`f({k: v})` for `f: dict[K, V] | None`), and post-construction field assign (`self.f = [1, 2, 3]` for `f: list[T] | None`). The rvalue container is materialized into a named slot and its address taken; the field assign goes through `std::optional<T>`'s implicit constructor from `T`.
  - Reassigning a narrowed `T | None` field back to `None` (`if self.x is None: return; ...; self.x = None`) emits `std::nullopt` against `std::optional<T>` storage. The assign target uses the declared field type, so the post-narrowing `None` write goes through the boundary conversion as if no narrowing happened. Applies symmetrically to value-Optional fields (`int | None`, etc.)
  - Nested field narrowing: `if obj.inner.field is not None:` narrows through multi-level field access
  - Field truthiness narrowing: `if obj.field:` narrows optional fields (with value-truthiness warning for value types)
  - Field narrowing facts are invalidated when the root object is passed by mutable reference to a function call
  - Functions returning `T | None` return `T*` in C++
  - Inside a tuple: same rule applies per element. `tuple[T | None, ...]` returns / params / locals lower to `std::tuple<T*, ...>`; fields and container elements lower to `std::tuple<std::optional<T>, ...>`. Element-wise conversion at boundaries via `tpy::tuple_to_storage<...>` / `tpy::tuple_to_pointer<...>`. See the Tuples entry under Containers.
  - Mutation-based const inference: `T | None` borrow params whose body provably doesn't mutate through the pointer become `const T*`. Same trigger as the existing `T → const T&` inference for plain record params.
- **Working**: `Optional[T]` from `typing` is equivalent to `T | None` at parse time
  - `from typing import Optional` then `Optional[int32]` produces the same type as `int32 | None`
  - Works in all positions: parameters, returns, local annotations, class fields
- **Working**: `T | None` in generic contexts -- the form is decided per instantiation
  - A generic PARAMETER slot is spelled `::tpy::opt_param_t<T>` (`opt_cparam_t<T>` where the slot is const): `std::optional<T>` at a value `T`, so the caller passes the value exactly as it would to the monomorphic twin, and `T*` / `const T*` at a reference `T`, so a mutation through the slot stays the caller's. A `ValueType`-bounded `T` is a value at every instantiation and keeps the spelled `std::optional<T>`.
  - Fields and container elements keep the storage form `std::optional<T>`; the lift from a parameter slot into one is `::tpy::to_opt_storage<...>`, which absorbs either form.
  - A generic RETURN slot is still committed to `T*` for every instantiation (an optional return cannot alias the field it reads), so a `T | None` parameter cannot be returned directly from a `T | None` return -- see `BUGS.md`.
  - The body's reads are form-neutral: `*o` and `o->x` read both forms, and `o is None` renders `::tpy::opt_has_value(o)`.
  - `None` literals in generic Optional positions emit `std::nullopt` at a value `T` and `nullptr` at a reference one
- **Working**: `T | None` for value types (`int32 | None`, `bool | None`, `float | None`) → `std::optional<T>`
  - Variables, parameters, returns use `std::optional<T>` directly
  - `x is None` / `x is not None` → `.has_value()` checks
  - Truthiness checks (`if x`, `assert x`, `while x`) narrow on true path, with warning about falsy non-None values
  - `print()` prints `None` for empty, value otherwise. Container/tuple/bytes inners (`list[T] | None`, `dict[K, V] | None`, `set[T] | None`, `tuple[...] | None`, `bytes | None`, `bytearray | None`) print via their respective `ListPrinter` / `DictPrinter` / `SetPrinter` / `TuplePrinter` / `BytesPrinter` / `ByteArrayPrinter` wrappers, threaded through `print_optional` / `print_optional_val` as the `Formatter` template arg.
- **Working**: Reassignment-based Optional inference for unannotated variables
  - `x = None; x = Point()` infers `x` as `Point | None`
  - `x = None; x = 123` infers `x` as `int | None`
  - `x = None; x = make_point()` works from function/method return types
  - Bare `x = None` without later type anchor is an error
- **Working**: Literal anchoring for unannotated reassignment
  - `x = 0; x = int32(666)` infers `int32` (if previous literals fit int32 range)
  - `x = 0; x = True` is rejected (no implicit int/bool merge)
- **Working**: Auto inference rules (reassignment)
  - Inference is per variable within its binding scope, across all writes.
  - A later explicit annotation is authoritative and retro-validates earlier writes.
  - Conflicting explicit annotations are an error.
  - `None` seeds optional inference: `x = None; x = T(...)` infers `T | None`.
  - Bare `x = None` with no later concrete anchor is an error.
  - Literal-seeded variables default to the configured default integer type (`--default-int`, default: `int32`) and may be refined by later writes.
  - `bool` does not auto-merge with numeric families during inference.
  - Augmented assignment currently does not perform literal anchoring (`x = 0; x += int32(5)` remains `int`/`BigInt`).
  - For `x = 0` style literal-seeded vars, `x += int32(...)` emits a warning that augmented assignment does not narrow the variable type.
  - The warning also applies when RHS is a function returning `int32`; explicit `int` anchors (`x: int = 0`, `x = int(0)`) do not warn.
- **Working**: Numeric widening across reassignments
  - `x = int32(1); x = int64(2)` infers `int64` (same-sign, wider wins)
  - `x = int32(1); x = 1.5` infers `float` (any integer + float -> float)
  - `x = 1.5; x = int32(1)` stays `float` (bidirectional -- order doesn't matter)
  - `x = int32(1); x = int(2)` infers `int`/BigInt (FixedInt + BigInt -> BigInt)
  - `x = uint8(1); x = int32(2)` infers `int32` (unsigned -> wider signed)
  - Mixed sign same width is an error: `x = int32(1); x = uint32(2)` (requires annotation)
  - Bool mixed with numeric is an error: `x = True; x = int32(1)` (requires annotation)
- **Working**: Optional class members (`self.field: T | None`) → `std::optional<T>` inline storage
  - Field access through optional (`obj.field.x`) works via `std::optional::operator->()`
  - `is None` / `is not None` checks use `.has_value()`
  - Boundary conversions between `std::optional<T>` fields and `T*` pointer-locals handled automatically
- **Working**: Optional-aware operator checks for value-consuming expressions (e.g., `x + 1` where `x: int32 | None`)
  - Unproven use emits warning and inserts runtime null checks
  - Proven non-None paths (guard/assert narrowed) emit unchecked unwraps
- **Working**: None-safe `==`/`!=` for Optional value types
  - `Optional[T] == T`, `Optional[T] != T`, `Optional[T] == Optional[T]` work without warnings or runtime panics
  - `None == 5` -> `False`, `None != 5` -> `True` (matches Python semantics)
  - Ordering operators (`<`, `>`, `<=`, `>=`) still use conservative runtime checks
- **Open**: `T | U` → templates with `if constexpr`, or overloads

For details, see [docs/NONE_SAFETY.md](NONE_SAFETY.md).

### Any (Working)
- **Working**: `from typing import Any` -- type-erased value cell holding any
  copyable concrete value. Backed at runtime by `tpy::Any` (`std::any` plus a
  per-type ops table). See [docs/ANY_TYPE_DESIGN.md](ANY_TYPE_DESIGN.md) for
  the full design.
- **Storage**: `a: Any = 42` / `lst: list[Any] = [1, "x", None]` /
  `cfg: dict[str, Any] = {...}`. Inline construction via `Any(value)` is
  supported (TPy-specific sugar for the INTO_ANY coercion -- CPython
  rejects this; documented divergence). View types upgrade to owned at the storage
  site (`StrView` -> `std::string`, `BytesView` -> `::tpy::Bytes`, a str /
  bytes LOCAL whose storage is still pending likewise) so the cell owns its
  contents; scalars are spelled with their own C++ type so `int64(1)` stays
  `int64`. `int` literals store as `BigInt` (matches
  TPy's `int` annotation and makes `cast(int, x)` round-trip naturally).
  Move-only contents (`@nocopy` records, `Own[T]` of `@nocopy`) rejected.
- **Copy-warning for reference-type sources**: storing a `record` / `list`
  / `dict` / `set` from a named lvalue into `Any` warns ("copies T into
  Any; use `copy()` to make this explicit"). Suppressed for `copy()`
  calls, rvalue sources, last-use auto-move, value types, `str`, and
  `bytes`/`BytesView`. Same convention as the existing dict.update /
  iter copy-warning machinery.
- **Universal ops on raw `Any`** (no narrowing required): `print`, `str`,
  `repr`, f-string interpolation, `bool` / `if x:` / `not x`, `==` / `!=`,
  `hash`, `x is None` / `x is not None`. `==` on two cells of one type
  goes through that type's `==`; two cells of different types are equal
  only as numbers of one value (`Any(1) == Any(1.0) == Any(True)`, the
  value union's rule through the same comparison leaf) and False
  otherwise. `hash` agrees (`hash(1.0) == hash(1)`), so `dict[Any, V]` /
  `set[Any]` dedupe `1`, `1.0`, `True` as CPython does. Residual
  divergences: a record with no `__eq__` compares False against a copy of
  itself (identity is unanswerable inside an owning cell), and `char`
  `'a'` vs `str` `"a"` is False.
  `hash` raises `TypeError("unhashable type: '<demangled>'")` (catchable) if the contained type isn't Hashable.
- **`isinstance(x, T)` narrowing**: non-consuming borrow into the cell.
  Inside the true branch the variable is a `const T&` aliasing the contents;
  the outer `Any` survives. Single concrete `T` only (no `Optional`, no
  `Any`, no protocol). Tuple form `isinstance(x, (A, B))` is supported via
  an OR of typeid checks; the branch keeps the variable as `Any` (no
  single-typed extraction). Works on both function locals and module
  globals. Cast is exact-typeid only -- no inheritance walk in v1.
- **`typing.cast(T, x)`**: runtime checked extraction. Concrete `T`; typeid
  mismatch raises `TypeError("Any holds <actual>, cannot cast to <expected>")`
  (catchable; type names demangled). For non-`Any` sources, `cast` is a static
  no-op (matches CPython). `cast(Any, x)` rejected at compile time (including
  aliased imports like `from typing import Any as A`).
- **Auto-coerce out**: `n: int = any_var` and other annotated-target sites
  (function arg, return, container insert) emit the same runtime check as
  `cast(T, x)`. Union / Optional / generic-type-param targets are rejected
  with a "narrow first" diagnostic; protocol targets fall through to
  structural conformance (Any satisfies Hashable / Equatable / Stringable /
  Representable at the type-system level).
- **Narrow-required ops** (compile error on raw `Any`): attribute / method,
  call, iter, subscript, `len`, binary operators, `is` against any non-None
  RHS. User must narrow first.
- **Composition**: `Any | None`, `Any | T`, `Optional[Any]`, `Own[Any]` all
  rejected as redundant. `isinstance(x, Any)` rejected (Any is not a runtime
  class).
- **`@noalloc`**: not enforced today (TPy's `@noalloc` decorator is
  parsed but doesn't policy allocation behaviour yet). Any will join
  the reject list once broader `@noalloc` enforcement lands -- `std::any`
  may heap-allocate for non-trivial contents.
- **`set[Any]` / `dict[Any, V]`**: allowed; runtime hash via the cell's
  hash slot. Inserting an Any holding a non-Hashable value raises `TypeError("unhashable type: '<demangled>'")` (catchable).

---

## Operators

### Arithmetic
- **Working**: `+`, `-`, `*`, `//`, `%`, `/`, unary `-`
- **Working**: `**` (power) for `int`, `int32`, and `float`
- **Working**: `**` with negative integer exponent (e.g., `2 ** -3`) - compiles but panics at runtime (use `2.0 ** -3` for float result)

### Comparison
- **Working**: `==`, `!=`, `<`, `<=`, `>`, `>=`
- **Working**: Chained comparisons (`a < b < c`, `a <= b <= c`, `1 < x < 10`, etc.) -- each operand evaluated exactly once, with the trailing operands short-circuited. An operand that only looks inert -- a `@property` or `__getattr__` access, which is a method call behind field-access syntax -- binds to a temp like any other call rather than being duplicated into both of the pairs it joins. Operand *order* is not guaranteed to match CPython's left-to-right when a sibling operand can assign to another, nor within a single two-operand expression like `f() + g()` (both tracked in BUGS.md).
- **Working**: `is`, `is not` (identity comparison with `None`, enum values, and bool literals `True`/`False`)
- **Working**: Mixed `int`/`float` comparisons (BigInt promoted to double)
- **Working**: Mixed-sign fixed-int comparisons (e.g. `int32 < uint32`, `int64 >= uint64`) -- codegen routes through `std::cmp_*` so the result is mathematically correct regardless of value range (no signed-to-unsigned reinterpretation surprises). Sema emits a warning at the comparison site naming both types and suggesting an explicit cast, except when one side is a literal or a literal-seeded local that retro-widens to the other side's type (those resolve to same-sign and don't warn).

### Membership
- **Working**: `in`, `not in` (for list, Array, Span, str, tuple literals)
- Containment follows CPython's rule, `x is e or x == e` per element, wherever identity exists: over a CONTAINER haystack (list, Array, Span, set, dict, a `__contains__`-less user iterable's `__iter__` loop aside) there is ONE render, `::tpy::seq_contains`, whose own legs test address identity before `==` wherever needle and element can be the same object -- so a REFERENCE-typed needle (a record) is found by identity, and an open-`T` needle inside a generic body gets that leg at each instantiation that supplies it, so an object that IS an element is found even when its `__eq__` is not reflexive (`p in xs` for a record with a NaN field). A TUPLE LITERAL haystack is the second render (the `==` OR-chain below), and a record needle against one is **rejected** rather than rendered (*this construct is not yet supported by C++ code generation (binop.shape.in.record)*) -- bind the elements to a list to get the identity-aware answer. Over VALUE-typed elements there is no identity to test -- a `float64` element is a bare `double`, not an object -- so membership there is `==`-only, and NaN is the one value that makes the difference observable: `nan in [nan, 1.0]` is False in TPy and True in CPython (`BUGS.md#membership-skips-identity-shortcut`). The sibling sequence queries (`list.index` / `.count` / `.remove`) and hashed `dict` / `set` lookup are `==`-only throughout. A record with NO `__eq__` stays **rejected** at `in` (*'in' requires element type 'T' to conform to 'Equatable'*) rather than falling back to CPython's identity-only containment: TPy has no object-identity operator for records at all (`a is b` on two records is an error), so answering `in` by identity alone would make record identity observable through a back door, and it would make TPy's copy-vs-alias choices load-bearing -- wherever TPy copies a record CPython would alias, the answer would silently flip to False. With `__eq__` present the identity leg can only add the True answers CPython also gives, which is why that direction is safe and this one is not.
- Against a tuple literal, `x in (a, b, c)` expands to an `==` OR-chain, so the needle renders once per element -- `==`-only, with no identity leg, and a record needle there is rejected at `binop.shape.in.record` rather than answered without one. Anything but a name or literal (a call, a `@property` / `__getattr__` access, a field read) binds to a temp first so its access path runs once. The temp aliases rather than snapshots, so an element that assigns to the needle's own storage is still observed -- an evaluation-order gap tracked in BUGS.md, not a property of the temp. A `bytes` needle takes the same chain; its literal elements render the static span, since the family compares through `::tpy::BytesView`.

### Logical
- **Working**: `and`, `or`, `not`
- **Working**: `and`/`or` return operand value (Python semantics) when both operands have the same type, including every reference type (`list[T]`, `dict[K,V]`, `set[T]`, `bytearray`, `Array[T, N]`, records) and the `bytes` / `BytesView` / `Span[T]` value families. The truthiness test is the operand type's own (`.empty()` for the str/bytes families and `bytearray`, `__len__() != 0` for the containers and `Span`, `__bool__` where a record defines it), so a select and a plain `if` over the same operand agree. Reference-typed operands (the containers, `bytearray`) alias the chosen operand; the value families (`bytes`, `Span`, `str`) copy a view, materialized to the owned type at an owned sink. Two `bytes` operands whose C++ spellings differ at runtime (a `bytes` parameter, a span, against an owned local or a literal) are rejected rather than converted (`BUGS.md#bytes-select-mixed-runtime-spelling`). Mixed-type operands return `bool`. In condition context (`if`, `while`), always uses efficient C++ `&&`/`||`. Both contexts **short-circuit**: the unchosen operand is not evaluated (a fallible or side-effecting right operand runs only when reached), matching CPython.

**Argument temporaries inside a conditionally evaluated operand.** A call argument sometimes needs a named temporary (an owning slot copying a borrowed source, a record rvalue at a reference parameter). Where that argument sits in a *conditionally evaluated* position -- an `and`/`or` right operand, either arm of a ternary, a chained comparison's second and later comparators -- the temporary is created where the operand runs, so its work (a container copy, a constructor and its side effects) happens exactly when CPython would do it. Two declared exceptions:

- A value that **cannot be moved** (a `@nomove` record, or one transitively owning inline uninitialized storage) cannot be placed in the deferred slot, so it is built with the enclosing statement even when the branch is skipped. The compiler warns at the argument; assigning the value to a local before the expression states the unconditional build in the source and silences the warning.
- A temporary whose C++ type is deliberately left for the compiler to deduce (the structural-protocol argument temporaries, where the concrete type is what the protocol's concept must see) has no spelling for the deferred slot, so the construct is **rejected** in a conditionally evaluated operand rather than hoisted ahead of the guard. Bind the value to a local before the expression.

### Bitwise
- **Working**: `&`, `|`, `^`, `~`, `<<`, `>>`

### Assignment
- **Working**: `=`, `+=`, `-=`, `*=`, `/=`, `//=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=`
  - Augmented assignment works on variables, fields, and subscripts (`items[i] += 1`)
- **Working**: Multiple assignment (`a = b = c = expr`) -- value is evaluated once; all name targets alias (non-value types) or copy (value types). Supports mixed name/field/subscript targets and property setters. Tuple unpacking targets not supported in multiple assignment. Note: the rightmost Name target is assigned first (anchor strategy), so `b[a] = a = 3` uses the new value of `a` for the subscript index, unlike Python which assigns left-to-right.

---

## Control Flow

### Conditionals
- **Working**: `if`, `elif`, `else`
- **Working**: Ternary `x if cond else y` -- same-type branches (including any reference type: `list[T]`, `dict[K,V]`, `set[T]`, `bytearray`, `Array[T, N]`, records), numeric widening, `T`+`None` to `Optional[T]`, Optional narrowing (`is not None` / truthy). Reference-type aliasing follows arm value-category: a **both-lvalue** ternary (`a if c else b`) binds by reference and aliases the chosen arm like CPython; a **mixed** lvalue/rvalue ternary (`a if c else [9]`) renders as a prvalue that copies the lvalue arm where CPython would alias it -- this is **warned** ("ternary copies a reference type where CPython would alias the variable arm"), and `copy()` acknowledges it. As a method RECEIVER the same mixed pair is refused rather than warned -- the mutation would land on the copy (`BUGS.md#ternary-receiver-mixed-category-copies`). A mixed/both-lvalue ternary passed to an `Own[T]` slot copies the chosen arm into owned storage (the standard "copies into owned storage" warning).

### Loops
- **Working**: `while`
- **Working**: `for i in range(n)`, `for i in range(start, end)`, `for i in range(start, end, step)`
- **Working**: `for item in container` (for-each over list, Array, Span, str)
- **Working**: `for x in iterator` (for-each over Iterator types -- user-defined iterators)
- **Working**: `for x in iter_param` (for-each over `Iterator[T]` and `Iterable[T]` protocol-typed parameters)
- **Working**: the iterable may be an lvalue CHAIN (`for b in rows[i]`, `for b in g.rows`, `for b in self.rows[i]`). Which hops are admitted -- "the hop must name storage the ROOT outlives" -- and which keep a located reject is stated once in **Const reference optimization** under [Parameter Passing Convention](#parameter-passing-convention), together with the depth limit the iteration's borrow key imposes.
- **Working**: `break`, `continue`
- **Working**: Reassigning scalar range targets inside the body does not affect iteration, matching Python. Direct assignment, augmented assignment, walrus writes and accepted nested scalar target reuse use private induction. Existing target storage and post-loop values are preserved. A tuple-unpack target reusing a name already in scope (`for i, j in pairs:` under `for i in range(3):`) assigns that slot per iteration and holds the last element after the loop; the reused slot must be a same-type scalar, since a reused str slot may be the view form while the element is owned.
- **Working**: Const-ref loop variable binding -- when the loop body never mutates the loop variable (no field writes, no non-`@readonly` method calls, no passing to mutable parameters, no address-of), codegen emits `const auto&` instead of `auto&&`. Value types always use typed copies regardless. Parameter mutation inference (see [Implementation Notes](#parameter-mutation-inference-partial)) refines "passing to mutable parameters": if the callee is known not to mutate a specific parameter, passing the loop variable there does not force mutable binding.
- **Working**: `for/else`, `while/else` -- else block runs when loop completes without `break`; `break` emits `goto` past the else body
- **Working**: Loop variable and body-declared variables visible after the loop (matching CPython scoping). Only hoisted when actually referenced after the loop -- no codegen change for variables used only inside the loop. Range counter loops use a hidden counter so the user variable holds the last-yielded value (not the C++ post-increment overshoot). A name first declared in a LOOP body takes its single declaration at the anchor whether the later use reads it or only *assigns* it: `for i in range(3): x = i` followed by `x = 5` emits `int32_t x;` before the loop and writes into it from both sites, since it is one local either way. A loop's `else` clause is a clause of the same loop statement and hoists the same way: a name first bound there takes its declaration at the loop's anchor, so the `else` binding and a later assignment write one slot. The other block-emitting statements revoke their declarations at the block's close, so a name first declared in an `if` / `match` arm or a `try` / `except` body and only assigned afterwards needs no hoist -- the later assignment declares the function-scope local itself. One gap: a name first declared in a `try`'s `else` body and assigned after the `try` is still rejected by the C++ build (see BUGS.md). Because it is one local, it also carries **one type and one storage**: the join of every binding to it anywhere in the function -- same block, `if` / `try` / `match` arm, loop body, sibling loop -- and the position of a binding never changes the TYPE or the SAFETY of the answer, though for a `str`/`bytes` local whether it copies can still depend on binding order (`BUGS.md#view-storage-join-stricter-after-first-binding`, detailed below). The join widens exactly where a same-scope reassignment widens (an int literal past `int32`, `int32` then `int64`, `None` then a record, an empty `[]` then `[1, 2]`, coercions such as `int32` into an `int` local) and is rejected where that reassignment is rejected (`Type mismatch in reassignment to 'n'` for a value the earlier bindings' type cannot hold). Within one function TPy has no re-declaration/shadowing: a name cannot take a second type by crossing a block boundary any more than it can within one scope. (A *function* boundary is different, and follows CPython: a bare write inside a function binds a fresh local that takes its own type even when a module global of that name exists -- see "Variables & Scope".) The loop *variable* is exempt, since its type comes from the iterable rather than from the user -- `for p in points: ...` followed by `p = Point(9)` binds a fresh local, matching CPython. A body-declared reference local that is read after the loop and bound at two sites (if/else arms, or once in the body and again after the loop) hoists as a pointer local with a rebind slot per site, the alias-gated model's storage, so a holder bound in the body keeps the loop's last object across the later rebind; `while` bodies hoist the same way as `for` bodies, nested either way round; at module level that two-site shape is still rejected (see BUGS.md). The single declaration stands in the innermost C++ scope enclosing both the binding loop and the read, so it reaches across an unrelated *sibling* loop between them (two loops binding one name bind one local -- the declaration goes before the first, and both bodies assign into it) and out of an enclosing `if` / `with` / `try` / `match` whose arm holds the binding loop. Read *inside* that enclosing block, the declaration stays inside it. Definite assignment follows the block, not the loop: a loop in only ONE `if` / `try` / `match` arm leaves the name maybe-unassigned after the block and the read is rejected, exactly as a direct assignment in one arm is -- assign the name once before the block to accept it. The MIXED shape -- a loop binding the name in one arm and a plain assignment in another -- is accepted at the `if` only, and only when the loop head provably runs (otherwise the loop arm assigns nothing and every spelling rejects); the `try` and `match` spellings still reject (`BUGS.md#try-match-mixed-loop-arm-bind-rejected`). Two hoist flavors do not render: a UNION-typed local rejects at every position, loop or branch (`BUGS.md#union-local-hoist-unclassified`), and a non-value local hoisted out of a `match` rejects where its `if` / `with` / `try` twins compile (`BUGS.md#match-nonvalue-hoist-unadmitted`). The one storage runs the same join for `str`/`bytes`, taking the STRICTEST binding's demand: a binding that owns -- a concatenation, or any source the check cannot prove is a live borrow -- makes the one local own, and the borrowing bindings copy into it, which is what CPython's aliasing means for an immutable string. A local every binding of which is a proven live borrow keeps the zero-copy view. The proof is stricter after the first binding, so the two orders of the same pair of bindings can differ in whether the local copies (never in its type, and never towards the unsafe side) -- `BUGS.md#view-storage-join-stricter-after-first-binding`. Two caveats on what the hoist emits: a REFERENCE-typed loop *variable* read after the loop holds a copy of the last element rather than the element itself (`BUGS.md#ref-loop-var-read-after-loop-copies`), and a loan taken in the body of a loop over storage the enclosing block owns is carried past the block that frees it (`BUGS.md#loop-body-storage-outlived-by-loan`).

#### Loop clause locals, the zero-trip loop and `break`

Every clause of a loop -- the body and the `else` -- is a block. A name first
bound in one is the function-scoped local Python makes it, hoisted to the loop
statement, and:

> after the loop it counts as assigned only when that clause **provably ran**
> and the binding is on **every edge that leaves the clause**: the fall-through
> into `else`, each `continue`, each `break`.

Otherwise the read after the loop is rejected with
`variable 'x' may not be assigned at this point`.

The two clauses answer "provably ran" differently, and for a zero-trip loop
oppositely: the body needs a head that proves an iteration, while the `else`
clause runs on the fall-through path by definition -- it is the paths that
*skip* it, the `break` paths, that have to be joined in.

What proves a loop runs:

- a `range` whose bounds the value ranges decide, including the step's sign:
  `range(2)`, `range(0, 6, 2)`, `range(3, 0, -1)`, and `range(n)` /
  `range(1, n)` where an earlier guard narrowed `n` (`if n < 1: return ...`);
- a non-empty literal at the head: `for x in [4, 5, 6]`, `for c in "abc"`, and
  the set, dict and bytes spellings -- though for those three the read after
  the loop is not yet compiled, and reports the construct as unsupported
  instead (`BUGS.md#hoisted-local-over-set-dict-literal-head-unlowered`); bind
  the literal to a name ahead of the loop to get the same proof and code that
  builds;
- a local whose only binding is a non-empty container literal
  (`items = [10, 20, 30]` ahead of `for x in items`), of any container kind;
- a `while` whose entry condition is provably true -- the literal `True`, or an
  integer comparison the value ranges decide, as in `i = 0; while i < 3`;
- iteration over an enum (`for c in Color`), which always has members.

What does not: a parameter or field container, a call result (including
`d.items()`), an unnarrowed bound, an `async for` head, and an emptiness guard
(`if not xs: return` / `if len(xs) == 0: return` / `if xs:`) -- the last is a
tracked gap in `TODO.md`, not a decision that the guard is meaningless.

One caveat on the literal-bound-local proof: it reads the local's write
history, and a *mutation* is not a write, so `items = [1, 2]` followed by
`items.clear()` still proves. That one is a defect, not a rule
(`BUGS.md#literal-bound-local-proof-ignores-mutation`) -- the container-non-empty
fact family that closes the emptiness guard closes it with the same kill set.

The second half of the rule is the body path: a provable head is not enough.

```python
for i in range(2):     # provably runs
    if flag:
        q = Pic(i)     # but the binding is not on every body path
return q.n             # ERROR: variable 'q' may not be assigned at this point
```

**This is stricter than CPython, deliberately.** CPython accepts such a program
and raises `UnboundLocalError` only on the zero-trip path, at runtime; TPy has
no such exception to raise -- the C++ it would emit reads an uninitialized slot
-- so it rejects at compile time instead. The remedy is either of the two the
diagnostic implies: assign the name once before the loop, or narrow the bound so
the loop provably runs.

```python
def last(xs: list[int32]) -> int32:
    v = 0                 # assigned once before the loop
    for x in xs:
        v = x
    return v
```

**The edges out of a clause.** A `break` leaves the loop past the rest of the
body and past the `else` clause; a `continue` leaves the body the same way and
lands at the head, so the `else` still runs. Both are edges out of the body
clause and both are joined -- a `continue` against the body's own end, a `break`
against the state after the `else`. So an `else`-bound name is not assigned
after a loop whose body can break, and a body-bound name is not assigned when a
`break` *or* a `continue` sits above its binding. A `return` or `raise` never
joins here at all: those paths do not reach the statement after the loop.

`while True:` is the shape with no fall-through at all -- the head is never
false, so the `else` never runs and the `break`s are the only edges out. A name
bound on every break path is therefore assigned after it:

```python
i = 0
while True:
    i += 1
    if i > 2:
        total = i * 10     # the only edge out carries the binding
        break
return total               # accepted, prints 30
```

```python
for i in range(3):     # provably runs
    if flag:
        break          # ... but this path skips both the binding and the else
    w = i
else:
    w = 9
return w               # ERROR: variable 'w' may not be assigned at this point

for i in range(3):     # provably runs
    if flag:
        continue       # ... but this path skips the binding
    w = i + 1
return w               # ERROR: variable 'w' may not be assigned at this point
```

CPython raises `UnboundLocalError` on exactly those paths. The remedies are the
same two: assign the name before the loop, or bind it on the early-exit path as
well (`w = i` above the `break` / `continue`).

**A walrus in the head** follows one rule, the same one an `if` head follows,
because it is the same question asked of the loop's two exits: *a walrus target
in a condition counts as assigned on a branch only when every way of reaching
that branch evaluated it.* The condition answers it for both of its paths at
once, by structure:

- `a and b` is true only through both operands, false either through `a` alone
  or through both -- so true grants `T(a) | T(b)`, false grants
  `F(a) & (T(a) | F(b))`.
- `a or b` is the mirror: false grants `F(a) | F(b)`, true grants
  `T(a) & (F(a) | T(b))`.
- `not a` swaps the two answers, and a comparison chain (`x < (m := f()) < y`)
  evaluates its first two operands whatever it answers and the later ones only
  when it is true.

A `while` body is the true path and the edge that *leaves through the head* --
the fall-through into the `else` and the code after the loop, but never a
`break` -- is the false path.

```python
while (n := next_val(i)) > 0:   # plain: assigned on every edge
    ...
return n                        # accepted

while flag and (b := next_val(i)) > 0:
    total += b                  # accepted: the head came out true to get here
return b                        # ERROR: `flag` alone may have ended the head

while i > 5 or (c := next_val(i)) > 0:
    i += 1
return c                        # accepted: leaving the head evaluated the `or`

while not (i > 5 or (d := next_val(i)) > 0):
    total += d                  # accepted: a false `or` evaluated both operands

while (flag and (e := next_val(i)) > 0) or i < 0:
    total += e                  # ERROR: the `or`'s right operand alone may
                                # have made the head true
```

The head is evaluated even by a zero-trip loop, so the plain and `or` forms are
assigned after one that never ran a single iteration.

One stated conservatism: a `break` inside a `try` whose `finally` binds the name
is snapshotted at the `break`, so the `finally`'s binding is not counted and a
valid program of that shape is rejected
(`BUGS.md#break-join-ignores-finally-binding`, which covers the `continue`
spelling too).

### `with` Statement (Context Managers)
- **Working**: `with expr as var:` -- duck-typed context manager protocol via `__enter__`/`__exit__` methods
  - `__enter__(self) -> T` -- return type determines the `as`-variable type (can differ from the context manager type)
  - `__exit__(self, exc_type, exc_val, exc_tb) -> None | bool` -- CPython-compatible 4-arg shape. `exc_type` and `exc_tb` are `None` in v1.5 (no traceback/type-object machinery); `exc_val` carries the caught exception (`Optional[BaseException]`) on the exceptional path, `None` on normal exit.
  - **Suppression** (v1.5 M1): `__exit__ -> bool` -- returning `True` suppresses the exception (control falls through past the `with`); returning `False` re-raises. `__exit__ -> None` is cleanup-only (no suppression). `if exc_val is not None:` narrows for binary suppression.
  - **Class-based dispatch** (v1.5 M2): `isinstance(exc_val, X)` on `Optional[BaseException]` lowers to `dynamic_cast` on the `const BaseException*` pointer that flows into `__exit__`. `BaseException` is `@dynamic`-rooted via the `Throwable` protocol in the stdlib; every exception subclass inherits transitively, so `isinstance(exc_val, ValueError)`, tuple form `isinstance(exc_val, (OSError, RuntimeError))`, and same-type checks all work. Subclass-typed narrowing in the true branch is also supported via cast-and-cache codegen -- `if isinstance(exc_val, HttpError): exc_val.status` reads through a `dynamic_cast`'d typed reference emitted once at branch entry; inherited methods still virtual-dispatch through the cached view. See `docs/ASYNC_PROGRESS.md` v1.5 M2 section.
  - `exc_val` may be explicitly annotated as `None` for cleanup-only managers (call site passes `{}` instead of `&__exc`); unannotated defaults to `Optional[BaseException]`.
  - Cleanup via try/catch on every path -- normal fall-through, exception, early return, break, continue. Foreign C++ exceptions (non-`BaseException`) take a best-effort cleanup path with no suppression.
  - `as`-variable is visible after the `with` block (matching CPython scoping)
  - Multiple context managers: `with a() as x, b() as y:` -- nested try/catch blocks, inner exits first (LIFO)
  - Name reuse: `with a() as f: ... with b() as f: ...` -- second block rebinds via pointer-local indirection
  - `with expr:` (no `as`) -- enter/exit without binding
  - **Not supported**: a manager whose `__enter__` or `__exit__` is `@error_return` -- the `with` header calls the dunder implicitly and has no `except` clause to receive the error, so the statement is rejected at compile time (`BUGS.md#with-header-error-return-dunder`). Keep the dunders plain and move the failing work into a method the body calls inside its own `try`/`except`
  - **Manager binding**: a reference-type lvalue manager (`with mgr:` / `with self.mgr:`) is *borrowed* -- `__enter__`/`__exit__` act on the original object, so their mutations are visible afterward (matching CPython); a `@nocopy` manager works. An rvalue manager (`with Lock():`) and a value-type manager are owned by the block (copied -- a value type crosses a `with` as a value boundary). Same rule applies to `async with`.
  - **`as`-target binding**: the target *aliases* whatever `__enter__` returned, matching CPython (`t is c` for the `return self` idiom), so a mutation through the target is visible on the manager -- in `__exit__` and after the statement. A plain non-value `__enter__` always lends: returning a fresh object requires `Own[T]` (sema otherwise rejects "cannot return local or temporary as reference"), and that spelling makes the target *own* the value instead. The target is an ordinary function local, so it outlives its statement and is readable afterwards; in a generator or `async def` it lives in the resumable frame, and its alias-vs-own form is spelled from `__enter__()`'s return type so C++ resolves it at instantiation. **Manager lifetime.** When the target's storage points *into* the manager (an aliasing, pointer or view slot) and the target is still read after the statement, a temporary manager is kept alive for as long as the target — in the resumable frame, or in a function-scope slot off it — so `with Manager() as t:` followed by a later read of `t` is well-defined rather than a dangling read. Three conditions, all required: the manager must be a temporary the `with` owns (a named one already outlives the block), the target must actually be read past the statement *before* being rebound, and `__enter__` must lend the manager's own storage. That last one matters for a *delegating* manager -- one whose `__enter__` returns some other object -- which keeps its ordinary lifetime and is destroyed at the end of the block, as CPython does; provenance is followed through the body's local bindings, so `tmp = self.item; return tmp` counts as lending its own storage, and anything unprovable counts as lending too. **Declared divergence:** for a manager that IS kept, `__del__` runs at the end of the enclosing function (or frame) rather than at the end of the `with` block. CPython drops the manager there because the target holds an independent reference to what `__enter__` returned; under TPy the lent object lives *inside* the manager, so dropping it would invalidate the target. Bind the manager to a name to make the lifetime explicit, or have `__enter__` return `Own[T]` so the target owns a value instead. No compile-time diagnostic is emitted for this (decided 2026-08-04): Python does not guarantee `__del__` timing at all, so the difference sits inside behavior the language leaves unspecified -- timing-sensitive release belongs in `__exit__`, which runs at the identical point on both paths. One name, one storage location: a target that is also assigned elsewhere reuses that same slot rather than shadowing it.

### Other
- **Working**: `return`, `pass`. Bare `return` and `return None` are one statement everywhere (the parser gives the bare form a `None` operand, so every later phase sees one shape): at a void slot -- `-> None`, an unannotated def, a generator's end-of-iteration return -- both emit a plain `return;`, in every position (free function, method, `__init__`, `__exit__`, `try`/`finally`, `with`, `match` arm, closure, `@error_return` body, `async def`); at an Optional or None-union slot both spell the None value (`std::nullopt` / the null pointer / the monostate). At a slot that cannot hold None either spelling is the ordinary return-type mismatch ("expected int32, got None").
- **Working**: `del obj[key]` -- element deletion via `__delitem__` dunder (dict, list, user types)
- **Working**: `del x` -- variable unbinding. Use after del is a compile-time error. Re-assignment after del is supported. Works on locals, parameters, globals, nonlocals, loop variables, generators, and module-level variables. Early destruction (move-sink) is only emitted when the variable is the sole owner of its value; aliases, alias sources, parameters, and globals just unbind the name without destroying. Limitation: pointer-locals that were initially aliases (e.g. `a = b; a = new_value; del a`) skip early destruction conservatively, even after reassignment to an owned value.
- **Working**: `match`/`case` -- structural pattern matching
  - **Union subjects**: class patterns (`case Circle():`, `case Circle(radius=r):`), primitive type patterns (`case int32():`, `case str():`), container type patterns (`case list():`), parameterized record patterns (`case Box():`), subject narrowing, `switch (s.index())` codegen with `std::get<N>`. **Literal field-value sub-patterns** (`case Dog(legs=4):`) compare the field after the variant index matches: the arm becomes conditional (routed to the guarded switch path, `if (__case.legs == 4)`), falls through to a later `Dog()` / `_` arm when it fails, and does not count toward exhaustiveness. `field=None` on a nullable field tests its storage repr (`!opt.has_value()` for an optional field, `std::holds_alternative<std::monostate>` for a union-with-None field); `=None` on a non-nullable field is rejected (can never match). **Nested type sub-patterns** for disambiguating parameterized union members: `case Box(value=str() as v):` on `Box[str] | Box[int32]` resolves to `Box[str]` at compile time via recursive field type matching. Also works on union-typed record fields (`case Wrapper(pet=Cat() as c):` where `pet: Cat | Dog`) with `std::holds_alternative`/`std::get` codegen. Supports arbitrary nesting depth and mixed combinations (type-param x type-param, type-param x union-field, union-field x type-param, union-field x union-field).
  - **Enum subjects**: value patterns (`case Color.Red:`), `switch` codegen
  - **Literal subjects** (`Literal["r", "w"]`, `Literal[1, 2, 3]`): match on `Literal`-typed parameters with exhaustiveness warnings for missing values, subject narrowing in each arm (e.g. `case "r" | "w":` narrows to `Literal["r", "w"]`), enabling overload dispatch from match bodies. Codegen routes to str switch/if-elif, int switch or bool chain based on base type.
  - **Primitive subjects** (`int`): literal patterns, `switch` codegen; (`bool`, alone or as the value side of `bool | None`): the `==` chain, since the strict warning set refuses a C++ switch over a bool; (`str`): switch-based dispatch for 5+ unguarded literal cases (best length/char discriminator, computed over UTF-8 bytes so non-ASCII literals dispatch correctly), if/elif fallback below threshold; (`float`): if/elif fallback. A NON-LVALUE subject (`match f():`, `match a + b:`) materializes into an owned dispatch local (`auto __match_subject_N = <expr>;`) so every arm reads one stable copy; lvalue subjects keep the `auto&` bind
  - **Numeric literal patterns are cross-kind, and a float literal at an integral subject splits by its value.** A non-bool numeric literal compares with `==`, which is numeric across `bool`/int/float in CPython and in the emitted C++ alike, so an INT literal matches any numeric subject or field whatever its kind: `case 1:` matches a `float` subject, `case P(x=1)` a `float` field. A FLOAT literal against a subject or field that holds whole numbers only (fixed int, `bool`, `int`) is judged by its value: a non-integral one (`case 1.5:` over an `int32`) can never match and is an ERROR -- CPython runs it as a dead arm, TPy reports it, the same declared divergence as the bool-identity arm; an integral one (`case 2.0:`) matches, but warns (*float literal pattern 2.0 against subject type 'int32'; spell it 2*) and is folded to the int literal, so the arm keeps the ordinary label render. The same never-match rule covers RANGE whichever kind spells the literal: an integral value the subject type cannot hold (`case 300:` over an `int8`, `case 2:` over a `bool`, `case 1e30:` over an `int32`) is an error naming what the type holds, while an `int` (BigInt) subject is unbounded and never rejected. `True`/`False` are NOT in this family: PEP 634 compares them by identity, so `case True:` matches only a `bool` subject and TPy reports the arm that could never fire rather than emitting a `== true` that would match where CPython does not
  - **Record subjects**: field-value matching (`case Point(x=0, y=0):`), if/elif codegen; `goto`-based fallthrough for guards
  - **Nested class sub-patterns** (`case Outer(inner=Inner(v=1)):`): the nested test composes into the arm's condition on every tier that renders field conditions -- the record tiers, the Optional chain and the polymorphic tier. Binding a name BELOW a nested pattern (a capture, an `as`, or a keyword-bearing union-field guard) is supported on the record tiers only: their emit threads the base-name map such a binding is spelled against, and elsewhere it is rejected rather than dropped
  - **Optional subjects**: `case None:` + literal/class/value patterns on the inner type; if/elif with `has_value()`/`nullptr`; value-type and pointer-repr. Wildcard/capture arms match the None side too (CPython): a bare `case x:` binds the full `T | None` subject unless an earlier unguarded `case None:` arm exists, in which case it soundly narrows to `T`. Coverage is tracked per side -- a class pattern covers every non-None value but never None, so `case Point(): ... case None: ...` is legal and a match with only `case None:` warns about the missing value side. A capture that would re-type an existing same-named variable to the full Optional is rejected with a fix hint. Field/element-sourced subjects (storage-form `std::optional`) auto-lift to pointer form at the subject binding.
  - **Polymorphic subjects** (`@dynamic` protocol / polymorphic class; bare `Pet`, `Ptr[Pet]`, or `Box[Pet]`/`Rc[Pet]` deref view): class patterns dispatch by runtime type via `dynamic_cast` (inheritance conformer) / `tpy::dyn_adapter_cast` (structural conformer), reusing the `isinstance` engine. The subject narrows to the matched subclass inside the arm (`p.speak()` resolves against the subclass), with field bindings, **literal field-value sub-patterns** (`case Dog(legs=4):` -- the field check is emitted after the cast, falling through to a later arm when it fails), or-patterns (type-test only), and `goto`-based guard fallthrough. Exhaustive only via a wildcard or a root-type catch-all arm (open hierarchy); a later arm whose type is a subclass-or-equal of an earlier *unconditional* arm's is rejected as unreachable. **Subject may be an arbitrary expression** -- a bare name, subscript (`match animals[i]:`), field (`match owner.pet:`), rvalue call (`match make_box():`), or wrapper deref view: the subject is evaluated once into `__match_subject` (`auto&` for an lvalue so a `case C() as v: v.f = ...` arm writes through; `auto` for an rvalue so a temporary outlives the deref-view cast), and the `dynamic_cast` chain runs off that binding. Cast const-ness is derived from the subject's readonly-ness (a `const` subject casts to `const Sub*`). **This is a deliberate divergence from `isinstance`, which stays bare-name-only**: `match` does not narrow a name in place (CPython re-evaluates the subject; the arm captures the narrowed value via `as`/a binding), so an expression subject needs no dotted-path narrowing key -- only a bare-name subject narrows the subject for in-arm reads (`p.speak()` resolves against the subclass). A *named-constant* field comparison (`case Dog(size=Big):`), `Optional[Pet]`, and `yield`/`await`-in-arm are not yet supported. See `docs/MATCH_CASE_DESIGN.md` Phase 9.
  - Wildcard (`case _:`), capture (`case x:`), as-pattern (`case Dog() as d:`), and the double whole-subject bind (`case x as y:`, on the scalar, str-switch and polymorphic tiers)
  - Keyword and positional field bindings in class patterns (`case Rect(w, h):`, `case Point(x, y, z=pz):`)
  - Positional patterns resolved via field declaration order (implicit `__match_args__` for all records, not just `@dataclass` -- CPython extension)
  - Pattern binding scope: bindings leak to enclosing scope (like Python), pre-declared when defined in all arms
  - Or-patterns (`case Dog() | Cat():`) with `switch` case fallthrough; with bindings (`case Dog(name=n) | Cat(name=n):`) via body duplication in switch (str/float fall back to if/elif with `||`). A **parenthesized alternative group** (`case (Dog() | Cat()) | Bird():`) is flattened into its parent's alternative list at parse time, at any nesting depth, so it behaves exactly as the flat spelling -- CPython's own compiler treats the group boundary as transparent too. An `as` inside a group (`case ((Dog() | Cat()) as p) | Bird():`) is a binding boundary and is NOT flattened. An `as` ON a group (`case Cat(lives=1) | Cat(lives=2) as q:`) binds the whole subject -- one thing however many alternatives there are -- and is supported on the record tiers and the Optional chain; on a union or polymorphic subject it is still rejected, because sema types such a binding as the full subject rather than the join of the alternatives (`BUGS.md#or-pattern-as-binding-no-join`). An alternative list containing the wildcard (`case 1 | _:`) is irrefutable, so the whole group IS the catch-all arm (the switch `default:` / the chain's `else`) and no alternative renders a label -- handled on the scalar, str-switch and union tiers; a GUARDED irrefutable group (`case 1 | _ if flag:`) is the catch-all arm too, on every scalar tier (the guarded-union tier still rejects a wildcard alternative, `BUGS.md#guarded-union-or-wildcard`). A catch-all arm -- guarded `case _ if flag:` as much as the or-group spelling -- that PRECEDES a more specific arm keeps its source position: such a match routes to the ordered if/elif chain instead of the switch or the string discriminator, since a C++ `switch` has no source order
  - Guard clauses (`case Dog(name=n) if n == "Rex":`) with `goto`-based fallthrough for unions, records, str/float/bool chains, and Optional subjects: bindings are emitted before the guard (a guard may read its own captures), and a failed guard falls through to later arms -- including a duplicate-literal arm after a guarded one. Enums/int use `switch` (bool takes the chain) with if/else guard chains + `goto` to default
  - `break`/`continue` inside match arms target the enclosing Python loop (a `break` in a switch-lowered arm jumps past the loop instead of exiting the C++ switch)
  - Pattern-binding storage: a free-copy-scalar binding (fixed int, bool, `char`, float, enum) is bound *by value* (a durable snapshot that survives subject mutation in the arm, matching CPython at zero cost); every other binding (heap-backed `str`/`BigInt`, views, reference types) is a zero-cost `auto&` borrow into the subject's storage. Mutating that storage in a non-scalar binding's arm dangles the borrow where CPython keeps the old object alive, and is warned: assignment to the subject path/owner prefix or an invalidating container method (synchronously), and a non-readonly method call on the subject prefix (deferred until readonly is settled). The binding form is one sema fact read by both codegen and the warning, so a scalar is never silently aliased; mutation through an alias of the subject (or an opaque unknown-readonly method) still evades the warning, and the full borrow-tracker treatment is tracked in BUGS.md. **Capture rebinds**: if the arm REBINDS the capture NAME itself (`for v in xs`, `v = ...`, aug-assign, walrus, `with ... as v`, unpack), the `auto&` alias would write THROUGH into the subject where CPython rebinds a fresh local -- so a value-typed capture is instead forced *by value* (the copy absorbs the rebind, matching CPython), and a reference-typed capture -- or a tuple with reference-typed elements, which aliases the same way -- is *rejected* (the parity-correct alias-until-rebind-then-reseat local is not yet modeled; neither a copy nor the alias matches). A rebound capture is also HOISTED (added to the match's branch-decl predecl set), so it declares once in the enclosing scope and both its binding and every rebind emit assignments -- this covers the bare-name, walrus, aug-assign, for-loop-var and tuple-unpack forms, and rebinds nested in an inner block, matching CPython, where `match` is not its own scope and the capture is an ordinary function local. (Two carve-outs: a `with ... as <capture>` rebind is still blocked by a separate pre-existing gap in the `with`-as target emit for predeclared locals -- see BUGS.md -- and a capture name bound at DIFFERENT types across arms keeps its per-arm block-scoped binding, since one hoisted slot cannot hold both types.) A **nested `match` reusing an outer arm's capture name** rebinds it the same way (a `match` is not its own scope either), so it hoists too -- but it is not a write-through: a capture bind re-seats (pointer form) or assigns (value form) the binding itself, so unlike the rebind forms above it neither forces a value-typed capture by value nor rejects a reference-typed one. A reference capture hoists to a pointer local that every bind re-seats, so the inner subject's value flows out of the outer arm and post-re-seat mutation of it is visible through the capture -- matching CPython. One slot serves every bind, so its const-ness is the OR over all binding subjects, and they must agree on the TYPE -- a nested match binding the name at a different type is rejected at the nested capture (mirroring the cross-arm rule; without it C++ truncates silently wherever an implicit conversion exists). Field/subscript writes through the capture (`v.x = 1`, `v[0] = 1`) are legitimate CPython-visible aliasing and do NOT count as rebinds; a comprehension's own loop var (`[v for v in ...]`) is a separate scope and does not either
  - Error diagnostics: non-member type, duplicate case, type mismatch, unreachable case after wildcard, unreachable value-side arm after an Optional class catch-all, type-changing Optional capture of an existing variable, `as` binding on `case None:` (NoneType is monostate -- no value to bind; declared divergence, CPython binds None), too many positional patterns, positional/keyword overlap, or-pattern variable name mismatch, wrong class for record subject, reference-type capture rebound in the arm (the alias would corrupt the matched object), literal pattern of a kind the subject type cannot match (including inside a class pattern's field sub-pattern), literal kinds TPy cannot render against an otherwise-compatible subject -- a `bytes` literal against a `bytes` subject and an `int` literal against an `IntEnum` -- which CPython WOULD match and which are reported as not-yet-implemented with a guard-based workaround, and a non-integral `float` literal against a whole-number subject, which can never match (the bullet above). A `str` literal against a `char` subject (`case "a":` over a `char`) is rejected as well: `char` is a distinct single-character type, so the arm would be a pointer comparison, not the character test the source reads as; CPython, which has no `char`, matches the one-character string. Declared divergence -- match on `str(c)` instead (`match str(c): case "a":`); a guard comparing the character (`case c if c == "a":`) does not lower yet, the `char` subject has no chain tier
  - Exhaustiveness warnings for unions (missing member types), enums (missing values), booleans (missing True/False), optionals (missing None and/or the missing value side), Literal types (missing literal values), records (no unconditional catch-all arm), and non-enumerable scalar subjects (`int`/`str`/`float` literal arms cannot prove coverage -- a catch-all is required). A non-exhaustive match falls through (no `std::unreachable()` tail) and joins the pre-match flow state

---

## Functions

### Definition
- **Working**: Typed parameters and return types
- **Working**: End-of-body return enforcement -- a function whose declared return type can hold `None` falls through to Python's implicit `return None` (materialized by sema; works in sync, owned-record, and async forms); a return type that cannot hold `None` makes a reachable end of body a compile error (mypy's "missing return" rule -- the C++ body would return garbage). Generators are exempt (fall-through is StopIteration). Terminators: `while True:` without a loop-level `break`, a trailing `assert False` (TPy never strips asserts, so there is no CPython `-O` analog where the assert would vanish and the function would return None), and a `try` whose `finally` body itself returns/raises. A trailing call to a never-returning function is not recognized yet (no `NoReturn` type -- see TODO.md), end with `raise` instead.
- **Working**: Reassigning function parameters (const-ref params like `int`/`str` auto-emit by value when reassigned)
- **Working**: C++ keyword escaping -- Python identifiers that clash with C++ reserved words (e.g., `default`, `class`, `namespace`) are automatically mangled in generated code
- **Working**: Default parameter values -- constant expressions (literals, `None`, fixed-int constructors), **enum members** (`def f(order: MemoryOrder = MemoryOrder.SEQ_CST)` emits the scoped C++ enumerator `MemoryOrder::seq_cst`, qualified for cross-module enums and honoring `@native` member renames), and references to module-level `Final[T]` constants (same module or imported). Maps to a C++ default argument where one can express the binding, and is otherwise materialized at the call site: a default has no C++ spelling when a later parameter lacks one (C++ requires defaults to be trailing, Python does not -- `def f(a, b=10, *, c)`, and likewise a fixed positional ahead of `*args`), or when its type would instantiate a class template over a record still incomplete at the declaration (a value-form `T | None` / value union of a record, including under `Own[...]`, which forces the value form whatever the inner representation says). That second case applies to FREE functions only: a member's declaration sits inside the record, where the alternatives are already defined, so a member keeps its C++ default -- a member whose union forward-references a record defined LATER in the module is a known residual (see BUGS.md). A materialized default currently resolves its name in the CALLER's scope rather than the module that wrote it, so a cross-module `Final` constant shadowed by a same-named name at the call site silently takes the wrong value (see BUGS.md); cross-module Final references otherwise emit the qualified C++ name. A default is type-checked against the slot it initializes, by the same rules and in the same words as the equivalent `x: T = <const>` assignment: `def f(n: int32 = None)`, `def f(s: str = 5)` and an out-of-range `def f(x: int8 = 200)` are all rejected at the `def`. The check covers free functions, methods, `__init__`, both halves of an `@overload` group (the stubs' and the implementation's defaults each reach the emitted specialization) and record field defaults, so the default surfaces stay consistent. Three surfaces are excluded, all of them ones where the default never reaches codegen: a `Protocol` method signature, a nested `def` -- the Protocol method silently drops the default, the nested `def` warns that it is ignored (`BUGS.md#nested-def-default-ignored`), so a call omitting the argument fails (see BUGS.md) -- and a `Protocol` field, which contributes no initializer to a conformer. An enum-member default additionally requires that the base name resolve to a registered enum and the member exist, and a `Final[T]` name is checked once its binding resolves. A default on a generic slot is deferred instead: it is legitimately polymorphic and validated at instantiation, so `def f[T](x: T = 0)` called as `f[str]()` produces a clear sema error there. `T()` default-construction syntax is supported: `def f[T](x: T = T()) -> T` maps to `T{}` in C++. Generators and `async def`s carry defaults on the canonical declaration of every C++ entity a call can land on: the factory's forward declaration for a free function, the in-class declaration for a method (never the out-of-line definition -- C++ rejects the repeat), plus the resumable frame's constructor, which an inline `await` and the synthetic `async with` / `async for` suspensions construct directly instead of going through the factory. So an omitted default applies at every call form -- named call, `create_task`, inline `await`, and a defaulted `__aenter__` / `__aexit__` / `__anext__` parameter -- on both free functions and methods, monomorphic and generic alike.
- **Working**: Keyword arguments at call sites -- `f(name="World")`, `Point(y=1, x=2)`, mixed positional+kwargs. Resolved to positional at compile time. Supported for user functions, methods, constructors, and generic functions. Not supported for overloaded builtins (e.g., `range`, `len`).
- **Working**: Keyword-only parameters -- `def f(x: int, *, name: str = "default")`. Parameters after `*` or `*args` can only be passed by name. Enforced at compile time for module-level functions and methods; a NESTED def drops the marker and accepts a positional call CPython rejects (`BUGS.md#nested-def-kwonly-marker-dropped`); no C++ codegen changes (keyword-only is a Python-level constraint).
- **Working**: Positional-only parameters -- `def f(a: int32, /, b: int32 = 5)`. Params before `/` bind positionally with correctly aligned defaults; passing one by keyword is a compile error (CPython raises TypeError). Works on free functions, methods, protocol methods, and `@overload` stubs; not enforced for record constructors (`__init__` posonly params parse correctly but constructor calls accept keywords TPy-side that CPython rejects with TypeError); lambdas reject `/`.
- **Working**: `for` over an existing local rebinds it (CPython: the var holds the last element after the loop); rebinding with a different element type is a compile error. Fresh loop vars stay loop-scoped in C++; comprehension vars are Python-scoped (never rebind).
- **Working**: Multi-target assignment (`a = b = expr`) evaluates the value once and assigns targets left-to-right (CPython order), preserving reference aliasing.
- **Working**: `__all__` with literal `+=` extension; `__all__` statements are compile-time export metadata and emit no runtime code. Dynamic mutation (non-literal `+=`, `.append(...)`) is a compile error.
- **Declared divergence**: `raise E(...) from cause` warns and drops the cause -- TPy's exception model has no `__cause__`/`__context__` chaining. Remove the clause to silence the warning.
- An `assert` message containing `await` is evaluated only on failure (desugared to an explicit conditional, matching CPython); `await` in a match-case guard is rejected with a bind-before-the-match hint.
- **Working**: Homogeneous `*args: T` -- `def f(*args: int32)`. Type annotation required. Inside the body, `args` has the distinct body-view type `varargs[T]` (sema-level; **not** `Span[T]`), supporting `len()`, indexing, iteration, and slicing (`args[1:]`, `args[i:j]`, etc.); a slice stays a `varargs[T]`. At call sites, trailing positional args are packed into a stack array. Works with fixed positional params before `*args` and keyword-only params after. C++ codegen uses `tpy::varargs<T>` -- a dual-mode span that stores value types directly (like `std::span<T>`) and non-value types via pointer indirection for correct reference semantics. Because `tpy::varargs<T>` has no conversion to `std::span<T>`, **passing a vararg (or a slice of it) where a `Span[T]` is expected is rejected at sema** with a clean type-mismatch (not a C++ build error). `varargs[T]` is compiler-internal and **not user-spellable** -- users write `*args: T`, never `varargs[T]` in an annotation. Mutations through `*args` to non-value types are visible to the caller. Works with `@nocopy` types (no copies made). Supported on free functions, module-qualified functions, and instance methods alike.
- **Working**: Readonly `*args` -- `def f(*items: readonly[T])` is a genuinely-readonly vararg: codegen emits `tpy::varargs<const T>` (const element access), so the body cannot mutate elements -- a write through a reference element is rejected at sema (`Cannot mutate readonly reference`), with C++ const as the backstop, same as `Span[readonly[T]]`. Mutable args may be passed in (adding const is safe). The readonly slot is what makes unpacking a readonly source legal (see the unpacking entry below). The slot is **auto-inferred** to readonly when the body doesn't mutate the vararg (parallel to the existing non-vararg ref-param auto-const inference, propagating across vararg forwarding via Phase-2 mutation propagation). Plain `*args: T` whose body mutates an element keeps the mutable slot.
- **Working**: `*list` unpacking at call sites -- `f(*my_list)` passes a list/array/span to a `*args` function. Zero-cost for contiguous containers (direct span mode). Supports forwarding: `def g(*args: T): f(*args)`. The unpacked element type must be compatible with the `*args` slot: into a **mutable** slot a `Span[readonly[T]]` source is rejected (the slot exposes mutable element access, so aliasing readonly data is unsafe), and into a **readonly** slot (`*items: readonly[T]`) a readonly source is accepted (the const-span source constructs `varargs<const T>` directly). Any element type that would need a per-element conversion is rejected. All rejections are clean diagnostics, not C++-build errors. (Forwarding a `*args` parameter is exempt from the readonly check: it carries the `varargs[readonly[T]]` body-view type but its runtime form is the mutable `tpy::varargs<T>`, forwarded via the varargs copy- or const-view ctor.)
- **Working**: Generic `*args` -- `def first[T](*args: T) -> T` infers `T` from call-site arg types. The body can iterate the pack directly (`for x in args`) and after a slice (`for x in args[1:]`); the element binds as the type param `T`.
- **Working**: `*args` on a **generator** frame, a generator method included. `varargs[T]` is a value type, so the frame field is the same `tpy::varargs<T>` view the sync signature spells, ctor-moved -- no owned copy of the pack -- and every body read (`len`, subscript, iteration, whole-pack forward) takes the sync rows. A suspending loop over the pack aliases its elements (`const E*` for the auto-readonly slot, `E*` for a mutated one), so a mutation through the loop var stays visible to the caller across the suspension. A varargs LOCAL inside a frame body (e.g. `tail = args[1:]`) is still rejected -- the pack is admitted as a parameter only.
- **Limitation**: `*args` on `@overload` stubs is rejected (clear error).
- **Limitation**: `*args` on `async def` functions/methods is rejected (clear error) -- the variadic coroutine-factory await lowering is not yet wired.
- **Working**: TypedDict -- `class Foo(TypedDict):` defines a struct with dict-like subscript syntax. `d["key"]` compiles to `d.key` (compile-time field access, key must be string literal). Construction via keyword args: `Foo(name="Alice", age=30)`. All fields required at construction (matches mypy). Field `= default` values are ignored (warning emitted; CPython ignores them too). `total=False` makes all fields `Optional[T]` with `None` default; `d["key"]` on an absent field throws a catchable `KeyError` at runtime (matches CPython, modulo CPython's repr-of-key formatting). C++ mapping: plain struct, zero hash-map overhead. Dynamic keys and unknown keys produce compile errors. No methods allowed on TypedDict. `.get("key")` returns `Optional[T]` (may be `None` for `total=False`, always has value for `total=True`); `.get("key", default)` returns `T` (uses `value_or` for `total=False`, returns field directly for `total=True`). `"key" in td` checks field presence (`has_value()` for `total=False`, constant `True` for `total=True`). Unknown class keyword arguments produce warnings.
- **Limitation**: TypedDict vs CPython -- all keys must be compile-time string literals (no dynamic access). Not yet supported: dict-like methods (`.keys()`, `.values()`, `.items()`, `.update()`, `.pop()`), `len(td)`, `del td["key"]`, iteration (`for k in td`), `**td` unpacking in dict contexts, dict literal construction, per-field `NotRequired[]`/`Required[]` (PEP 655), type parameters, TypedDict inheritance. See `docs/FEATURE_ROADMAP.md` TypedDict section for details.
- **Open**: Heterogeneous `*args` (untyped) -- needs `Any` type
- **Working**: `**kwargs: Unpack[TypedDict]` -- typed variadic keyword arguments via PEP 692. `def f(**kwargs: Unpack[TD])` compiles to `void f(const TD& kwargs)` (single struct parameter). At call sites, keyword args are packed into a TypedDict construction. `f(**td_instance)` passes the struct directly. `kwargs["key"]` uses TypedDict subscript. `total=False` TypedDicts make all kwargs optional. Works with regular positional params (`def f(host: str, **kwargs: Unpack[TD])`), on methods, and with kwargs forwarding (`inner(**kwargs)`). Mixed keyword args are split: regular param kwargs resolve normally, remaining go to the TypedDict.

### Decorators
- **Working**: `@noalloc` (parsed and recognized, enforcement planned)
- **Working**: `@hotpath` (parsed and recognized on functions and methods; no effect yet -- marks perf-critical code for planned warnings/constraints). Takes no arguments.
- **Working**: `@readonly` ("doesn't mutate its arguments" contract on functions/methods)
  - C++ codegen: `@readonly` non-value params emit `const T&` (or `const T*` for Optional aliases, `::tpy::Union<const A*, const B*>` for non-value union params), matching sema-level enforcement. The union form is not `@readonly`'s alone -- every non-mutating union parameter gets it, so the annotation changes the enforcement and not the ABI. A union param whose address escapes (narrowed + member-accessed) stays mutable, consistently across signature, call site, and body -- the const decision is the single materialized `decide_param_const` verdict on `FunctionInfo`.
  - Type-based enforcement: `ReadonlyType` wraps non-value params; field access, subscript, and method calls propagate readonly through expressions
  - Local alias deduction: `alias = param` preserves `ReadonlyType` through variable assignments
  - Constructors, `print`, I/O, and global writes are generally allowed; constructors follow the same call rule when passed param-derived mutable refs
  - Value-type arguments are copies and can be freely passed to any function
  - Implicit readonly on dunders: `__len__`, `__getitem__`, `__eq__`, arithmetic operators, etc.
  - `@readonly(False)` opts out of implicit readonly (e.g., `__getitem__` that caches)
  - Readonly methods returning references get both const and non-const C++ overloads; value returns get const only
  - Protocol method readonly: `@readonly` on protocol methods enforces that implementations are also readonly; conformance fails at sema time if a record's method is not readonly when the protocol requires it
  - Protocol concept generation: user-defined protocols where all methods are readonly generate `const T&` in C++ concepts
  - Deep readonly for pointers: accessing a `Ptr[T]` field through a readonly receiver yields `Ptr[readonly[T]]`, preventing mutation through pointer fields
- **Working**: Const method auto-inference -- methods that never mutate `self` are automatically emitted as `const` C++ member functions without requiring an explicit `@readonly` annotation
  - Phase 1 tracks whether each method directly writes to `self` (field writes, subscript writes)
  - Phase 2 propagates `self_mutated` through the call graph via call edges: `self.method()`, `self.field.method()`, `super().method()`, and for-each iteration over self fields are all deferred to Phase 2, which only marks self as mutated when the callee actually mutates its self
  - Post-pass (`infer_method_const`) marks all non-self-mutating methods as `is_readonly`, and the codegen sync pass copies the flag to AST nodes
  - Exceptions: `__init__`, `__del__`, in-place operators, consuming methods (`Own[Self]` receiver), the mutable clone of an `@auto_readonly` pair (its const sibling already exists, so flipping the mutable to const would create duplicate signatures), and methods that override non-const C++ virtuals from `@dynamic` protocols are never inferred const
  - Methods whose return value borrows from `self` (e.g. `def get(self) -> Span[int32]: return self.field`) are skipped to avoid silently degrading the declared mutable return type to its readonly counterpart -- with one exception: when the return type is an inherently-const view (`StrView`, `BytesView`, `Span[readonly[T]]`, `SpanIter[readonly[T]]`), const-ifying the method does not change the return type, and auto-const fires
  - Receiver inference and const-method parameter emission share the recorded return-borrow source reader; this extraction preserves the existing decisions and generated signatures.
  - `@readonly(False)` opts out of const inference for a specific method
  - Limitation: container-mediated aliases not tracked (e.g., `[param]` into list then iterate)
- **Working**: `readonly[T]` type modifier (per-parameter constness)
  - `readonly[T]` on a parameter means "immutable reference to T", maps to `const T&` in C++
  - Prevents: field writes, subscript writes, non-readonly method calls, passing to mutable `T` param
  - Local alias deduction: `alias = readonly_param` inherits readonly status for non-value types
  - `readonly[int32]` (value types) is a no-op -- copies are always safe
  - `readonly[T | None]` and `readonly[T] | None` normalize to the same C++ type (`const T*`)
  - `readonly[Protocol]` generates `const T_name&` for template protocol params
  - `readonly[T]` on a field declaration makes the field immutable after `__init__` -- assignment outside `__init__` is rejected at sema time (C++ codegen does not emit `const` on the field, since const fields break move/copy assignment)
  - See `docs/READONLY_DESIGN.md` for full design rationale
- **Working**: `unsafe_interior_mutable[Ptr[T]]` field modifier (the dual of `readonly`) -- marks a field as *outside* the owning object's readonly boundary, an unsafe escape hatch for hidden bookkeeping reached through a pointer (e.g. `Rc`'s refcount cell). Imported from `tpy`.
  - Through a `readonly[Self]` receiver, `readonly` does **not** propagate into an interior field's pointee, and mutating *through* it (`self._cell.incr()`) does not demote the method -- so a refcount-style method can be `@readonly` / `@auto_readonly`. This is the C++ `mutable`-member / std::shared_ptr const-copy pattern (const doesn't cross a raw pointer).
  - Does **not** relax slot reassignment: `self._field = other` through a readonly receiver is still rejected (enforced on the receiver, not the field).
  - Field-only, `Ptr[T]`-only for now; rejected (with a clear diagnostic) on params/returns/locals, on non-`Ptr` fields, and wrapping `readonly[...]` / `Own[...]` / nested `unsafe_interior_mutable[...]`.
  - Transient marker: stripped at field registration into a `FieldInfo` flag, so it never flows through the type system (no codegen / type-comparison cost) -- mirrors how `auto_readonly[Self]` is stripped.
  - See `docs/READONLY_DESIGN.md` for rationale and the soundness contract.
- **Working**: `@pure` (no observable side effects -- no mutation of non-local state, no I/O)
  - Trusted annotation (Phase 1): no enforcement, metadata only for future borrow checker / escape analysis
  - Pure implies readonly -- `@pure` methods can be called on `readonly` receivers
  - Heap allocation is permitted (not considered an observable side effect); `@noalloc` is orthogonal
  - Marked on built-in functions (`len`, `repr`, `hash`, `chr`, `ord`, `pow`, `round`, `divmod`, `abs`, `min`, `max`, `range`, `iter`), all `math.*` functions, and all readonly methods on builtin types
  - Supported on user functions and methods via `from tpy import pure`
- **Working**: `@value_ptr_coercion` (`from tpy.extern import value_ptr_coercion`) -- enables `T -> Ptr[T]` call-site coercion for any type on `Ptr[T]` parameters. The compiler inserts address-of (`&`) automatically and enforces mutable lvalue. Used to define `take_ptr` in the stdlib:
  ```python
  @value_ptr_coercion
  @cpp_template("{0}")
  def take_ptr[T](p: Ptr[T]) -> Ptr[T]: ...
  ```
- **Open**: Custom decorators → compile-time transforms

### Type Polymorphism
- **Open**: `def foo(x: int | str)` → template or overloads
- **Working**: Generic functions `def foo[T](x: T)` → templates

### Generic Functions

**Working**: Generic functions using Python 3.12+ syntax (`def first[T]:`)

```python
def first[T](items: list[T]) -> T:
    return items[0]

def swap[T](a: T, b: T) -> None:
    # T can be any type
    temp = a
    # ... swap logic

def make_pair[A, B](a: A, b: B) -> Pair[A, B]:
    return Pair(a, b)
```

**Type Inference**: Type arguments are inferred from function arguments:

```python
nums = [1, 2, 3]
x = first(nums)        # T inferred as int from list[int]

strs = ["a", "b"]
s = first(strs)        # T inferred as str from list[str]

points = [Point(1, 2)]
p = first(points)      # T inferred as Point from list[Point]
```
Inference also accepts coercible concrete arguments on generic calls when type
parameters are inferred from other arguments. Example: if a generic function
has `delta: int64`, passing `int32` for `delta` is accepted via normal
argument coercion. The same per-element coercion applies when `T` is itself a
compound shape (`tuple`, `list`, `dict`, `set`): for `heappush[T: Comparable]`
called as `heappush(pq, (3, "third"))` where `pq: list[tuple[int32, str]]`,
the literal `3` inside the second-arg tuple coerces to `int32` to match the
slot in the already-determined `T`.

**Protocol-typed args**: when a callee parameter is a generic protocol
(`Iterable[T]`, `Awaitable[T]`, ...) and the arg is a record conforming
structurally or via inheritance, inference strips `Ref[T]` / `readonly[T]` /
`Own[T]` wrappers on the arg side before resolving the record's method table.
This mirrors what protocol conformance checking already does, so a record
passed via a generic outer parameter (whose expression carries an implicit
`Ref[T]` wrapper) infers the protocol's type arg the same way it would for a
locally constructed value.

**Owned-context args**: an `Own[T]` parameter or a record constructor argument
holds the storage form, not a borrow, so inference canonicalizes the arg to
storage form (strips `Ref` -- recursing into tuple elements -- and `readonly`)
before binding the type param. A subscript of a non-value `list[T]` analyzes to
the element borrow form (`Ref[T]`), so without this `Entry(copy(src[i]))` inside
a generic function would infer `Entry[Ref[T]]` rather than `Entry[T]` (and a
two-arg call like `heappush(heap, Entry(copy(src[i])))` would then fail
inference outright). A bare value passed into an `Own[T]` param keeps its
reference form, so val_or_ref reference passing (`map(identity, ...)`) is
unaffected.

**Optional-widening args**: a bare (non-Optional) arg widening into an
`Optional[T]` parameter slot strips an incidental `Ref` wrapper before binding
the type param -- the slot's payload is by-value storage, so binding `Ref[T]`
would instantiate against the borrow form the arg render never produces. A
sema-narrowed Optional field read analyzes as `Ref[Payload]`, so without this
`is_def_gen(t.o)` (with `def is_def_gen[U](o: U | None)`) would infer
`U=Ref[Pod]` rather than `U=Pod`. The bare-`TypeParamRef` param
reference-preservation rule (`map(identity, ...)`) is unaffected.

**Contextual Type Inference**: When arguments don't fully determine all type
parameters, the expected type from context (assignment annotation, return type,
reassignment, field assignment) fills in the remaining params:

```python
class Container[T]:
    val: T

def make_box[T]() -> Own[Container[T]]:
    return Container[T]()

# Assignment context: T inferred from annotation
b: Container[int32] = make_box()     # T = int32

# Record constructor context: T inferred from annotation
c: Container[int32] = Container()    # T = int32

# Return context: T inferred from enclosing function return type
def get_box() -> Own[Container[int32]]:
    return make_box()                # T = int32
```

Contextual inference also unwraps `Own[T]` on both sides, so `Own[Container[T]]`
matches `Container[int32]` correctly. If no context is available, inference
still fails and explicit type arguments are required.

**Nested call context**: parameter types from outer calls flow as hints to inner
calls. This means `sink(make_box())` infers T from `sink`'s parameter type.
Record constructor arguments also propagate hints to inner generic calls.

**Partial explicit type args**: you can provide some type arguments and let the
compiler infer the rest from arguments or context:

```python
from tpy.unsafe import unsafe_cast
q = unsafe_cast[uint32](p)  # T=uint32 explicit, U=int32 from arg

# Also works with module.func[T](args) syntax
import tpy.unsafe as m
q = m.unsafe_cast[uint32](p)
```

**`_` wildcard type arguments**: use `_` as a placeholder in any type argument
position to let the compiler infer that parameter. Works in functions,
constructors, and methods. `f[_]()` is equivalent to `f()` (full inference).

```python
pair_func[_, int64](int32(5), int64(20))  # T inferred from arg
triple[int32, _, int64](a, b, c)          # B inferred from arg
Box[_](int32(42))                         # T = int32 from constructor arg
m.transform[_, int64](x, int64(100))      # method-level wildcard
```

**Deferred generic instance inference**: when a generic type is constructed without
explicit type arguments and without enough context to infer them, the compiler
defers inference and resolves type parameters from subsequent method calls:

```python
class Container[T]:
    val: T
    def __init__(self) -> None: ...
    def set(self, val: T) -> None: ...
    def get(self) -> T: ...

c = Container()      # T unknown -- defer
c.set(int32(10))     # T = int32, eagerly resolved
x = c.get()          # normal: x is int32
```

Type parameters can also be resolved from expected-type context -- passing to
a function with a typed parameter or returning where the function's return type
is known:

```python
def consume(c: Container[int32]) -> None: ...
c = Container()      # T unknown -- defer
consume(c)           # T = int32, resolved from parameter type
```

The pending type is eagerly resolved the moment all type parameters are
constrained. While pending, field access is rejected with clear diagnostics.
Works for all generic records (user-defined and library types).
See `docs/LOCAL_TYPE_DEDUCTION.md` Phase 7a.

**Not yet supported**: inference from field assignment targets (`self.field = expr`)
or subscript targets (`items[i] = expr`). See
`docs/BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` for the full design.

**Explicit Type Arguments**: When inference isn't possible or you want explicit control:

```python
# Explicit type argument
result: int32 = first[int32](nums)

# Required when inference would be ambiguous
def identity[T](x: T) -> T:
    return x

# Type annotation provides hint for inference
y: int32 = identity(42)  # T inferred as int32 from annotation
```

Generated C++ (template functions):
```cpp
template<typename T>
tpy::val_or_ref_t<T> first(std::vector<T>& items) {
    return tpy::get_item(items, 0);
}

// Call site with explicit type args
first<int32_t>(nums);

// Call site with inferred type
first<tpy::BigInt>(nums);  // compiler always emits explicit args
```

**Error Handling**: Invalid type arguments are caught at compile time:

```python
first[123](nums)        # Error: Integer '123' is not a valid type argument
first[x](nums)          # Error: 'x' is not a valid type (if x is a variable)
first[UnknownType](nums) # Error: Unknown type: UnknownType
first[Printable](nums)  # Error: Protocol types cannot be used as type arguments
```

### Type Parameter Bounds

**Working**: Constrain type parameters with a bound (Python 3.12+ syntax). Two kinds:
a **protocol** bound (`T: Sized`, `T: Comparable`) is a *capability* constraint
checked structurally; a **class** bound (`U: Animal`) or a **type-parameter** bound
(`U: T`) is a *subtype* constraint checked nominally (`is_subclass_of`) and enabling
the `Ptr[U] -> Ptr[B]` upcast (see "Bounded type-parameter coercion" above).

```python
from typing import Sized
from tpy import int32, Comparable

# Function with bounded type parameter
def get_length[T: Sized](items: T) -> int32:
    return len(items)  # OK: T conforms to Sized, so len() works

# Class with bounded type parameter
class SortedContainer[T: Comparable]:
    data: list[T]

    def add(self, item: T) -> None:
        # Can use comparison operators because T: Comparable
        ...

# Multiple bounds on different type parameters
def process[T: Sized, U: Comparable](items: T, key: U) -> int32:
    return len(items)
```

**Protocol Method Calls**: You can call protocol methods on bounded type parameters:

```python
from typing import Protocol, Self
from tpy import int32, Own

class Clonable(Protocol):
    def clone(self) -> Own[Self]: ...

def duplicate[T: Clonable](item: T) -> Own[T]:
    return item.clone()  # OK: T conforms to Clonable
```

Generated C++ (concept-constrained templates):
```cpp
// Stdlib protocols generate concepts in the tpystd:: namespace
template<tpystd::typing::Sized T>
int32_t get_length(const T& items) {
    return ::tpy::__len__(items);
}

template<tpystd::tpy::Comparable T>
struct SortedContainer {
    std::vector<T> data;
    void add(const T& item) { ... }
};
```

**Available Protocol Bounds**:
- `Sized` - has `__len__()` method
- `Comparable` - has comparison operators (`<`, `<=`, `>`, `>=`, `==`, `!=`)
- `Sequence[T]` - has `__len__()` and `__getitem__()`
- `Deref[T]` - has `__deref__() -> T` (auto-deref for field/method access)
- `Covariant[T]` - marker: type param T is covariant, enables `G[Child] -> G[Parent]` coercion
- `Iterable[T]` - has `__iter__` returning `Iterator[T]` (standard Python iterable protocol, used in method signatures)
- `Iterator[T]` - has `__next__` and `__iter__` (Python iterator protocol)
- `NativeIterable[T]` - codegen optimization marker for C++ range-for iteration
- User-defined protocols (including protocols with inheritance)

**Protocol Inheritance with Bounds**: When using a child protocol as a bound (e.g., `T: PrintableAndSized`), methods from all ancestor protocols are available on `T`. The fixed-width integer markers are related the same way -- `AnyFixedSigned` and `AnyFixedUnsigned` are declared as sub-protocols of `AnyFixedInt` (as the C++ concepts always were: `AnyFixedSigned = AnyFixedInt<T> && is_signed_v<T>`) -- so a `[T: AnyFixedSigned]` or `[T: AnyFixedUnsigned]` bound satisfies every `AnyFixedInt` requirement, and a type-ctor call on the bounded value (`int32(x)`) resolves against the `__init__[T: AnyFixedInt]` overload.

**Method-Level Type Parameters**: Methods can have their own type parameters, independent of the class:
```python
class Converter:
    def identity[U](self, val: U) -> U:
        return val
```
Supports inference from arguments (`c.identity(42)`) and explicit type args (`c.identity[int32](42)`).

**Per-Method Bounds**: Methods on generic classes can add extra bounds to class type params:
```python
class Container[T]:
    def is_sorted[T: Comparable](self) -> bool: ...  # only when T is Comparable
```

### Default Type Parameters (`@type_param_default`)

**Working**: TPy extension that opts a generic function into falling back to a default type when inference has no other evidence. Required for empty-container literal calls -- `[]`, `set()`, `{}` carry an `UnknownElementType` placeholder that matches `Iterable[T]` / `Sequence[T]` but pins T to nothing.

```python
from tpy.extern import type_param_default, DefaultInt

@type_param_default(T=DefaultInt)
def f[T](xs: Iterable[T]) -> str:
    return "ok"

f([])           # T defaults to the configured --default-int (int32)
f([1, 2, 3])    # T = int32 (inferred from elements; default not consulted)
```

The fallback fires when (a) T was never inferred, or (b) T inferred only to `UnknownElementType`. `DefaultInt` is the sole supported sentinel today and resolves to the `--default-int` config (`int32` by default). Bounds still apply -- the default must satisfy them.

Several stdlib generics are tagged so their empty-literal calls "just work": `sorted[T: Comparable]`, `all[T: Truthy]`, `any[T: Truthy]`, `iter[T]`, `enumerate[T]`, `reversed[T]`, plus `round[T]` and `math.frexp[T]`.

Without the tag, calling such a generic with only an empty container produces a clean `Cannot infer type arguments for 'f'. Specify explicitly: f[T](...)` diagnostic. Workaround if you can't tag the function: bind to a typed local first (`xs: list[int] = []; f(xs)`).

---

## Classes

### Definition
- **Working**: Typed fields
- **Working**: In-class field defaults -- constant expressions (literals, `None`, fixed-int constructors) and **enum members** (`c: Color = Color.RED` emits the C++ constant initializer `Color c = Color::RED;`, qualified for cross-module enums and honoring `@native` member renames). The default's enum must match the field's declared type -- `c: Color = Mode.A` is rejected at registration. A field with an explicit default never blocks zero-arg construction, even when its type alone is not default-constructible (the sema/codegen default-constructibility walks skip fields that carry a default)
- **Working**: `__init__`
- **Working**: Instance methods. The receiver parameter must be spelled `self` (and `cls` on a `@classmethod`) -- Python treats those names as convention rather than syntax, so a method written `def m(klass)` is valid Python that TPy rejects with a located error; see TODO for lifting it.
- **Working**: Generic classes (Python 3.12+ syntax)
- **Working**: `@staticmethod` → static methods (including on generic classes with type inference)
- **Working**: Method-level type parameters (`def transform[U](self, val: U) -> U`), including on `@staticmethod`
- **Working**: Per-method type parameter bounds (`def is_sorted[T: Comparable](self) -> bool`). Bounds on a method type param that shadows a class type param are enforced as a sema diagnostic at every dispatch site (operators, `in`, `hash()`, protocol conformance, ...), matching the C++ `requires` clause codegen emits. Resolves through subclass receivers on the method-call path; the operator/conformance sites still check only direct generic receivers there (C++-caught, see BUGS.md).
- **Working**: Single class inheritance (`class Child(Parent)`)
- **Working**: Generic inheritance (`class Child[T](Parent[T])`)
- **Working**: Explicit protocol implementation (`class MyList(Sequence[T])`)
- **Working**: Docstrings in class and method bodies (silently ignored)
- **Working**: Nested class and enum definitions (`class Outer: class Inner: ...`). Arbitrary nesting depth. C++ codegen: nested struct/enum class. Short names resolve inside the class body (`kind: Kind` for `Container.Kind`) for CPython compatibility. Nested classes and enums inside generic parents are not supported. Nested protocols not supported.
- **Working**: `@dataclass` decorator (`from dataclasses import dataclass`) -- auto-generates `__init__`, `__eq__`, `__repr__`, and `__hash__` (frozen only) from field annotations; `frozen=True` for immutable instances usable as dict keys; `order=True` for lexicographic comparison via `operator<=>`; `field(default=X)` and `field(default_factory=X)` for mutable defaults; `__post_init__` called at the end of the synthesized `__init__`; dataclass inheritance (child inherits parent fields into all synthesized methods)
- **Working**: `@classmethod` → alternate constructors. `cls` names the *defining* class (it is not a value and carries no type): `cls(...)` constructs it, `cls.NAME` reads its class constants / `ClassVar`s, `cls.other(...)` calls its static- and classmethods, and `-> Self` resolves to it. C++ codegen: a static member function, exactly as `@staticmethod` -- `cls` leaves no trace. Callable through the class or an instance (`Point.origin()`, `p.origin()`), as in Python. Because `cls` binds statically, reaching an *inherited* classmethod through a subclass -- by class name (`Derived.make()`) or through an instance (`derived_obj.make()`) -- is a compile error (CPython would bind the subclass and construct it, TPy would construct the base) -- override the classmethod on the subclass, or call it on the defining class. Note the override itself warns that it *hides* the parent's method -- a `Shape` reference still calls `Shape.make`, TPy's static dispatch rather than Python's dynamic one -- so the per-class factory works but is not a virtual one. The receiver parameter must be spelled `cls` (the same fixed-vocabulary rule instance methods follow for `self`). Using `cls` as a value (`return cls`, `f(cls)`, `isinstance(x, cls)`) needs `type[T]` and is rejected; so are `@classmethod` on a dunder, a nested class, in a `Protocol` body, on an enum, combined with `@staticmethod` / `@override` / `@property` / `@auto_readonly`, as a generator, or `async`. `@readonly` is *accepted* and is a no-op (there is no receiver to make const, so the method still emits as a plain static) -- the same split `@staticmethod` already has, where `@readonly` is allowed and `@auto_readonly` is an error.
- **Working**: `@property` decorator for computed attributes. Getter: `@property def x(self) -> T`. Setter: `@x.setter def x(self, value: T) -> None`. C++ codegen: getter emits `T x() const`, setter emits `void set_x(T)`. **A property READ is a method call at every position, from the moment sema resolves it**: sema turns the access node INTO the getter call, so no consumer keys on its node kind. It therefore gets its spelled-method twin's verdict wherever the two facts agree -- with TWO declared asymmetries, both about what the getter LENDS. (1) A read off a TEMPORARY receiver (`mk().items`) is a located reject at every BINDING position -- a local decl, a call argument, a `return`, a `with` manager, a `match` subject, a field write, a `d[k] = ...` value, a tuple or container element, a frame or global slot, a for-each or comprehension iterable -- while TRANSIENT reads, the ones that finish inside the full expression, stay admitted: `print(mk().items)`, `len(mk().items)`, `mk().items[0]`, `f"{mk().name}"`, `mk().items.count(1)`. An ARGUMENT is the one position with a second question, because it depends on the CALLEE: the lend is admitted only when the callee provably READS the argument during the call, which sema answers per parameter (`return_borrows_from`, `addr_escapes_params`) and a body-less stub answers from its declared signature. So `len(mk().items)`, `sum`, `any`, `sorted`, `str()`, `list()` and a user function taking a view are admitted, while `reversed`, `enumerate`, `zip`, `map`, `filter` and every generator or coro factory reject -- each of those RETAINS what it was given (a lazy iterator, or the frame). The workaround is to bind the receiver first -- `h = mk()` then `v = h.items` -- which is what makes the lifetime the user's to see. The spelled method is a conceded warn-and-emit at the same positions (it warns, and the render dangles); the getter can hand back storage that outlives the receiver, which CPython aliases, so neither owning a copy nor binding a reference is right, and the method tier is a separate decision (`BUGS.md#readonly-borrow-of-temporary-receiver`). (2) A read whose getter lends the receiver's storage is rejected at an aggregate ELEMENT slot (`t = (b.rec, 1)`, `[b.rec for i in range(1)]`, a dict key or value, a set element) even off a NAMED receiver: the element slot has to pick between copying and aliasing, CPython aliases, and a C++ container cannot hold a reference. Bind first there too. Two positions reject for reasons that are not about lending. A getter as the RECEIVER of an augmented assignment (`b.rec.x += 1`) is rejected: the lvalue is reached through the read and the render spells the target twice, so the getter would run twice where Python runs it once -- held rather than fixed, because evaluation order is one postponed decision and the spelled-method twin shares it (`BUGS.md#augassign-call-receiver-double-eval`). And the four positions that never learned the STORAGE-REF return convention -- `print(s.o)`, `s.o.bump()`, `s.o.x` and a ternary arm, on a getter whose return is a pointer-repr `Optional[T]` -- are located rejects for want of a lift, not wrong code (TODO.md, "Four positions REJECT a storage-ref `@property` read"). A read bound off a receiver that is itself rebound inside a LOOP takes the field read's answer, which is what this unit gave it: `for i in ...: b = B(i); holder = b.rec` warns that `holder` will not keep the object it was given and hoists the receiver to one function-scope slot, and `for b in bs: holder = b.rec` compiles clean -- both were located THIR rejects (`decl.reseat_source`) for the getter spelling before, while the field spelling answered as it does now. The third shape in the family is a located error in BOTH spellings and was one before as well: `for i in ...: b = bs[i]; holder = b.rec` -- where the receiver ALIASES storage that already outlives the loop -- reports *"reference to 'b' may outlive its storage; use copy(b) for an independent value"*, because hoistability is decided from the local's block depth rather than from what it aliases. That one is the escape analysis's limit rather than a property one, and the symmetry with the field read is the point. Workarounds there: `for b in bs:`, whose loop variable inherits the container's depth, or `holder = copy(b.rec)`; `b = copy(bs[i])` is refused for an unrelated reason (`BUGS.md#copy-in-hoisted-loop-decl-unlowered`). In the other direction, `yield self.<getter>` inside the owning record lends the receiver's storage exactly as `yield self.<field>` does, so a consumer's mutation reaches the field. The `Own[...]`-returning flavour of the same yield hands out the value the getter built instead, so a consumer's mutation does NOT reach the field -- the slot follows the getter's declared return convention, not the syntax. Only the WRITE position is property-specific -- a settable property on the left of an assignment keeps the field spelling so the setter can be looked up by name -- together with two conventions on the getter itself: its pointer-repr Optional and Union returns come back as the FIELD's storage by reference (`std::optional<T>&`, `::tpy::Union<A, B>&`) where a method returns the borrow form, and the `@auto_readonly` const twin. A property read is NOT narrowed by an `is not None` guard -- a getter is a call, and so is the spelled-method twin -- so `if w.num is not None: s = w.num` binds `s` as the Optional rather than its payload, where a stored FIELD would narrow. Bind once instead: `n = w.num` then `if n is not None: ...` narrows the local (TODO.md, "Narrowing over a pure zero-arg getter"). Read-only properties (no setter) produce a compile error on assignment. Properties are inherited. Borrow tracking works through properties via `return_borrows_from` -- `v = obj.prop` registers a borrow on the receiver, so subsequent mutations warn. A property whose getter returns a freshly-owned string/bytes (`-> str` via `decode()`/concatenation) promotes the caller's inferred local to an owned copy, the same as a method call would -- the property read is a getter call, not stable field storage, so it does not bind a dangling view into the returned temporary. Augmented assignment on properties (`obj.x += 1`) not yet supported. `@x.deleter` not supported.

### Generic Classes

**Working**: Generic classes using Python 3.12+ syntax (`class Stack[T]:`)

```python
class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

class Pair[A, B]:
    first: A
    second: B

    def __init__(self, first: A, second: B) -> None:
        self.first = first
        self.second = second

# Instantiation - type arguments can be explicit or inferred
box: Box[int32] = Box[int32](42)  # Explicit
box2 = Box(42)                    # Inferred as Box[int] from argument
pair = Pair(1, "hello")           # Inferred as Pair[int, str]

# Generic methods use substituted types
print(box.get())  # Returns int32
print(pair.first) # Type is str
```

Generated C++ (template structs):
```cpp
template<typename T>
struct Box {
  T value;
  explicit Box(const T& value) : value(value) {}
  T get() const { return this->value; }
};

template<typename A, typename B>
struct Pair {
  A first;
  B second;
  explicit Pair(const A& first, const B& second) : first(first), second(second) {}
};

// Instantiation
Box<int32_t> box{42};
Pair<std::string, int32_t> pair{"hello", 100};
```

**Type Inference**:
- Type arguments can be inferred from constructor arguments: `Box(42)` -> `Box[int]`
- Inference works when all type parameters can be determined from arguments
- Contextual inference from assignment annotation, return type, reassignment, or nested call context fills unresolved params
- If inference fails, explicit type arguments are required
- When mixing int literals with `int32`, inference upgrades to `int32`: `Same(1, x: int32)` → `Same[int32]`
- Supports inference through wrapper types: `Ptr[T]`, `Ptr[readonly[T]]`, `Own[T]`, `list[T]`
- `Ptr[T]` arguments match `Ptr[readonly[T]]` parameters (follows coercion rules)

**Generic Static Methods**:

Static methods on generic classes can use the class type parameters. Type arguments are inferred from call arguments or specified explicitly with `ClassName[TypeArgs].method(...)` syntax:

```python
class Box[T]:
    value: T
    def __init__(self, value: Own[T]):
        self.value = value

    @staticmethod
    def from_optional(v: Own[T] | None) -> Own[Box[T]] | None:
        if v is not None:
            return Box(v)
        return None

b1 = Box.from_optional(42)              # Inferred: T = int
b2 = Box[int32].from_optional(None)     # Explicit: T = int32
```

Generated C++: `Box<int32_t>::from_optional(42)`, `Box<int32_t>::from_optional(std::nullopt)`.

### Integer Type Parameters

**Working**: Generic classes can have integer type parameters using Python 3.12+ syntax with `: int` bound:

```python
from tpy import int32

class Container[T, N: int]:
    size: int32

    def __init__(self) -> None:
        self.size = int32(N)

    def get_capacity(self) -> int32:
        return int32(N)

# Instantiation with integer type argument
c: Container[str, 10] = Container[str, 10]()
print(c.size)          # 10
print(c.get_capacity()) # 10
```

Generated C++ (non-type template parameters):
```cpp
template<typename T, std::size_t N>
struct Container {
  int32_t size;

  Container() : size(N) {}

  int32_t get_capacity() {
    return N;
  }
};

// Instantiation
Container<std::string, 10> c{};
```

**Key points:**
- Use `: int` bound to declare integer type parameters: `class Foo[T, N: int]`
- Integer type parameters become `std::size_t` template parameters in C++
- Can use `int32(N)` to convert the integer constant to int32 inside methods
- Integer type arguments are literal integers in instantiations: `Container[str, 10]`

**Forwarding integer type parameters in inheritance:**

```python
class Base[T, N: int]:
    value: T

class Child[T, N: int](Base[T, N]):
    pass  # N is forwarded to parent

c: Child[str, 20] = Child[str, 20]()
```

Generated C++:
```cpp
template<typename T, std::size_t N>
struct Child : Base<T, N> {
  // N is forwarded to parent template
};
```

**CPython compatibility:**
- Using `N` in methods works in CPython via `__orig_class__` (available after `__init__`)
- Using `N` in `__init__` (e.g., `self.size = int32(N)`) is **TurboPython-only** - CPython cannot access type args during construction
- `Array[T, N]` with forwarded N works as type annotation, but instantiation in `__init__` is TurboPython-only
- Tests using `N` inside `__init__` cannot be validated against CPython

### Class Inheritance

**Working**: Single class inheritance with optional protocol implementations.

```python
from tpy import int32
from typing import Protocol

# Base class
class Animal:
    name: str
    age: int32

    def __init__(self, name: str, age: int32) -> None:
        self.name = name
        self.age = age

    def speak(self) -> str:
        return "..."

# Child class inheriting from Animal
class Dog(Animal):
    breed: str

    def __init__(self, name: str, age: int32, breed: str) -> None:
        super().__init__(name, age)  # Call parent constructor
        self.breed = breed           # Initialize own field

    def speak(self) -> str:  # Override parent method
        return "Woof!"

# Usage
d = Dog("Buddy", 3, "Golden Retriever")
print(d.name)      # Access inherited field: "Buddy"
print(d.speak())   # Call overridden method: "Woof!"
```

Generated C++:
```cpp
struct Animal {
  std::string name;
  int32_t age;
  Animal() = default;
  explicit Animal(std::string_view name, int32_t age) : name(name), age(age) {}
  std::string speak() { return "..."; }
};

struct Dog : Animal {
  std::string breed;
  Dog() = default;
  explicit Dog(std::string_view name, int32_t age, std::string_view breed)
    : Animal(name, age), breed(breed) {}  // Base init + field init
  std::string speak() { return "Woof!"; }
};
```

**Key points:**
- Single and multiple class inheritance both supported (static MI, non-virtual; see below)
- Use `super().__init__(args)` to call the parent constructor
- An empty subclass (`class B(A): pass`) inherits the parent's `__init__` -- `B(...)`
  accepts whatever `A(...)` does. The child must declare neither `__init__` nor
  instance fields; the parent may be a regular class or an `@native` class such
  as `Exception`. Mixin shapes also work: `class C(Base, Mixin): pass` picks
  `Base.__init__` as long as `Mixin` contributes no `__init__` of its own
  (CPython's MRO ctor lookup, with sister bases default-constructing). Two or
  more init-bearing parents stay rejected -- define `C.__init__` and invoke
  each base explicitly. A generic-base instantiation works too: `class Sub(Base[14])`
  inherits `Base`'s `__init__` with the base's type params substituted from the
  instantiation. A bare unbound generic base (`class Sub(Base)`) is still rejected.
- Method override works by simply defining a method with the same name
- Inherited fields and methods are accessible via `self.field` and `self.method()`
- Use `@override` (from `typing`) to explicitly annotate overrides -- errors on typos,
  warns when the override is non-polymorphic (see `@override` below)

#### Multiple Inheritance (D22)

**Working**: Static multiple inheritance -- `class Widget(Named, Counted): ...` emits
`struct Widget : Named, Counted { ... }`. C3 linearization (MRO) is computed at
sema-registration time and drives `isinstance`, method lookup, and field inheritance.

```python
class Named:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        return self.name

class Counted:
    count: int32

    def __init__(self, count: int32) -> None:
        self.count = count

    def inc(self) -> None:
        self.count = self.count + 1

class Widget(Named, Counted):
    def __init__(self, name: str, count: int32) -> None:
        # Every base with __init__ must be invoked explicitly.
        Named.__init__(self, name)
        Counted.__init__(self, count)

w = Widget("button", int32(5))
print(w.describe())     # inherited from Named
w.inc()                 # inherited from Counted
isinstance(w, Named)    # True -- folded at compile time
isinstance(w, Counted)  # True -- folded at compile time
```

**`BaseN.method(self, ...)`** calls an ancestor class's instance method explicitly,
mirroring CPython's unbound-method form. The first argument must literally be `self`
(not an arbitrary expression), and `BaseN` must be a strict ancestor of the current
record. Method lookup walks `BaseN`'s own MRO, so inherited methods resolve even when
`BaseN` doesn't declare them directly. Use cases:

- Disambiguate when both bases define the same method (the child's override can
  still call `Left.describe(self)` and `Right.describe(self)` to compose).
- Call every base's `__init__` when more than one base defines one.
- Prefer over `super()` when `super()` would be ambiguous across bases.

```python
class Left:
    def describe(self) -> str: return "left"

class Right:
    def describe(self) -> str: return "right"

class Both(Left, Right):
    def describe(self) -> str:
        # Cross-base conflict requires an override; delegate via the unbound form.
        return Left.describe(self) + "+" + Right.describe(self)
```

**`BaseN.field`** accesses an ancestor's field slot on the current instance
(read, write, and augmented assignment), parallel to `BaseN.method(self, ...)`.
Same-name fields across ancestors are legal -- each subobject keeps its own
storage -- and `BaseN.field` is how the user distinguishes them.

```python
class RateLimiter:
    count: int32

class CacheStats:
    count: int32

class Service(RateLimiter, CacheStats):
    def tick_req(self) -> None:
        RateLimiter.count += 1          # writes RateLimiter's subobject slot
    def tick_cache(self) -> None:
        CacheStats.count = CacheStats.count + 1
```

Unqualified `self.count` in `Service` is rejected with an ambiguity error
listing the contributing ancestors. `BaseN` must be a strict ancestor; field
lookup walks `BaseN`'s own MRO so `BaseN.field` resolves fields `BaseN`
inherits from *its* ancestor.

**Rules and restrictions** (rejected at sema with targeted diagnostics):
- **Diamonds** (`class D(B, C)` where `B(A)` and `C(A)`) -- rejected; non-virtual C++ MI
  would duplicate the shared subobject. Users who need runtime polymorphism should make
  the shared ancestor a `@dynamic` protocol.
- **Method conflicts** -- two bases defining the same method require the child to override
  that method; the child's version shadows both.
- **Ambiguous `self.field`** -- when more than one direct-parent branch reaches a
  same-name field, unqualified access is rejected; disambiguate with `BaseN.field`
  or add a child override.
- **Field shadowing** (warning, not error) -- a child field that shadows an
  inherited same-name field emits a warning pointing at `AncestorN.field` for
  access to the ancestor's slot. Applies to any inheritance shape.
- **Member sharing a name with a sibling type** (works) -- a member (method,
  field, `@property`, class constant, static method, or a method-parameter
  type) named identically to *another* local type (record, enum, or `@dynamic`
  protocol) is allowed, matching CPython. In C++ the member would shadow the
  same-named type in record scope, so codegen renders references to that type
  fully-qualified (`::tpyapp::mod::day`) inside the colliding record only. This
  is how `datetime.date()` / `datetime.time()` accessors work. Distinct from
  the nested-type collision below (a nested type + a same-*class* member is
  still rejected -- the shadow is unsalvageable there).
- **Nested-type / field name collision** -- a class declaring both a nested
  type and a field/method with the same name is rejected: the generated C++
  is unsalvageable (the member shadows the nested type name, so even
  `Outer::Foo::A` stops naming the type).
- **`__init__` coverage** -- if any base defines `__init__`, the child must define its
  own `__init__` and invoke every such base explicitly via `BaseN.__init__(self, ...)`.
  `super().__init__(...)` covers the MRO-first `__init__` base only, so other
  `__init__` bases still need their own `BaseN.__init__` call.
  Base-init calls must be top-level statements in `__init__`; nesting in control flow
  is rejected. Writing the calls in an order other than declaration order emits a
  warning: C++ always runs base constructors (and their argument expressions) in
  declaration order, so source order is misleading.
- **`BaseN.__init__(self, ...)` scope** -- only legal inside the child's own `__init__`.
- **`BaseN.__del__(self)`** -- rejected outright; C++ invokes each base destructor
  automatically.
- **`super()` in multi-base** -- MRO-aware resolution: `super().method()` walks the
  child's C3 MRO and dispatches to the first ancestor whose *own* method table
  defines `method` (matching Python's `__dict__` walk; inherited methods on an
  ancestor don't count). With `class Child(Speaker, Greeter)`, both defining
  `greet`, `super().greet()` resolves to `Speaker.greet`. If only a grandparent
  defines the method, codegen targets the grandparent directly. See "Limitation:
  single-hop super" below. `super().__del__()` is still rejected in multi-base
  classes (C++ auto-invokes each base's destructor). `super().__init__()` covers
  the MRO-first base with `__init__`; other bases with `__init__` still need
  explicit `BaseN.__init__(self, ...)` calls to satisfy the coverage rule.
- **Source / MRO order** -- direct bases must be declared in an order consistent with
  C3 linearization; mismatches are rejected.

**CPython compatibility note for `BaseN.field`**: this form has no CPython
equivalent. Python's object model has no subobjects -- a class hierarchy
collapses into a single attribute namespace via MRO lookup. `BaseN.method(self, ...)`
works in both because `Base.foo(self)` is a valid Python unbound-method call,
but `BaseN.field = v` in CPython sets a *class attribute* on `BaseN` (shared
across all instances), not a per-instance subobject slot. A program that uses
`BaseN.field` will silently produce different results under CPython the moment
a second instance exists. If CPython parity matters, prefer one of:

- Rename one of the colliding fields in the base (the shadow warning exists
  to nudge you toward this).
- Wrap the ancestor field access in a child `@property` that reads/writes the
  inherited slot by name -- the property body can use `self.field` safely when
  only one branch actually contributes.
- Otherwise, mark the source file with a `no_cpython.txt` marker if you're
  testing under both runtimes, and accept that CPython is not a valid target.

**Limitation: single-hop super()**. v2.3's MRO-aware resolution only applies
at the child's `super()` call site -- it picks the first MRO-ordered ancestor
that defines the method. It does NOT replicate Python's full cooperative
`super()` chain, where `super()` inside an *ancestor* method dispatches based
on the runtime MRO of the most-derived instance. TPy dispatches statically:
`A.foo`'s `super().foo()` is compiled against A's own parents, so if `A.foo`
calls `super().foo()` and A has no further parent with `foo`, the chain stops
at A regardless of whether A is used standalone or as a base of `C(A, B)`. To
chain through multiple ancestors, call them explicitly via `BaseN.method(self, ...)`.

#### Implicit Upcasting

**Working**: Child instances can be used where parent types are expected.

```python
class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed

def greet(a: Animal) -> None:
    print(a.name)

d = Dog("Rex", "Lab")
a: Animal = d        # value upcast -- WARNING: narrows to Animal
greet(d)             # param passing (child -> parent) -- no warning (reference bind)
```

**Value upcast warning**: assigning, initializing, or returning a `Child` value where a `Parent` is expected emits a warning because the upcast narrows the value: only fields and methods declared on `Parent` are accessible through the variable, and method calls resolve to the parent's implementations (differs from Python's dynamic dispatch). For concrete classes, the copy slices subtype-specific fields; for generic parents, the initialization is a C++ reference bind that preserves the source but still narrows access. The upcast is still accepted. To fix, either keep the concrete type (`a: Dog = Dog(...)`) or make the parent a `@dynamic` protocol for runtime polymorphism. Passing `Child` as a `Parent` parameter does not warn because tpyc binds the parameter as a C++ reference (`Parent&`, or `const Parent&` for readonly callees) -- no copy, though method calls still dispatch statically.

At every position whose slot is a POINTER, an upcast of a child LVALUE is the same address bind as the same-type case and aliases the source (mutation through the base handle is visible through the child, as in CPython): a `Parent | None` local (function or generator/`async` frame scope), a re-pointed `Parent` local (`p: Parent = d; p = c`), a pointer-variant union local (`p: Parent | Other = d`, the child's address landing in the unique base member -- a child deriving from TWO members is rejected as ambiguous), a `Parent | None` or `Parent | Other` argument (unwarned, like the plain `Parent` parameter; the union form takes the same unique-base-member rule), and a `Parent | None` return. The narrowing warning still applies where the annotation narrows. The one storage-form position, a field, copies the child into the base slot -- the `copies ... into field` and upcast warnings together name that. A child RVALUE at a field (`h.node = Dog("x")`, plain or `Parent | None`) takes the same slicing copy; a child rvalue at a CONTAINER element is still rejected, and a child rvalue at a pointer-variant union local has no address to bind and is rejected too.

**Pointer coercion** also works -- a child can be used where a pointer to parent is expected:

```python
from tpy import Ptr

def read_animal(p: Ptr[readonly[Animal]]) -> None:
    print(p.name)

d = Dog("Rex", "Lab")
read_animal(d)                    # Dog -> Ptr[readonly[Animal]]
dp: Ptr[Dog] = take_ptr(d)
ap: Ptr[Animal] = dp             # Ptr[Dog] -> Ptr[Animal]
cap: Ptr[readonly[Animal]] = dp  # Ptr[Dog] -> Ptr[readonly[Animal]]
```

Generic parent upcasting is supported with type argument matching:

```python
class IntContainer(Container[int32]):
    ...

ic = IntContainer(int32(42))
c: Container[int32] = ic         # upcast to generic parent
```

**Note:** Covariant containers (`list[Dog] -> list[Animal]`) are not supported -- they are unsafe because the target list could be modified with incompatible types.

#### `super()` Support

**Working**: Python 3-style `super()` for calling parent class constructors and methods.

**Calling parent constructor:**
```python
class Dog(Animal):
    breed: str

    def __init__(self, name: str, age: int32, breed: str) -> None:
        super().__init__(name, age)  # Calls Animal.__init__
        self.breed = breed
```

Generated C++ uses proper base class initializer:
```cpp
explicit Dog(std::string_view name, int32_t age, std::string_view breed)
  : Animal(name, age), breed(breed) {}
```

**Calling overridden parent method:**
```python
class Dog(Animal):
    def speak(self) -> str:
        return "Woof!"

    def full_speak(self) -> str:
        parent_msg = super().speak()  # Calls Animal.speak()
        return parent_msg
```

Generated C++ uses qualified method call:
```cpp
std::string full_speak() {
  std::string parent_msg = Animal::speak();
  return parent_msg;
}
```

**Works with generic parents:**
```python
class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value

class LabeledContainer(Container[int32]):
    label: str
    def __init__(self, label: str, value: int32) -> None:
        super().__init__(value)  # Type-aware: calls Container<int32_t>
        self.label = label
```

**Restrictions:**
- Python 3 style only: `super()` with no arguments
- Must be inside a non-static method
- Class must have a parent class
- `super()` in `@staticmethod` is an error
- `super()` not supported for builtin type parents (`list`, etc.) - use implicit default construction instead

**Working**: Inheriting from generic classes with concrete type arguments:
```python
from tpy import int32

class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value
    def get(self) -> T:
        return self.value

class IntContainer(Container[int32]):
    extra: int32
    def __init__(self, value: int32, extra: int32) -> None:
        self.value = value   # Inherited field, type is int32 (not T)
        self.extra = extra

c = IntContainer(42, 100)
print(c.get())   # Returns int32, not T
```

Generated C++:
```cpp
template<typename T>
struct Container { T value; /* ... */ };

struct IntContainer : Container<int32_t> {
    int32_t extra;
    /* ... */
};
```

**Working**: Generic child classes forwarding type parameters:
```python
from tpy import int32

class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value
    def get(self) -> T:
        return self.value

# Forward type parameter to parent
class Wrapper[T](Container[T]):
    extra: int32
    def __init__(self, value: T, extra: int32) -> None:
        self.value = value
        self.extra = extra

w: Wrapper[str] = Wrapper[str]("hello", int32(42))
print(w.get())  # Returns str - inherited method with forwarded type
```

**Working**: Partial type substitution (mix forwarded and concrete):
```python
class Pair[T, U]:
    first: T
    second: U
    def __init__(self, first: T, second: U) -> None:
        self.first = first
        self.second = second
    def get_first(self) -> T:
        return self.first
    def get_second(self) -> U:
        return self.second

# Forward T, fix U to int32
class IntPair[T](Pair[T, int32]):
    def __init__(self, first: T, second: int32) -> None:
        self.first = first
        self.second = second

p: IntPair[str] = IntPair[str]("hello", int32(42))
print(p.get_first())   # Returns str (forwarded T)
print(p.get_second())  # Returns int32 (fixed U)
```

**Working**: Nested type parameters in inheritance:
```python
class Container[T]:
    value: T
    def get(self) -> T:
        return self.value

# Parent's T is list[Child's T]
class ListContainer[T](Container[list[T]]):
    def __init__(self, value: list[T]) -> None:
        self.value = value

c: ListContainer[str] = ListContainer[str](["a", "b"])
items: list[str] = c.get()  # Returns list[str]
```

**Working**: Inheriting from builtin types with concrete type arguments:
```python
from tpy import int32

class IntStack(list[int32]):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def push(self, value: int32) -> None:
        self.append(value)  # Inherited from list

def main() -> int32:
    stack = IntStack("my_stack")

    # Use inherited methods
    stack.push(int32(10))
    stack.push(int32(20))

    # Use inherited __len__
    print(len(stack))  # 2

    # Use inherited __getitem__
    print(stack[0])    # 10

    # Access own field
    print(stack.name)  # "my_stack"

    return int32(0)
```

**Supported builtin parents:**
- `list[T]` - dynamic list
- `dict[K, V]` - mapping
- `set[T]` - set
- `Array[T, N]` - fixed-size array (planned)

**Key points:**
- Inherited methods from builtins work automatically (e.g., `append`, `__getitem__`, `__len__`)
- Type parameters are substituted with concrete types (e.g., `T` -> `int32`)
- No `super().__init__()` needed - the C++ base default constructor runs automatically.
  Calling it explicitly to seed the base also works (`super().__init__([1, 2, 3])`)
- Can add custom fields and methods to the child class
- Subscript (`stack[i]`) and `len(stack)` work on child types
- A generic child forwarding its parameter works (`class Bag[T](list[T])`,
  instantiated as `Bag[int32]()`)

**Limitations:**
- Generic parent without type args rejected (`class Child(Parent)` where `Parent[T]` is generic)
- The child must declare an `__init__`. A bodyless child (`class Child(list[int32]): pass`)
  emits an inherited-constructor declaration spelled with the TPy type name and fails to
  compile
- `super().<method>()` into a builtin base does not compile: the call spells the TPy method
  name against the C++ base type, ignoring the stub's native rename. Call the method on
  `self` instead, or rename the override
- Operator protocols do not walk to the builtin base: `x in child` is rejected on a `set`-
  or `dict`-inheriting child (and iteration behaves the same way) even though the plain
  method forms (`.add()`, `.discard()`, `.get()`) resolve correctly

#### `@override` Decorator

**Working**: The `@override` decorator from `typing` (PEP 698) marks methods that intentionally override a parent
class or protocol method. The compiler verifies that the annotated method actually overrides something, turning
typos and stale overrides into hard errors.

```python
from tpy import int32
from typing import override

class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def speak(self) -> str:
        return "..."

class Dog(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)

    @override
    def speak(self) -> str:  # OK: overrides Animal.speak
        return "Woof!"

    @override
    def speek(self) -> str:  # ERROR: 'Dog.speek' is marked @override but does not override any parent class or protocol method
        return "Typo!"
```

**Three dispatch contexts:**

| Context | `@override` effect |
|---------|-------------------|
| Regular class inheritance | Validates override exists; emits non-polymorphic dispatch warning |
| Static `Protocol` | Validates method is in the protocol; no warning (structural conformance) |
| `@dynamic` protocol | Validates method is in the protocol; no warning (virtual dispatch) |

**Non-polymorphic dispatch warning:**

TPy class inheritance uses C++ static (non-virtual) dispatch. A parent-typed reference always calls the parent
method, regardless of the runtime type. `@override` triggers a warning to make this visible:

```python
class Dog(Animal):
    @override
    def speak(self) -> str:       # WARNING: override is non-polymorphic; Animal-typed references
        return "Woof!"            #          will call Animal.speak, not Dog.speak
                                  #          Use a @dynamic protocol for runtime dispatch.
```

No warning is emitted when the class already implements a `@dynamic` protocol that covers the same method
(runtime dispatch is already in play via the vtable).

**Protocol overrides (no warning):**

```python
from typing import Protocol, override
from tpy import dynamic

class Speakable(Protocol):
    def speak(self) -> str: ...

@dynamic
class DynamicSpeakable(Protocol):
    def speak(self) -> str: ...

class Cat(Speakable, DynamicSpeakable):
    @override
    def speak(self) -> str:  # OK: satisfies both protocols, no non-polymorphic warning
        return "Meow!"
```

**Duplicate method error:**

Defining the same method name twice in one class is always a compile error:

```python
class Bad:
    def foo(self) -> None: ...
    def foo(self) -> None: ...  # ERROR: Method 'foo' defined twice in class 'Bad'
```

**Restrictions:**
- `@override` cannot be combined with `@staticmethod`
- `@override` does not take arguments

### Explicit Protocol Implementation

**Working**: Classes can declare protocol implementations explicitly.

```python
from tpy import int32
from typing import Protocol

# Define a protocol
class Printable(Protocol):
    def __str__(self) -> str:
        ...

# Explicit protocol implementation
class Person(Printable):
    name: str
    age: int32

    def __init__(self, name: str, age: int32) -> None:
        self.name = name
        self.age = age

    def __str__(self) -> str:  # Required by Printable
        return self.name
```

The compiler validates that declared protocols are actually implemented:
```python
class BadPerson(Printable):  # ERROR: missing required methods: __str__
    name: str
```

**Multiple protocol implementations:**
```python
class Describable(Protocol):
    def describe(self) -> str: ...

class Measurable(Protocol):
    def size(self) -> int32: ...

# Implement multiple protocols
class Box(Printable, Describable, Measurable):
    width: int32
    height: int32

    def __str__(self) -> str:
        return "Box"

    def describe(self) -> str:
        return "A rectangular box"

    def size(self) -> int32:
        return self.width * self.height
```

**Combined class inheritance and protocol implementation:**
```python
# Inherit from class AND implement protocols
class Car(Vehicle, Printable, Measurable):
    model: str

    def __init__(self, brand: str, year: int32, model: str) -> None:
        self.brand = brand  # From Vehicle
        self.year = year    # From Vehicle
        self.model = model

    def __str__(self) -> str:
        return self.model

    def weight(self) -> int32:
        return 1500
```

**Rules:**
- Multiple class parents allowed (see "Multiple Inheritance (D22)" above for restrictions)
- Multiple protocol implementations allowed
- Class parents must come before protocols in the base list: `class Child(Parent, Protocol1, Protocol2)`
- All declared protocol methods must be implemented (compiler validates)
- Protocols provide no implementation - they're pure interfaces

### Special Methods
- **Working**: `__init__`
- **Working**: `__eq__`, `__ne__`, `__lt__`, `__le__`, `__gt__`, `__ge__` → C++ `friend` comparison operators. Sema errors if comparing user records without the required dunder. `!=` is auto-synthesized from `__eq__` via C++20 rewriting.
- **Working**: `__add__`, `__sub__`, `__mul__`, `__truediv__`, `__floordiv__`, `__mod__`, `__and__`, `__or__`, `__xor__`, `__lshift__`, `__rshift__` → C++ `friend` binary operators. **Exception**: `__floordiv__`/`__rfloordiv__` lower to a direct `.__floordiv__()`/`.__rfloordiv__()` method call, not a friend `operator/` -- C++ has no `//`, and reusing `operator/` would collide with `__truediv__`, so a type may define both `/` and `//` distinctly (e.g. `timedelta`).
- **Working**: reflected operators `__radd__`, `__rsub__`, `__rmul__`, `__rtruediv__`, `__rfloordiv__`, `__rmod__`, `__rand__`, `__ror__`, `__rxor__`, `__rlshift__`, `__rrshift__` → C++ `friend` binary operators with the operand order swapped (the record is the right operand). When `left OP right` has no viable `left.__OP__(right)`, sema dispatches to `right.__rOP__(left)` -- enabling `builtin OP user-type` (e.g. `1.5 * dur` where `Dur.__rmul__(float)` is defined). No reflected comparisons (Python reflects those by swapping `__lt__`/`__gt__`) and no `__rpow__` (pow is `std::pow`, not an operator).
- **Working**: `__neg__`, `__pos__`, `__invert__` → C++ `friend` unary operators (`-x`, `+x`, `~x`)
- **Working**: borrow-returning operator dunders. A binary/reflected/unary dunder returning `-> Cls` or `-> readonly[Cls]` hands out an ALIAS of `self`/the operand: `c = a + b` binds a `const Cls&` (mutating the source afterwards is visible through `c`, matching CPython's aliasing), and the friend-operator shims mirror the method's const-projected signature. Arithmetic dunders take a readonly receiver by rule, so the borrow is readonly: mutation THROUGH the result is rejected (`-> readonly[Cls]` gets a located sema error; the plain spelling is currently toolchain-caught -- see BUGS.md) and `-> Own[Cls]` is the escape hatch returning a fresh, mutable copy. Binding a borrow result over TEMPORARY operands warns (the borrow dies at end-of-statement). The same signature mirroring makes an arg-mutating `__call__` and a reference-key-returning `__getitem__` (`def __getitem__(self, k: Node) -> Node: return k`, aliasing the key) compile through their `operator()` / `operator[]` shims.
- **Working**: `__iadd__`, `__isub__`, `__imul__`, etc. → in-place mutation (`+=`, `-=`, `*=`, etc.). Must return `self` (like Python). Generates C++ `T&` return with `return *this`. Params are `const` (rvalue-safe).
- **Working**: `__contains__` → `in` / `not in` operator (user-defined membership test)
- **Working**: `__hash__` → `std::hash<T>` specialization (enables use as dict key, in sets)
- **Working**: `__len__` → `__len__()` method (used by `len()`). A negative return value raises `ValueError("__len__() should return >= 0")` -- catchable -- matching CPython's length-slot validation for every consumer (`len()`, truthiness `if obj:`, `reversed()`); the guard lives in the runtime `tpy::__len__` free function they all route through. Also auto-generates `size_t size() const` for STL compatibility (which carries the same negative guard); warns if a user-defined `size` field/method shadows it.
- **Working**: `__getitem__` → `operator[]` (for `Sequence` conformance)
- **Working**: `__del__` -- maps to C++ destructor `~ClassName()`. Parent destructors are called automatically after child body (no `super().__del__()` needed). If `super().__del__()` is written (Python style), it must be the last statement and is silently dropped in codegen. A warning is emitted when parent has `__del__` but child omits the call, since C++ always calls parent dtors automatically while Python requires an explicit call. Non-virtual; virtual dispatch is a separate feature. Classes with `__del__` get a hidden `__tpy_owned_` drop flag and custom move constructor/assignment to prevent double-drop after move -- the moved-from object's destructor skips its body.
- **Working**: `__str__` -> `str(obj)`, `print(obj)`, `f"{obj}"` (via `Stringable` protocol / `tpy::__str__`). `Stringable` is also satisfied by any type with only `__repr__` -- mirrors Python's default `object.__str__` -> `__repr__` fallback; the C++ runtime picks whichever dunder is defined.
- **Working**: `__repr__` -> `repr(obj)`, `f"{obj!r}"` (via `Representable` protocol / `tpy::__repr__`)
- **Working**: Default record printing matches CPython: plain classes print as `<ClassName object at 0xADDR>`. `@dataclass` classes print fields (via macro-generated `__repr__`). Custom `__str__`/`__repr__` always takes priority.
- **Working**: Container element printing dispatches through `__repr__` (CPython-faithful). `print(rec)` uses `__str__` first; `print([rec])`, `print((rec,))`, `print({k: rec})` use `__repr__` per element, falling back to the default `<ClassName object at 0xADDR>` form for records without `__repr__`.
- **Working**: `__call__` → C++ `operator()` (callable objects). Classes with `__call__` can be invoked with `obj(args)` syntax and passed to `Fn`/`Callable` parameters. Supports `@readonly`, mutable state, and recursive `self(args)` calls.
- **Working**: `__setitem__` / `__delitem__` -> `::tpy::__setitem__()` / `__delitem__()` free function dispatch. Works for builtin types (list, dict, Array, ArrayList) and for user-defined records via `obj[key] = value` / `del obj[key]`, with an integer *or* non-integer (e.g. str) key dispatching to the user method. (A record with `__setitem__`/`__delitem__` but no `__getitem__` is still rejected by subscript sema -- see BUGS.md.)
- **Working**: `__enter__`, `__exit__` -> context manager protocol for `with` statement. Duck-typed: sema validates methods exist, codegen emits try/catch cleanup. See [with statement](#with-statement-context-managers).
- **Working** (D16 v1 + v1.5 phases 7-9): `__getattr__`, `__setattr__`, `__delattr__` -- user-defined dynamic attribute access. `__getattr__(self, name: str) -> T` is fallback-only: called for `obj.foo` and `getattr(obj, "foo")` when `foo` is not a declared field/method/property/class-constant. Missing-attribute is signaled via `raise AttributeError(name)` (throw-tier; catchable via `try/except AttributeError`). `__setattr__(self, name: str, value: V) -> None` is fallback-only: declared-field writes bypass it (intentional CPython divergence -- code that defines `__setattr__` and writes declared fields in `__init__` recurses under CPython but works under TPy; tests using this shape are TPy-only). `__delattr__(self, name: str) -> None` is fallback-only: `del obj.foo` and `delattr(obj, "foo")` route through it. Return-type allow-list: value type, `Any`, or `Own[T]`. Builtin `getattr` / `setattr` / `delattr` / `hasattr` accept both literal and runtime names. Literal name: declared-member names rejected (use direct access); `hasattr` and 3-arg `getattr` wrap the dunder call in a try/catch lambda IIFE that converts the throw to a boolean / default; declared-member names fold to compile-time True / direct access. Runtime name: routes unconditionally to the dunder (Option A; CPython divergence documented). See [docs/DYNAMIC_ATTRS_DESIGN.md](DYNAMIC_ATTRS_DESIGN.md).

---

## Built-in Functions

- **Working**: `print()`, `len()`, `hash()`, `range()`, `chr()`, `ord()`, `pow()`, `round()`, `divmod()`, `next()`, `copy()` -- most builtins (`len`, `repr`, `hash`, `chr`, `ord`, `abs`, `min`, `max`, `pow`, `divmod`, `next`) defined in `lib/tpy/builtins/` via `@native`/`@cpp_template`
  - `print(*args, sep=" ", end="\n", file=sys.stdout, flush=False)`. `sep`/`end` accept a string literal, a string-typed name, or a computed string expression (`sep=s + "!"`, `sep=make()`), which is evaluated once. A computed separator needs every positional argument to be a literal, a name or a plain field read: a call-valued argument beside it is refused with a located "not yet supported" message, since the chain would run it after the separator (see BUGS.md `subexpression-right-to-left-eval`). A `StrView`-typed computed source is refused too -- the binding outlives the expression the view may point into. `file=` accepts any value satisfying the `Writable` protocol (`write(str) -> int32` + `flush() -> None`); `sys.stdout`, `sys.stderr`, `open(...)` results, and user records all qualify. `flush=` requires a bool literal.
  - Container printing matches Python format: bools as `True`/`False`, floats with `.0`, strings in `'quotes'`
  - `input()` / `input(prompt)` read one line from stdin (trailing newline stripped) and return `String`. The prompt is written to stdout without a newline and flushed before the read; EOF raises `EOFError`, like CPython
- **Working**: `str()`, `repr()`, f-strings on containers (tuple, list, dict, Array, Span) -- uses runtime to_str helpers matching `print()` format
  - Generic type parameters use `ValuePrinter` for runtime dispatch (bool/float correctly formatted)
- **Working**: List methods: `append()`, `pop()`, `insert()`, `remove()`, `clear()`, `extend()`
  - **Note**: `remove(value)` silently does nothing when value not found (Python raises `ValueError`)
  - `pop()` / `pop(index)` return `Own[T]` (ownership transfer), matching `dict.pop` / `set.pop`. For reference-type T this means the popped element is moved out cleanly; for value-type T the `Own[T]` resolves to plain `T` and the call is equivalent to a direct value return.
- **Working**: List repetition: `[element] * N` and `[elements...] * N`
  - Elements are evaluated once and copied into each slot (CPython aliases the
    same object instead); a non-copyable element (`@nocopy` or `__del__`-bearing
    record) is rejected with a sema error suggesting a per-slot comprehension
  - Deferred resolution for untyped locals via `PendingListType`:
    - Constant count, unmutated -> `Array[T, N]` (stack-allocated, supports
      subscript), built by aggregate construction via `tpy::array_from_index`
      (no buffer default-construction; multi-element repeats index `i % k`)
    - Variable count, unmutated -> lazy `repeat[T]` (`tpy::repeat_range<T>`, no allocation)
    - Variable count with subscript -> auto-promoted to `list[T]`
    - Mutated (`.append()`, `+=`, concat operands, etc.) -> auto-promoted to `list[T]`
  - Lazy `repeat[T]` conforms to `Iterable[T]`, `Sized`, `NativeIterable[T]`
  - Explicit annotation (`z: list[T] = [v]*N`) always produces `list[T]`
  - Inline repeat cannot be passed directly to `Span` -- assign to a variable first
- **Working**: Negative indexing for list, Array, Span: `items[-1]` (last element)
- **Working**: `hash(x)` → `uint64` hash value. Works on all `Hashable` types (str, int, fixed ints, float, bool, char, Enum). Uses `tpy::__hash__()` free function dispatch.
- **Working**: `abs()`, `min()`, `max()`, `pow()`, `round()`, `divmod()` for numeric types. `abs(x)` also dispatches to a user type's `__abs__` (mirroring CPython's `abs()` -> `x.__abs__()`).
- **Working**: `sorted()` / `min()` / `max()` with a `key=` callable, including inside a generic function over a generic-element container (`def ranked[T](pairs: list[tuple[T, int32]]): return sorted(pairs, key=lambda p: -p[1])`) for **value-type** keys (str/int/...). Sort is stable; `min`/`max` return the first element on a tie (CPython parity).
  - **Limitation**: a `key=` lambda over a *reference-type* generic element (`T` a class/record) fails the C++ build (borrow-vs-storage tuple-form mismatch); see BUGS.md. Value-type keys are unaffected.
- **Working**: `ord(c)` accepts `char` (zero-cost) and `str` (runtime length-1 check; raises `TypeError` -- catchable -- with CPython-aligned `expected a character, but string of length N found`)
  - `round(x)` uses banker's rounding (round half to even, matching Python)
  - `round[T](x)` is generic: return type defaults to `default_int`, can be inferred from context
  - `divmod(a, b)` returns `tuple[T, T]` with Python floor-division semantics
- **Working**: String slicing: `s[1:3]` -> `StrView` (zero-copy), `s[::2]` -> owned `str`. Python clamping semantics, negative indices, negative step
- **Working**: Container slicing: `items[1:3]` -> `Span[T]` (zero-copy), `items[::2]` -> owned `list[T]`. Supports `list[T]`, `Array[T,N]`, `Span[T]`, `Span[readonly[T]]`
- **Working**: Bytes slicing: `b[1:3]` -> `BytesView` (zero-copy), `b[::2]` -> owned `bytes`
- **Working**: User-type slicing via `@overload __getitem__(self, index: basic_slice)` or `__getitem__(self, index: slice)`. `basic_slice` has `start`/`stop` (`Optional[int32]`), maps to `tpy::BasicSlice`. `slice` adds `step`, maps to `tpy::Slice`. `basic_slice` coerces to `slice`
- **Working**: `basic_slice(start, stop)` and `slice(start, stop, step)` constructors. Args are `int32 | None`. `slice` is a Python builtin (no import needed), `basic_slice` requires `from tpy import basic_slice`
- **Working**: `isinstance(x, T)` → compile-time type narrowing for union types (`std::holds_alternative<T>` + `std::get<T>`)
- **Working**: `isinstance(x, Protocol)` → compile-time protocol check on protocol-typed template params (`if constexpr (Concept<T_x>)`)
- **Open**: `type()` → compile-time type info
- **Working**: `list()` → empty list constructor (requires type annotation), `list(iterable)` from Iterable containers, `list(range(...))`, `list(iterator)` from Iterator
- **Working**: `int(float)` → truncates toward zero; raises `ValueError` on NaN, `OverflowError` on infinity (both catchable; messages match CPython)
- **Working**: `float(int)`, `float(int32)` → converts to float
- **Working**: `str()` → string conversions for scalars, containers, and any type with `__str__` or `__repr__` (see below)
- **Working**: `int(str)` → string-to-int parsing (via `BigInt::from_str`)
- **Working**: `iter(x)` → calls `x.__iter__()`, returns `Iterator[T]`
- **Working**: `make_default[T]()` / `make_default()` → default-constructs `T` (maps to `T{}` in C++). Requires `T: Default`. Type can be explicit or inferred from context. Portable alternative to `T()`.
- **Working**: `open(path)`, `open(path, mode)` -> `TextIO` or `BinaryIO` file handle. Text modes (`"r"`, `"w"`, `"a"`, `"x"` and variants) return `TextIO` with methods: `read(size=-1)`, `write()`, `readline()`, `readlines()`, `close()`. Binary modes (`"rb"`, `"wb"`, `"ab"`, `"xb"` and variants) return `BinaryIO` with methods: `read(size=-1)`, `readline()`, `readlines()`, `write()`, `close()`. `read(size)` returns at most `size` bytes (`size < 0` reads all remaining). Mode dispatch uses `Literal` string overloads -- binary vs text is resolved at compile time. Context manager (`with open(...) as f:`). Variable (non-literal) mode falls back to `TextIO`. Raises the errno-mapped `OSError` subclass (catchable) when the file cannot be opened -- `FileNotFoundError` for a missing path, `PermissionError` on EACCES, etc., via the shared PEP 3151 table, with `.errno`/`.strerror`/`.filename` populated and CPython-exact `str(e)` text -- `ValueError` (catchable) on an unsupported mode string, and `OSError` (catchable) when an operation does not match the file's mode (e.g. `f.read()` on a write-only handle; CPython raises `io.UnsupportedOperation`, which is `OSError + ValueError` via diamond MI -- TPy routes to `OSError` alone since diamond inheritance is not supported). One known hole in the errno mapping: opening a DIRECTORY succeeds instead of raising `IsADirectoryError` as CPython does (filed in `BUGS.md`), so a `try`/`except OSError` around `open()` does not catch that case. `readline()`/`readlines()` preserve trailing newlines (Python compat). TPy-specific alternatives: `open_text(path)` / `open_text(path, mode)` -> `TextIO` and `open_binary(path)` / `open_binary(path, mode)` -> `BinaryIO` -- explicit, non-overloaded functions that don't rely on Literal dispatch (`from tpy import open_text, open_binary`).
- **Working**: `enumerate(iterable)`, `enumerate(iterable, start)` → `Iterator[tuple[int32, T]]`. Supports lvalue and rvalue iterables (owning iterator prevents dangling).
- **Working**: `zip(iter1, iter2, ...)` → `Iterator[tuple[T1, T2, ...]]`. Overloads for 2-5 iterables. Stops at shortest. Supports lvalue and rvalue iterables. Combinator composition: `zip(map(...), map(...))` works -- owning variant stores iterators by value to prevent dangling references.
- **Working**: `map(fn, iterable, ...)` → `Iterator[U]`. Single and multi-iterable (up to 5). Accepts named functions, lambdas, generic functions, and `Callable`-typed variables. Lazy evaluation. Supports lvalue and rvalue iterables. Reference preservation: when `fn` returns by reference, `map` yields `val_or_ref<T>` so mutations propagate to the original container. Mixed `Ref`/`Own` params forwarded correctly via `fn_param_t` traits. Combinator composition: `enumerate(map(...))`, `filter(map(...))`, `map(filter(...))` work.
- **Working**: `filter(fn, iterable)` → `Iterator[T]`. Accepts named functions, lambdas, and `Callable`-typed variables. Lazy evaluation. Direct iteration for containers preserves element references for non-value types. Limitations: `filter(None, ...)` not supported.

#### Type Conversion Functions

**`list()` (Working)**:

```python
# Empty list with type annotation
x: list[int] = list()        # type from annotation
x: list[int] = []            # same - empty literal infers from annotation

# Empty list with inference from usage (inside functions)
xs = []                      # element type inferred from subsequent usage
xs.append(42)                # → list[int32], inferred from append argument
ys = list()                  # same with list() constructor
ys.append(42)                # → list[int32]

# Bare list()/[] with no usage that reveals element type → error
x = []                       # error if never appended to or passed to typed param

# List from iterable - type inferred from element type
x = list([1, 2, 3])          # → list[int], infers element type from literal
y = list(some_array)         # → list[T], infers from array's element type
z = list(other_list)         # → list[T], copies the list

# List from range
nums = list(range(5))        # → [0, 1, 2, 3, 4]
nums2 = list(range(2, 7))   # → [2, 3, 4, 5, 6]

# List from user-defined iterator (Iterator)
result = list(Counter(5))    # → [0, 1, 2, 3, 4]
```

Generated C++:
```cpp
// list([1, 2, 3]) - from literal
std::vector<tpy::BigInt> x({1, 2, 3});

// list(iterable) - from any Iterable (range, array, list, iterator, etc.)
// Dispatches at C++ level: begin/end for ranges, __next__() for iterators
auto y = tpy::construct<std::vector<int32_t>>(tpy::Range<int32_t>(5));
auto z = tpy::construct<std::vector<int32_t>>(Counter(5));
```

**`int()` (Working)**:
```python
x = int(3.14)     # → 3 (truncates toward zero)
y = int(-2.7)     # → -2 (truncates toward zero)
z = int(1e100)    # → large BigInt (works correctly)
# int(float("nan"))  # raises ValueError: cannot convert float NaN to integer
# int(float("inf"))  # raises OverflowError: cannot convert float infinity to integer

# From string
a = int("42")     # → 42
b = int("-123")   # → -123
c = int("  99  ") # → 99 (whitespace trimmed)
# int("abc")      # raises ValueError: invalid literal for int() with base 10: 'abc'
```

**`float()` (Working)**:
```python
x = float(42)     # → 42.0
y = float()       # → 0.0
```

**`bool()` (Working)**:
```python
b = bool()        # → False (default)
b = bool(True)    # → True (identity)
b = bool(0)       # → False
b = bool(42)      # → True (non-zero)
b = bool(int32(0))  # → False
b = bool(int32(1))  # → True

# User-defined __bool__() dispatch
class Container:
    count: int
    def __bool__(self) -> bool:
        return self.count != 0

c = Container(3)
b = bool(c)       # → True (calls c.__bool__())
```

**`copy()` (Working)**:

Used to make value copies explicit — both for returning lvalues as `Own[T]` and for acknowledging implicit copies when assigning to record fields:

```python
from tpy import int32, Own, copy

class Box:
    value: int32

def take_value(b: Box) -> Own[int32]:
    return copy(b.value)  # Explicit copy required for lvalue

def make_box() -> Own[Box]:
    return Box()  # No copy needed - constructor is an rvalue
```

The `copy()` function:
- Takes exactly one argument of any type `T`
- Returns `Own[T]` (owned value)
- Required when returning lvalues (variables, field accesses, subscript) as `Own[T]`
- Silences copy warnings when assigning lvalues to inline storage (`self.field = copy(x)`, `items.append(copy(x))`)
- Not required when returning rvalues (constructor calls, function calls)
- Accepts a `readonly[T]` source (a readonly param, a field read under a
  `@readonly` method, an `auto_readonly` borrow return) and yields a mutable
  `Own[T]`: copying reads its source and never writes it, and the result is
  independent storage. The qualifier is not laundered -- the borrow itself
  still cannot reach a mutable slot without the `copy()`
- In generated C++, `copy(x)` simply evaluates to `x` (the `Own[T]` return type handles the by-value semantics)
- In CPython tests, uses `deepcopy` to match C++ by-value semantics (containers copy all elements)

**`str()` (Partial - with caveats)**:
```python
s = str()         # → "" (empty string)
s = str("hello")  # → "hello" (identity)
s = str(True)     # → "True"
s = str(False)    # → "False"
s = str(c)        # → single-char string from char

# Numeric conversions
s = str(42)       # → "42" - safe (str is owned std::string)
s = str(3.14)     # → "3.14"

# Container conversions (matches print() output)
s = str([1, 2, 3])           # → "[1, 2, 3]"
s = str((1, "hello"))        # → "(1, 'hello')"
s = str({"a": 1, "b": 2})   # → "{'a': 1, 'b': 2}"
s = repr([1, 2, 3])          # → "[1, 2, 3]" (same as str for containers)
```

---

## Modules & Imports

- **Working**: `from tpy import ...` (built-in types like `int32`, `Span`, `Array`)
  - **Note**: tpy types require explicit import -- using `int32` without `from tpy import int32` produces an error with a helpful suggestion
- **Working**: `from typing import ...` (type annotations like `Optional`, `Protocol`, `Self`, `Sized`, `Sequence`, `MutableSequence`, `Iterator`, `Iterable`)
  - **Note**: typing names require explicit import -- using `Optional` without `from typing import Optional` produces an error with a helpful suggestion
- **Working**: `import time` and `from time import time`
- **Working**: Import aliases: `from time import time as get_time`, `from tpy import int32 as I32`, `from typing import Optional as Opt`
  - Aliases work for both type annotations (`x: I32`, `x: Opt[I32]`) and constructor calls (`I32(42)`)
- **Working**: Module-level aliases: `import time as t`, `import tpy as tp`, `import typing as t`
  - Qualified type annotations work: `tp.int32`, `t.Optional[tp.int32]`, `typing.Protocol`
- **Working**: Submodule namespace binding -- `from pkg import submod` binds `submod` as a usable namespace. Qualified calls (`submod.fn(...)`), record constructors (`submod.RecordName(...)`), and type annotations (`field: submod.RecordName`) all resolve through the submodule's exports. Aliased form (`from pkg import submod as alias`) works the same way.
- **Working**: `import pkg.sub` then `pkg.sub.X` for both function calls and variable / constant access (`pkg.sub.fn()`, `pkg.sub.CONST`).
- **Working**: `import sys` - system module with `sys.argv`, `sys.stdout`, `sys.stderr`, `sys.exit(code)`, `sys.maxsize`, `sys.byteorder`, `sys.maxunicode`
- **Working**: `import math` - mathematical functions
- **Working**: Namespace wrapping for modules (each module gets its own C++ namespace)
- **Working**: User-defined modules (multi-file projects)
- **Working**: Package support (dotted imports, `__init__.py`, namespace packages)
- **Working**: Relative imports (`from . import sibling`, `from ..pkg import func`)
- **Working**: Universal re-export -- every module exposes its imports as module attributes, so `from b import X` works when `b` itself does `from c import X`, regardless of whether `b` is a `__init__.py` / `# tpy: native_module` / implicit-stdlib facade. Records, enums, protocols, functions, variables, and macros (class / call / builder) all flow through arbitrary chain depths. The consumer's `using` declarations land at the ultimate definer's namespace. Cycle peers re-export too, with cycle-aware `using` suppression in codegen for kinds that aren't forward-declared in `<peer>_fwd.hpp` (functions, variables, static-protocol concepts). Re-exports of records / enums from a descendant submodule additionally get a relative-namespace forward-decl preamble in the parent's header (so `using ::pkg::sub::Name;` resolves when the sub's parent-walk pulls our header in mid-parse); the parallel using-decl for function / variable re-exports from a descendant submodule is suppressed, with codegen routing consumers through the ultimate definer's qualified name directly.
- **Working**: Shadowing detection -- local definitions (def, class, assignment) that shadow imported names are detected. Parser-resolved names (type annotations, decorators, base classes like `Enum`/`Protocol`/`TypedDict`, `auto()`) correctly respect shadowing. Warnings are emitted for `typing` and `enum` module names (e.g., `class Sized` after `from typing import Sized`, or `def auto()` after `from enum import auto`)
- **Working**: Standard library infrastructure (`tplib`, `stdlib`) with `-L` search paths
- **Working**: `tplib.Box[T]` -- heap-allocated owning container (via `from tplib import Box`)
- **Working**: `tplib.ArrayList[T, N]` -- fixed-capacity list with ownership-correct element lifecycle (iterable via `for x in list`). Movable for any element type, including non-trivially-relocatable ones (e.g. a record with a `str` field), via a `__move__` relocating-move ctor that relocates the live prefix `[0, _size)` through `UninitArrayStorage.relocate_from` (a `memcpy` for a trivially-relocatable element, element-wise move otherwise) -- an O(N) cost inherent to inline storage (the heap-backed builtin `list` moves in O(1)).
- **Working**: `tplib.FixStr[N]` -- fixed-capacity string with stack-allocated storage (char-level operations, `__str__` for zero-copy printing)
- **Working**: `tplib.json` -- JSON parsing/serialization library: `JsonReader` (pull parser), `JsonWriter` (serializer), `@model` class macro for pydantic-style typed JSON with `from_json`/`to_json`/`try_from_json`. File I/O via `save_json(path, indent=0)`, `load_json(path)` (panics on error), and `try_load_json(path)` (propagates `JsonError`). Supports `str`, `bool`, `int`/`int32`/`int64`/`BigInt`, `float`/`float32`, enums, `Optional[T]`, `list[T]`, `dict[str, V]`, `tuple[T, ...]`, nested `@model` records, model inheritance (single + multi-level, with defaults and optionals), field renaming via `field(alias="jsonKey")`, and user-defined types implementing `__json_encode__`/`__json_decode__`. Pretty printing via `JsonWriter(indent=2)` or `obj.to_json(indent=2)`. `JsonError` carries `message` and `pos` fields with a `describe(data)` helper for human-readable error context.
- **Working**: `bisect` module -- array bisection algorithms (via `from bisect import bisect_left`)
- **Working**: `from module import *` -- star imports from user modules, `tpy`, `builtins`, and `typing`
  - Respects `__all__` if defined; must be a compile-time literal (list / tuple of string literals; set literals work in TPy but break under CPython, whose star-import machinery does `__all__[i]` and rejects sets -- prefer list / tuple for dual-target code). A non-literal `__all__` is a parse-time error in the module that defines it, regardless of whether any consumer uses `from M import *`. The empty form `__all__ = []` (the Python idiom for "export nothing") is accepted as `list[str]` without an annotation.
  - Names listed in `__all__` that the module does not actually define produce a compile-time warning on the defining module ("__all__ lists 'X' which is not defined in this module"); CPython raises `AttributeError` at star-import time for the same input.
  - Without `__all__`, exports all public top-level names (functions, classes, records, variables, type aliases, imports) not starting with `_`
  - Relative star imports: `from .sibling import *`. The dot-only `from . import *` (package-level star) is not supported and currently silently no-ops; tracked in BUGS.md.

### User-Defined Modules (Working)

TurboPython supports importing from other `.py` files in the same directory:

```python
# utils.py
from tpy import int32

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

def add(a: int32, b: int32) -> int32:
    return a + b

MAX: int32 = int32(100)
```

```python
# main.py
from tpy import int32
from utils import Point, add, MAX

p = Point(int32(1), int32(2))
result = add(p.x, p.y)
print(MAX)  # 100
```

A module-level variable exports whether or not it carries an annotation: `WIDTH = 800` in `utils.py` is importable as `from utils import WIDTH` (and readable as `utils.WIDTH`) with the type inferred from its initializer, exactly like the annotated `MAX` above. The binding has to be a module-level *statement*: a name first bound inside an `if`/`for`/`while`/`try` body at module level is a local of the module's init code, not an exportable variable (see `BUGS.md#block-level-module-binding-has-no-slot`).

Inside a function a bare write to a name binds a fresh **local** for the whole body, CPython's rule, even when a module global of that name exists and holds another type -- `COUNT = 10` at module level and `COUNT = "hello"` inside a `def` give that function a `str` local and leave the global's `int32` untouched. `global COUNT` is how a function rebinds the module variable instead (the global must already exist; see "Variables & Scope"). Once any function rebinds a global that way, no function may RETURN a borrow of it (a `StrView`/`BytesView`/`Span`, or a reference-typed global) -- the rebind frees the storage the borrow points into; return the owned type instead (see the `StrView` rules above). A global that HOLDS a handle (`Ptr[T]`, or a `Span[T]` over another buffer) is exempt: returning it copies the handle rather than pointing into the slot. Because the fresh local covers the whole body, a *read* placed before the bare write has nothing to read: CPython raises `UnboundLocalError` at runtime and TPy rejects the function at compile time (today with a not-yet-supported diagnostic naming `name.global_read` rather than the unbound local -- `BUGS.md#unbound-local-read-reported-as-global-read`). One shape escapes the rejection: reading an ATTRIBUTE of a record global (`y = gate.x` ahead of `gate = Gate(1)`) still compiles and reads the global -- `BUGS.md#read-before-local-shadow-resolves-global`.

**Supported import styles:**
- `from mod import func, Record, Protocol` - import specific items. `from mod import Cls` makes `Cls(...)` and `Cls.staticmethod(...)` (incl. inherited via MRO and method-level type args) work; also `Cls.field` for class constants and `Cls.Inner` for nested types
- `from mod import *` - star import; respects `__all__` if defined (see the dedicated star-import bullet above for `__all__` literal forms and known limitations)
- `import mod` then `mod.func()` / `mod.CONST` / `mod.Record` - module-qualified access (calls, variables, types). Three-level chains through a module-namespace variable (`mod.Cls.staticmethod(...)`, `mod.Cls.CONST`) also work
- `from mod import X as Y` - import with alias; `Y(...)` and `Y.staticmethod(...)` route to the same record as the un-aliased form
- `from pkg import submod` - bind submodule as namespace (`submod.fn()`, `submod.Type` annotations, `submod.Cls.staticmethod(...)`)
- `import pkg.sub` - dotted import; `pkg.sub.fn()`, `pkg.sub.CONST`, and `pkg.sub.Cls.staticmethod(...)` all work

**Module resolution:**
- Looks for `mod.py` in the project directory
- Supports package directories with `__init__.py`

**`__name__` constant:**
- Entry point module: `__name__ == "__main__"`
- Imported modules: `__name__ == "module_name"`
- Immutable: `__name__` is a `Final[str]` compile-time constant; reassignment is a compile error

**Import execution order:** Imports execute at their source location, matching Python semantics. Top-level code in imported modules runs when the import statement is reached, not hoisted to the beginning:

```python
# main.py
print("before import")  # runs first
from helper import func  # helper's top-level code runs now
print("after import")   # runs last
```

Each module initializes only once (double-init guard prevents diamond dependency issues).

**Circular imports:** Cyclic imports between user modules are accepted, including the `from b import bar` / `from b import B` / `from b import P` (protocol) / `from b import E` (enum) shapes where cycle members directly bind names from each other. Workspace-wide skeletal pre-registration mints `RecordInfo` / `FunctionInfo` / `ProtocolInfo` shells (and matching enum `NominalType` + `TypeDef.protocol` entries) from each module's parsed AST before any module's full sema runs; peer `bind_imports` finds stable references that the registration paths later mutate in place. Class macros (`@dataclass`, etc.) on cycle members work. The C++ back-end emits a per-module `<mod>_fwd.hpp` for cycle members; `<peer>.hpp` includes of cycle peers in the `.hpp` are swapped for the fwd version (the `.cpp` pulls in the full header for body-sema completeness), and cycle-member method bodies always emit out-of-line in `.cpp` so trivial inline-in-header bodies don't reach into a peer's complete type. Cycle members must contain only imports and bare type declarations at top level (no executable statements). Package `__init__.py` and `# tpy: native_module` re-export facades may now participate in cycles too (the cycle-facade reject gate was lifted by the per-module attribute table refactor); cycle-aware `using` suppression in codegen drops re-export `using` lines for symbols not forward-declared in `<peer>_fwd.hpp` (functions, variables, static-protocol concepts). By-value cross-module references between cycle peers (concrete inheritance from a peer record, by-value field of peer record, peer record inside a container / tuple / value-variant union) get a structured sema diagnostic naming the offending field and the workaround (`Ptr[T]` or moving the value-stored field out of the cycle).

**Shadowing builtin modules:** User modules can shadow builtin modules (none currently -- `math`, `time`, `sys` moved to lib/tpy/). If you create `math.py` in your project, `from math import ...` will use your module instead of the builtin. A warning is emitted:
```
main.py:1: warning: import 'math' shadows builtin module
```

**C++ mapping:** Each module gets its own namespace (`tpyapp::utils::Point`). Cross-module references use fully qualified names. Package modules use nested namespaces (`tpyapp::mypackage::submod::func`). A `# tpy: cpp_namespace("ns")` directive replaces `tpyapp::<module>` for that module and its package children.

A call to a module-level function is qualified too, including a SAME-module one (`::tpyapp::main::apply(f, v)`, never a bare `apply(f, v)`). A bare call is an unqualified-id, so C++ argument-dependent lookup adds the namespaces of every argument type to the overload set: a `std::function` or `list` argument pulls in `std` and a `::tpy::` type pulls in `::tpy::`, and a same-named template there (`std::invoke`, `std::apply`) can be a better match than the user's own function. A qualified-id disables ADL, so a user function may safely carry any name. Calls through a LOCAL callable -- a nested `def` or a `Callable`/`Fn` value -- stay bare: they name a C++ local, which no namespace can reach.

### Packages (Working)

TurboPython supports Python-style packages with `__init__.py` files:

```
project/
├── main.py
└── mypackage/
    ├── __init__.py    # Package init (can export functions/variables)
    ├── utils.py       # Submodule
    └── inner/
        ├── __init__.py
        └── core.py
```

**Importing from packages:**
```python
# Import from submodule
from mypackage.utils import add

# Import from package __init__
from mypackage import CONST, func

# Nested packages
from mypackage.inner.core import helper
```

**Namespace packages:** TurboPython supports namespace packages (no `__init__` required for simple submodule imports). If `mypackage/utils.py` exists, `from mypackage.utils import X` works without requiring `mypackage/__init__.py`.

**Package initialization:** Parent package `__init__` files are discovered and initialized before submodules, matching Python import semantics. When importing `from mypackage.submod import X`, the `mypackage/__init__` is executed first (side effects like `print()` run), then the submodule is initialized.

**C++ mapping:**
- `mypackage/__init__.py` → `namespace tpyapp::mypackage`
- `mypackage/utils.py` → `namespace tpyapp::mypackage::utils`
- Output structure: `__tpyc__/mypackage/utils.d/utils.{hpp,cpp}`

### Relative Imports (Working)

TurboPython supports Python-style relative imports within packages:

```python
# mypackage/consumer.py - import from sibling module
from .utils import add           # → mypackage.utils.add
from . import utils              # → import mypackage.utils as utils

# mypackage/inner/deep.py - import from parent package
from ..utils import helper       # → mypackage.utils.helper
from .. import config            # → import mypackage.config as config
```

**Supported patterns:**
- `from . import module` - import sibling module
- `from .module import item` - import item from sibling module
- `from .. import module` - import from parent package
- `from ..module import item` - import item from parent package module
- Multiple levels: `from ...pkg import X` (three dots = grandparent)

**Import ordering:** Relative imports execute at their source location, matching Python semantics. Multiple imports can be interleaved with top-level code:
```python
# pkg/consumer.py
print("before")
from . import first      # first.__tpy_init() runs here
print("middle")
from . import second     # second.__tpy_init() runs here
print("after")
```

**Error handling:**
```python
# pkg/mod.py
from ...outside import X  # ERROR: Relative import beyond top-level package
```

**TurboPython extension:** Unlike CPython, TurboPython allows relative imports that reach root level when the compiler has full visibility of the module structure. For example, `from .. import utils` in `pkg/mod.py` can import a root-level `utils.py` module. This may not work when running the same file with CPython directly.

### Re-exports (Working)

Every module exposes its imports as module attributes -- there is no facade vs. plain distinction. `from b import X` works whenever `b` itself has `X` in scope, regardless of how `b` got it (local definition, `from c import X`, star import, etc.). Records, enums, protocols, functions, variables, type aliases, and macros (class / call / builder) all flow through arbitrary chain depths. Mirrors CPython's "every imported name is a module attribute" semantics.

```python
# utils.py - regular flat module, no special directive
from tpy import int32
from .helpers import add, Point   # plain re-export
```

```python
# main.py
from utils import add, Point      # works the same as importing from .helpers directly

result = add(1, 2)
p = Point(10, 20)
```

**Package `__init__.py` files** and **`# tpy: native_module` facades** are the same code path -- they just happen to be the most common re-export shape. The C++ output is the same.

**What can be re-exported:**
- Functions
- Records (classes)
- Protocols (static / `@dynamic`)
- Enums
- Variables (Final and non-Final)
- Type aliases
- Macros (`@class_macro`, `@call_macro`, `@builder_macro`)

**C++ implementation.** The consumer's `using` declarations land at the *ultimate* defining module's namespace, not at the intermediate. `using ::tpystd::stdlib::dataclasses::dataclass;` rather than chaining through every intermediate. For records / enums / dynamic protocols the consumer's reach analysis pulls the definer's full header into the include set; for static protocols (concepts) the same holds because they're emitted in the same .hpp as the protocol's definition.

```cpp
// utils.hpp emits the surface namespace
namespace tpyapp::utils {
  using ::tpyapp::helpers::add;     // function -- single using declaration
  using ::tpyapp::helpers::Point;   // record -- single using declaration
  inline auto& counter = ::tpyapp::helpers::counter;  // variable
}
```

**Aliased re-exports** (`from .helpers import func as f`): functions use `inline auto&` aliases; records / enums / type aliases use `using NewName = ...;`.

**Native-module facades** (`# tpy: native_module`, no `.hpp` generated): consumer codegen routes around the missing facade and qualifies directly to the ultimate defining module. The facade's `__tpy_init()` is synthesized at the consumer side; re-exported variable initialization chains through correctly.

**Cycle peers** also re-export, with one codegen wrinkle: cycle peers' headers include `<peer>_fwd.hpp` rather than `<peer>.hpp`, so cycle-aware suppression drops `using` lines whose target isn't forward-declarable in `_fwd.hpp` (functions, variables, static-protocol concepts). Consumer codegen still works because it qualifies directly to the ultimate definer rather than relying on the intermediate's `using`. The cycle-facade reject gate that used to prohibit `__init__.py` / `native_module` / implicit-stdlib facades inside cycles was lifted.

**Known limitations** (see `BUGS.md`): variable and type alias re-export *from* a cycle peer (where the consumer's bind_imports runs before the cycle peer's) currently hits "X not found in module Y". Workaround: define the variable / alias outside the cycle.

### Standard Library Infrastructure (Working)

TurboPython has two library search roots that provide reusable modules:

- **`tplib`** -- TPy-specific standard library (`from tplib import Box`)
- **`stdlib`** -- Python stdlib analogs (`from bisect import bisect_left`)

**Module resolution order** (first match wins):
1. Entry point directory (user modules)
2. `-L` paths (user-specified, in order)
3. `lib/tpy/` (tplib, stdlib modules, tpy protocols)

**CLI flags:**
- `-L /path` -- add extra library search path (can be repeated)
- `--no-stdlib` -- disable standard library

**Available tplib modules:**

| Module | Description |
|--------|-------------|
| `tplib.Box[T]` | Heap-allocated owning container (similar to Rust's `Box<T>`); `@nocopy` with explicit `.clone()` to duplicate |
| `tplib.Rc[T]` (and `make_rc`) | Pure-TPy non-atomic shared-ownership smart pointer; `@nocopy` with explicit `.clone()` to share |
| `tplib.ArrayList[T, N]` | Fixed-capacity list with stack-allocated uninitialized storage; full list API (`append`, `pop`, `insert`, `index`, `count`, `remove`, `reverse`, `sort`, `swap`, `truncate`, `extend`, `clear`, `__contains__`, `__eq__`, `__repr__`) |
| `tplib.FixStr[N]` | Fixed-capacity string with stack-allocated storage; `__str__() -> StrView` for zero-copy printing |
| `tplib.json` | JSON library: `JsonReader` (pull parser), `JsonWriter` (serializer), `@model` macro for typed JSON deserialization/serialization. User types can implement `__json_encode__`/`__json_decode__` to work as `@model` fields. |

```python
from tplib import Box
from tpy import int32

b = Box[int32](42)
print(b.get())    # 42
b.set(100)
print(b.get())    # 100
c = b.clone()     # Explicit clone (Box is non-copyable)
val = c.take()    # Consuming: moves out value, destroys box (c cannot be used after this)
```

**Covariant Box for @dynamic protocols**: `Box[T]` declares `Covariant[T]`, so
`Box[Child]` can be passed where `Box[Parent]` is expected when `Child` implements
a `@dynamic Parent` protocol. The compiler generates a converting move constructor
on the C++ template:

```python
from tplib import Box
from tpy import dynamic
from typing import Protocol

@dynamic
class Shape(Protocol):
    def area(self) -> float: ...

class Circle(Shape):
    _r: float
    def __init__(self, r: float) -> None:
        self._r = r
    def area(self) -> float:
        return 3.14 * self._r * self._r

def print_area(b: Box[Shape]) -> None:
    print(b.get().area())

bc = Box(Circle(5.0))      # Box[Circle]
print_area(bc)              # covariant coercion: Box[Circle] -> Box[Shape]
```

`Covariant[T]` is a marker protocol. Any user-defined generic type can declare it
to opt into covariant coercion:

```python
from tpy import Ptr, Covariant

class SmartPtr[T](Covariant[T]):
    _ptr: Ptr[T]
    ...
```

The covariant upcast also applies to **container-literal elements**: a `list`
literal or comprehension may upcast each element to the annotated covariant
element type, so `pets: list[Box[Pet]] = [Box(Dog()), Box(Cat())]` and
`[Box(d) for d in dogs]` compile (each `Box[Dog]`/`Box[Cat]` converting-moves to
`Box[Pet]`). Same for `list[Rc[Pet]]`. **`dict` value literals** do the same per
value: `pets: dict[str, Box[Pet]] = {"a": Box(Dog()), "b": Box(Cat())}` builds at
`dict[str, Box[Pet]]` directly (each value coerced to the annotated value type,
not peer-unified). This all matches what `pets.append(Box(Dog()))` already
accepts. Plain value/reference-record element slicing is still rejected
(`list[Animal] = [Dog(), Cat()]` errors -- storing a `Dog` by value into an
`Animal` slot slices), as is an *invariant* generic element. Note the upcast is
at the *element/value* position of a fresh literal, not at the container level:
passing a whole `dict[str, Box[Dog]]` where `dict[str, Box[Pet]]` is expected is
still rejected, because `dict` is invariant. `set` element upcast is moot for the
`@nocopy` `Box`/`Rc` wrappers (rejected as set elements) but applies to any
hashable covariant value-type element.

**Available stdlib modules:**

| Module | Functions |
|--------|-----------|
| `bisect` | `bisect_left`, `bisect_right`, `insort_left`, `insort_right` |

```python
from bisect import bisect_left, insort_left
from tpy import int32

a: list[int32] = [1, 3, 5, 7]
pos = bisect_left(a, int32(4))  # 2
insort_left(a, int32(4))        # a = [1, 3, 4, 5, 7]
```

### Standard Library Modules

For the full per-module coverage tracker (status, priority, per-item status,
blockers, implementation policy), see `docs/STDLIB_ROADMAP.md`. The subsections
below cover only modules with a stable working surface.

**Implementation policy summary** (see STDLIB_ROADMAP.md for detail): stdlib
modules are pure TPy by default. Native C++ code is used only for OS/libc
primitives, bindings to existing C++ libraries, or benchmarked hot inner
loops -- everything else is .py. If the language is missing something that
blocks a clean pure-TPy implementation, the right fix is to extend the
language, not drop into C++.

Currently working with a stable surface:

| Module | Notes |
|---|---|
| `math` | Partial (~50%). Checked libm bindings + pure TPy helpers |
| `time` | Stub (`time()`, `sleep()`). More planned |
| `sys` | Stub (`argv`, `stdout`, `stderr`, `exit`, `maxsize`, `byteorder`, `maxunicode`). More planned |
| `random` | Stub (`random()`, `seed()`). Target: pure-TPy Mersenne Twister |
| `bisect` | Done. Pure TPy over the `Comparable` protocol |
| `functools` | Partial. 3-arg `reduce(func, a, initial)` only. See STDLIB_ROADMAP.md for blocked items |
| `struct` | Partial (`unpack`, `unpack_from`, `calcsize`) via compile-time macros |
| `enum` | Partial (`Enum`, `IntEnum`, `auto()`) via class macro |
| `dataclasses` | Partial (~80%; `@dataclass(frozen, order)`, `field()`, `asdict()`, `astuple()`, `__post_init__`) via class macro |
| `argparse` | Partial (~88%; `ArgumentParser` with `prog=`/`usage=`/`epilog=`/`add_help=`, all 7 actions, all 4 nargs forms, `type=int\|float\|str\|float32` + fixed-width ints + custom records via `@staticmethod from_arg`, `choices`/`required`/`dest`/`help`/`metavar`, scalar + list-literal defaults, `Optional[T]` / `Optional[list[T]]` for absent flags, bare `parse_args()` reads `sys.argv[1:]`, `--help`/`-h` auto-generation with CPython-style 80-col usage wrap, parse errors via stderr + `sys.exit(2)`, subparsers via `add_subparsers()` + `add_parser()` with flat per-sub fields exposed as `Optional[T]` on the top namespace) via builder-trace macro. Open: mutually-exclusive groups, runtime-derived `prog` default, terminal-width-aware help wrap, typed-union escape hatch on subparsers (sema phasing wall lifted by the pre-pass-6 builder-trace move; remaining work is private-record reachability + per-sub forwarder emission, tracked in MACRO_DESIGN.md) |
| `typing` | Partial (`Protocol`, `Self`, `Sized`, `Iterator`, `Iterable`, `TypedDict`, `Unpack`, etc.) |
| `collections` | Partial. `Counter` v1: `Counter()` / `Counter(iterable)`, `c[key]` (missing -> 0), `len`, `in`, `total()`, `most_common(n)`, `update`/`subtract`. `elements()` and `+ - & |` deferred (filed compiler blockers); pure TPy over `dict[T, int]`. **Divergence:** `Counter(mapping)` counts the mapping's *keys*, not its values (`Counter({"a": 3})` -> a=1, not a=3) -- seed counts via `c = Counter(); c[k] = n` instead. `update`/`subtract` take a `Counter` only (not an arbitrary iterable/kwargs) |
| `os` | Partial (~72%). Filesystem queries (`getcwd`/`chdir`/`listdir`/`scandir`/`getenv`) + `stat`/`lstat`/`fstat` -> `stat_result` and `scandir` -> `DirEntry`; mutating ops (`mkdir`/`makedirs`/`rmdir`/`removedirs`/`remove`/`unlink`/`rename`/`replace`/`symlink`/`readlink`/`link`/`truncate`/`ftruncate`/`chmod`/`chown`/`utime`/`fsync`) over raw POSIX; low-level fd I/O (`open`/`close`/`read`/`write`/`lseek`/`pipe`/`dup`/`dup2` + `O_*`/`SEEK_*`), `access`(+`*_OK`), `urandom`; process/system queries (`getpid`/`getppid`/`getuid` family/`getlogin`/`umask`/`cpu_count`/`strerror`/`isatty`/`get_terminal_size`), `fspath`, + module constants (`name`/`sep`/...). Syscall errors map to the CPython OSError subclass via the shared PEP 3151 table and carry the structured `.errno`/`.strerror`/`.filename` attributes with CPython-exact `str(e)` text (`"[Errno N] strerror: 'path'"`; `rename`/`replace`/`link`/`symlink` also set `.filename2`), so `e.errno == errno.ENOENT` works after any file op. `os.environ` is a snapshot mapping (+`pop`/`setdefault`/`update`/`clear`/`copy`); `putenv`/`unsetenv` are libc-only (matching CPython). `walk` (topdown + bottomup + `followlinks` + `onerror` callback + default error-skip), process spawning deferred. See STDLIB_ROADMAP.md |
| `os.path` | Partial (~96%). POSIX (`posixpath`): string surface (`join`/`split`/`splitext`/`basename`/`dirname`/`isabs`/`normpath`/`splitdrive`/`commonprefix`/`commonpath`/`normcase` + constants) plus filesystem queries (`exists`/`lexists`/`isfile`/`isdir`/`islink`/`getsize`/`abspath`/`realpath`(+`strict=`)/`relpath`/`getmtime`/`getatime`/`getctime`/`samefile`/`samestat`/`ismount`/`expandvars`/`expanduser`). CPython byte-compatible. `expandvars`/`expanduser` read the `os.environ` snapshot (`expanduser` `~user` via a pwd binding); `realpath` via `weakly_canonical` (symlink-loop best-effort may diverge), `realpath(strict=True)` via `canonical` (raises on a missing path). See STDLIB_ROADMAP.md |
| `urllib.parse` | Partial. `urlsplit`/`urlparse`/`urlunsplit`/`urlunparse`/`urljoin`, `quote`/`quote_plus`/`unquote`/`unquote_plus`, `urlencode(dict[str,str])`, `parse_qsl`. CPython-faithful algorithms. **Divergence:** results are records (`.scheme`/`.netloc`/`.path`/`.query`/`.fragment` + `.hostname`/`.port`/`.username`/`.password`/`.geturl()`), not namedtuples -- integer indexing and unpacking are compile errors (never silent). Known divergence: `unquote` of invalid UTF-8 returns raw bytes (CPython uses U+FFFD). `parse_qs`, bytes variants, `urldefrag`, `urlencode(doseq=)`, `quote(encoding=)` deferred. See STDLIB_ROADMAP.md |
| `ssl` | Partial. HTTPS-client core + minimal server-side TLS (backed by vendored mbedTLS): `create_default_context()` (secure by default -- verify + hostname), `SSLContext` (`load_verify_locations(cafile)`, `load_cert_chain(certfile, keyfile)`, `verify_mode`, `check_hostname`), `wrap_socket(sock, server_hostname=..., server_side=False)` -> `SSLSocket` (`do_handshake`/`recv`/`send`/`sendall`/`version`/`makefile`/`close`/`setblocking`), `SSLError`/`SSLCertVerificationError`/`SSLWantReadError`/`SSLWantWriteError`/`SSLZeroReturnError`, `CERT_NONE`/`CERT_REQUIRED`. **Server-side TLS:** `SSLContext().load_cert_chain(cert, key)` + `wrap_socket(sock, server_side=True)` runs a real mbedTLS handshake as the server; the context is role-agnostic (no `PROTOCOL_TLS_*`/`Purpose` -- role chosen at `wrap_socket`), `server_side=True` requires a loaded cert chain (else `SSLError`) and ignores the client verify/hostname config. No mutual-TLS client-cert verification yet. `makefile()` returns an `io.BufferedReader` over the TLS session, shared via `Rc` so the connection outlives the `SSLSocket` for a live reader (CPython's refcounted `socket.makefile`). Non-blocking `recv`/`send` raise `SSLWantReadError`/`SSLWantWriteError`; the write path maps a `close_notify` return to `SSLZeroReturnError` defensively (mbedTLS surfaces close_notify from reads; CPython's write-after-close behavior is unverified); `recv()` returns `b""` on a clean `close_notify`, matching CPython's `SSLSocket.recv`. **Divergences:** `do_handshake()` returns a bool (not `None`+`SSLWantRead*`); `SSLCertVerificationError` derives only from `SSLError` (TPy single inheritance -- not catchable as `ValueError`); `version()` returns `"unknown"` pre-handshake (not `None`); `close()` sends `close_notify` best-effort and defers the fd close to the last shared holder (not an eager close); tighter v1 signatures (`server_hostname` positional-with-default, `cafile`-only). `create_default_context()` trusts a **vendored Mozilla root bundle** (via certifi, embedded as a compiled-in blob) **plus the platform CA bundle** (`load_default_certs()`: `SSL_CERT_FILE` env override, else the first existing well-known bundle path -- Debian/Fedora/openSUSE/Alpine/FreeBSD/macOS/Homebrew locations), so `requests.get("https://...")` / `urlopen` verify out of the box and hosts signed by a system-installed corporate CA verify with no flags, like curl; `load_verify_locations(cafile)` adds to those roots (additive, like CPython), and a bare `SSLContext()` still trusts nothing. Gaps (declared): macOS Keychain-only CAs (no PEM bundle exists; use `SSL_CERT_FILE`), `SSL_CERT_DIR` directory stores, Windows (no TPy Windows port yet). Divergence: CPython trusts only OpenSSL's defaults; TPy trusts the vendored roots UNION the system bundle (a superset, hermetic on storeless machines). `http.client.HTTPSConnection` runs the HTTP/1.1 flow over an `SSLSocket` (it and `HTTPConnection` nominally inherit a `@dynamic _Connection` protocol, so a caller holds either behind one `Box[_Connection]` and dispatches virtually -- TPy method dispatch is static, so a plain subclass would not); it captures a caller-supplied `context` **by value** (`copy()`), so a later mutation of the caller's `SSLContext` is not seen -- CPython shares the context by reference. `tplib.requests` (`verify: bool|str`) and `urllib.request.urlopen` (`context=`) route `https://` to it, including http->https redirects. See STDLIB_ROADMAP.md / docs/SSL_DESIGN.md |

Everything else in CPython's stdlib is missing or blocked -- consult
STDLIB_ROADMAP.md before relying on a module name.

#### `time` module (Working)

```python
import time
# or
from time import time, time_ns, sleep, perf_counter, perf_counter_ns,
#                  monotonic, monotonic_ns, process_time

# Wall-clock time
t = time.time()              # → double, seconds since epoch
tn = time.time_ns()          # → int64, nanoseconds since epoch
time.sleep(0.5)              # suspend for 500ms

# Monotonic clock (use for elapsed-time measurements; CPython ties
# perf_counter and monotonic to the same backing on POSIX)
start = time.perf_counter()
# ... do work ...
elapsed = time.perf_counter() - start

start_ns = time.perf_counter_ns()    # → int64
m = time.monotonic()                 # → double, alias of perf_counter
mn = time.monotonic_ns()             # → int64

# Process CPU time (~1us resolution; CPython uses ns via
# CLOCK_PROCESS_CPUTIME_ID, see STDLIB_ROADMAP.md)
cpu = time.process_time()
```

#### `sys` module (Working)

```python
import sys

# sys.argv - command line arguments as list[str]
for arg in sys.argv:
    print(arg)

# First element is program name
program = sys.argv[0]
```

#### `math` module (Working)

The libm-backed logarithm, root, power, trigonometric, hyperbolic, gamma,
remainder, scaling and fused-multiply-add functions raise catchable Python
`ValueError` for their domain errors or poles and `OverflowError` for
finite-input overflow. They preserve valid NaNs, infinities, signed zeros
and underflow results according to each function's contract. For example,
`sqrt(-1.0)` raises `ValueError`, while `sqrt(-0.0)` retains negative zero
and `sqrt(inf)` returns infinity. `log(x, base)` checks both logarithms;
base 1 retains `ZeroDivisionError`. Exceptions also propagate normally
through generators, async bodies and `@error_return` functions.

`fma` remains fused and follows CPython 3.13's domain/overflow policy;
CPython 3.12 has no `math.fma`. `ulp` returns finite spacing for either sign
of the largest finite float. These scalar adapters preserve the existing
argument signatures and do not change arbitrary user `@native` bindings.

```python
import math

# Logarithms
math.log(x)        # natural log (ln)
math.log(x, base)  # log with specified base
math.log10(x)      # base-10 log
math.log2(x)       # base-2 log

# Power and roots
math.sqrt(x)       # square root
math.pow(x, y)     # x raised to power y
math.exp(x)        # e raised to power x

# Rounding
math.floor(x)      # largest integer <= x
math.ceil(x)       # smallest integer >= x

# Trigonometry
math.sin(x)        # sine
math.cos(x)        # cosine
math.tan(x)        # tangent

# Absolute value
math.fabs(x)       # absolute value (float)
```

---

## Native Interop (Partial)

TurboPython can import existing C/C++ functions and export its own functions with C linkage. See `docs/NATIVE_INTEROP.md` for the full design document.

### Native Functions (Working)

Two decorators for native interop, imported from `tpy.extern`:

```python
from tpy.extern import native, export
from tpy import int32

# Import a C++ function
@native
def global_func(x: int32) -> int32: ...

# Import a C++ function with qualified name
@native("physics::calculate_force")
def calc_force(mass: float, accel: float) -> float: ...

# Import a C function (binding="C" for extern "C" linkage)
@native(binding="C")
def abs(x: int32) -> int32: ...

# Import a C function with renamed symbol
@native("clock", binding="C")
def get_clock() -> int32: ...

# Narrowing C++ return: cpp_return_type signals codegen to insert
# static_cast<DECLARED>(...) when the C++ side returns a wider type.
# `@native` is otherwise an exact-match binding -- the TPy signature
# must match C++ -- so this is the escape hatch for size_t -> int32
# patterns without dropping to @cpp_template.
@native("std::strlen", binding="C", cpp_return_type=uint64)
def strlen(s: Ptr[readonly[char]]) -> int32: ...

# Export a TPy function with C linkage
@export(binding="C")
def app_init() -> None:
    print("initialized")

# Export with renamed symbol
@export("app_tick", binding="C")
def game_tick(time: int32) -> None:
    print(time)
```


Cross-module imports of native functions work normally -- the compiler re-declares extern symbols in each module.

### C-Linkage Signature Types (Working)

A `binding="C"` signature is emitted verbatim into an `extern "C"` declaration, so every type in it has to be one a C caller can spell. Sema enforces an allow-list on `@native(binding="C")`, `@export(binding="C")`, and `native_global(..., binding="C")`:

- **Permitted:** fixed-width integers (`int8`..`uint64`), `float32`, `float`, `bool`, `char`, `Ptr[T]` for any `T` (including `Ptr[None]` -> `void*` and a pointer to a `@native(binding="C")` struct), `T | None` where `T` is a reference type (pointer repr -- `-> Widget | None` is the nullable-handle idiom and emits the same `Widget*` as `Ptr[Widget]`, at a parameter as at a return), `@native` enums (the bound C/C++ header owns the spelling), and `None` in return position (`void`). `readonly[...]` and `Own[...]` around any of these is permitted -- neither changes the C spelling of a value type.
- **`Ptr[T]` is unconditional in the pointee** -- a pointer is an opaque handle at the ABI whatever it addresses, so `Ptr[list[int32]]` is accepted and emits `std::vector<int32_t>*` into the declaration. That spelling is C++, not C: a C consumer declares `void*` for any pointee it cannot name. The gate constrains what crosses *by value*, not what a pointer may address.
- **Rejected:** `int` (`BigInt`), `str` / `StrView` / `String`, `bytes` / `bytearray`, `list` / `Span` / `Array`, `tuple`, `T | None` over a value type (`int32 | None` is `std::optional`), unions (a variant, even where its alternatives are pointers), TPy classes, a plain (non-`@native`) enum, a `@native(binding="C")` struct passed **by value** (it emits `S&`, a C++ reference) and `Own[S]` over a reference type (`S&&`). `*args` is rejected too -- variadic C functions are not supported.
- **`array=True` checks the element.** `native_global(..., array=True)` emits `extern "C" <element> <name>[];`, which names the element rather than passing it, so a `@native(binding="C")` struct is permitted there (`pts: Ptr[Point]` emits `extern "C" Point g_pts[];`) although the same struct by value in a signature is not.

Without the gate these passed *by value* as C++ types no C header can express (`const ::tpy::BigInt&`, `std::vector<int32_t>&`, `::SDL_Rect&`) or -- worse -- a signature that compiled and silently misbehaved: a `str` param respells to `const char*` at the signature while the body still renders against `std::string_view`, so `s == "hello"` became a raw pointer comparison that is always false.

`@native(binding="C")` class fields and stub methods are deliberately **not** gated: tpyc emits nothing for them (they mirror a declaration the author's C header owns), so they keep the ordinary `@native` "the author asserts the C side" contract. Only what tpyc itself writes into an `extern "C"` declaration is checked.

There is no marshaling layer, so a string or buffer is converted by hand. A C string crosses as `Ptr[readonly[uint8]]` plus the `tpy.unsafe` pair:

```python
from tpy import int32, Ptr, String, uint8, readonly
from tpy.extern import export, native
from tpy.unsafe import unsafe_cstr, unsafe_str_from_cstr

@export(binding="C")
def greet(name: Ptr[readonly[uint8]]) -> None:
    print(unsafe_str_from_cstr(name))    # const char* -> owned str

@native("puts", binding="C")
def puts(s: Ptr[readonly[uint8]]) -> int32: ...

def shout(msg: String) -> None:
    puts(unsafe_cstr(msg))               # String -> null-terminated const char*
```

`unsafe_cstr` takes `String` (owned), not `str`: only the owned form carries the null terminator, while `unsafe_ptr`'s `str` overload points into view storage that has none. Its result is valid only while the `String` lives, so bind the string to a name before the call rather than building it inline. Byte buffers and sequences take the same hand-written shape -- `Ptr[readonly[uint8]]` / `Ptr[T]` plus an explicit length parameter.

### Inline C++ templates -- `@cpp_template` (Working)

`@cpp_template("<C++ expression>")` binds a bodyless function/method to an inline C++ expression. Placeholders substitute textually: `{self}` (receiver) and positional `{0}`, `{1}`, ... (arguments) are *runtime values*; `{cpp}` (return-type spelling) and named type params `{T}` are *types*, resolved before argument expansion. `{{`/`}}` emit literal braces.

```python
@cpp_template("static_cast<char>({0})")
def to_char(i: int32) -> char: ...
```

- **A runtime-value placeholder may appear at most once.** Because substitution is textual paste (no evaluate-once binding), repeating `{self}` or a `{N}` would evaluate that argument twice -- a C-macro footgun (`SQUARE(i++)`): side effects run twice, and a repeated side-effecting subscript receiver makes `std::stable_sort(x.begin(), x.end())` undefined (the two evaluations can view different containers). The compiler rejects a repeated value placeholder at parse time and points at `@native`. Repeated *type* placeholders (`{T}`, `{cpp}`) stay legal -- they are inert. When you need to reference an evaluated argument more than once, use `@native` to bind a typed C++ helper (evaluate-once by construction).

### Native Classes (Working)

Import existing C++ classes and C structs so TPy code can declare their fields, call their methods, and pass them to native functions. No struct definition is generated -- the compiler trusts the external type exists.

```python
from tpy.extern import native, native_field
from tpy import int32, Float

# @native -- C++ class import (constructor call syntax)
@native
class Vec2:
    x: int32 = native_field("m_x")     # field rename: v.x -> v.m_x
    y: int32 = native_field("m_y")
    def sum(self) -> int32: ...        # stub method (... body)
    def dot(self, other: Vec2) -> int32: ...
    @staticmethod
    def zero() -> Vec2: ...            # static method

# @native with rename -- fully qualified C++ name
@native("b2::Vec2")
class PhysVec:
    x: Float
    y: Float
    def length(self) -> Float: ...
    @native("mag")
    def magnitude(self) -> Float: ...  # method rename

# C struct import (aggregate init syntax)
@native(binding="C")
class Point:
    x: int32
    y: int32
    def manhattan(self) -> int32: ...

# C struct with rename
@native("SDL_Rect", binding="C")
class Rect:
    x: int32
    y: int32
    w: int32
    h: int32
    def area(self) -> int32: ...

# Opaque handle -- no fields
@native("SDL_Window")
class Window: ...
```

Generated C++:
- `@native` classes use constructor call syntax: `Vec2(1, 2)`
- `@native(binding="C")` classes use aggregate initialization: `Point{5, 6}`
- Renamed types use the native name everywhere, including composite types like `Ptr[Rect]` -> `SDL_Rect*`
- Methods on native classes must have `...` body (stub declarations); methods with real bodies produce a parse error
- `@native("cpp_name")` on methods allows renaming individual methods (generates `obj.cpp_name(args)`)
- `@native("ns::func", function=True)` on methods generates a free function call with self as first arg: `::ns::func(obj, args)`
- `native_field("cpp_name")` in a field's default-value slot renames individual fields (e.g. `x: int32 = native_field("m_x")` emits `v.m_x`); rejected on non-`@native` classes
- `@property` composes with `@native`/`@cpp_template` on native class methods -- property access syntax (`obj.prop`, `obj.prop = x`) desugars to the native/template method call

### Final Constants (Working)

Compile-time constant globals using `Final[T]` from Python's `typing` module:

```python
from typing import Final
from tpy import int32, int64, float32, char

MAX_SIZE: Final[int32] = 100
PI: Final[float] = 3.14159
DEBUG: Final[bool] = True
NAME: Final[str] = "hello"
LETTER: Final[char] = "A"
BASE: Final[int32] = 10
ALIAS: Final[int32] = BASE  # cross-reference to another Final
BIG: Final[int] = 1000000   # BigInt
OFFSET: Final[int32] = BASE + 5  # arithmetic on constants
HALF: Final[float32] = float32(0.5)  # type constructor
WIDE: Final[int64] = int64(BASE)  # cross-type cast
VERSION: Final[tuple[int32, int32, int32]] = (1, 2, 3)
```

**Semantics** (stricter than Python's `Final`):
- Frozen binding: cannot be reassigned at module level, cannot use `global X` in functions
- Immutable value: treated as readonly (no mutation through the binding)
- Local shadowing allowed: functions can declare local variables with the same name
- Initializer must be a compile-time constant: literal, reference to a previously declared Final, constant arithmetic, primitive type constructor (`int32(x)`, `float32(x)`, etc.) with a constant argument, tuple of constants, or `@call_macro` expansion that reduces to a constant (no forward references)

**C++ mapping:**
- Constexpr-eligible types (fixed-width integers, float, bool, char, str): `inline constexpr T NAME = VALUE;` in header
- BigInt (`int`), tuple: `const T NAME = VALUE;` in source, `extern const` in header

**Restrictions (v1):**
- Module level (class-level `Final[T] = value` is documented in the next section; not yet supported in function bodies)
- Supported types: primitives (int, float, bool, str, StrView, char, IntN) and tuple (no `Final[list[T]]`, `Final[SomeRecord]`)
- Must use explicit type: `Final[T]` (bare `Final` not yet supported)
- A record-typed module constant is spelled as a plain annotated global (`UTC: timezone = timezone(timedelta())` in the datetime stdlib is the model); it is exportable and importable but not `Final`-frozen. The ALL_CAPS-without-Final warning deliberately skips types `Final[T]` cannot wrap. Extending `Final` to value-type records is filed in TODO.md.

### Class-Level Final and ClassVar (Working)

`Final[T] = value` in a class body declares a class-scoped immutable
constant (PEP 591's implicit-`ClassVar` rule). `ClassVar[T] = value`
declares a mutable class-scoped slot (PEP 526). Reads through the class,
through an instance, or through a child class all resolve to the
*declaring* class.

```python
from typing import ClassVar, Final
from tpy import int32

class HttpClient:
    TIMEOUT: Final[int32] = 30
    DEFAULT_HEADERS: Final[str] = "User-Agent: tpy"
    instances: ClassVar[int32] = 0          # mutable class-scoped slot

    def __init__(self) -> None:
        HttpClient.instances += 1

    def fetch(self) -> None:
        timeout = HttpClient.TIMEOUT   # Class-name access
        also_timeout = self.TIMEOUT    # Instance-side read

class AuthClient(HttpClient):
    pass

# Inherited reads: Child.X resolves through the MRO to Parent.
print(AuthClient.TIMEOUT)              # emits HttpClient::TIMEOUT
print(AuthClient().TIMEOUT)            # emits HttpClient::TIMEOUT
HttpClient.instances = 0               # mutation OK on ClassVar (rejected on Final)

# `@native` extern binding -- Final[T] without an initializer binds to a
# C++ static member declared in the user's header.
# tpy: native_module
# tpy: cpp_namespace("x::core")
# tpy: include("<x/build_opts.hpp>")
@native
class BuildOpts:
    FLAG: Final[bool]                              # binds to ::x::core::BuildOpts::FLAG
    KMAX: Final[int32] = native_field("kMax")      # rename: emits ::x::core::BuildOpts::kMax
```

**Semantics:**
- Reads through `Class.X`, `obj.X`, `self.X`, and `Child.X` (MRO walk) all
  resolve to the same slot; codegen emits `<DeclaringClass>::X`
- `Final[T] = value` rejects all mutation (`Class.X = ...`, `obj.X = ...`,
  `Class.X += ...`) with the same message as module-level Final
  reassignment
- `ClassVar[T] = value` allows mutation through `Class.X = ...`, `obj.X =
  ...`, and aug-assign; writes route to the same class-scoped storage
  regardless of access path. `obj.X = ...` writes through to the class
  storage, intentionally diverging from CPython (which would create an
  instance attribute) -- TPy's flat instance layout has no other sensible
  target. The compiler **warns** on instance-side writes (matching mypy /
  pyright), pointing the user at the portable `Class.X = ...` form
- `ClassVar[Final[T]] = value` is the explicit form of `Final[T] = value`
  in a class body (PEP 591)
- When `obj` has side effects (e.g. `f().LIMIT`, `lst[i].LIMIT`), the
  receiver is still evaluated -- codegen wraps the access in a GCC
  statement expression so the constant is the yielded value
- `obj: C | None; obj.X` matches CPython's `AttributeError` on `None`:
  warns and panics at runtime if `obj` is `None`. Narrowing (`if obj is
  not None: obj.X`) drops both the warning and the check
- Multi-base inheritance: `class C(A, B)` where both `A` and `B` declare
  the same constant rejects unqualified `C.X` with the same "ambiguous"
  error as instance-field ambiguity; user must disambiguate via `A.X` /
  `B.X`
- Subclass redeclaration of a parent's `Final` class constant rejected;
  non-final `ClassVar` may be *shadowed* by a same-finality, same-type
  redeclaration (each declaring class gets its own `static inline` slot,
  matching Python's per-`__dict__` shadowing). Cross-finality
  redeclaration (parent `ClassVar` + child `Final`) and incompatible
  shadow types are rejected. Multi-base inheritance validates the shadow
  against *every* declaring ancestor, not just the first
- Instance-side reads of a shadowed `ClassVar` resolve via the receiver's
  *declared* type, not the runtime type. `def f(p: Parent): print(p.X)`
  emits `Parent::X` even when `p` is actually a `Child` with `X` shadowed
  -- TPy's static-typing model has no dynamic dispatch on attribute
  lookup. CPython does dynamic lookup and would print Child's value.
  Use class-qualified access (`Parent.X` / `Child.X`) for portable
  behavior when the static and runtime types may differ
- Cross-module access works including the inherited case: `from mod import
  Child` where `LIMIT` is on `Parent` emits the parent's fully-qualified
  namespace even though `Parent` was never imported

**C++ mapping (regular classes):**
- `Final[T] = value` -> `static constexpr T NAME = VALUE;` (constexpr-eligible
  types: numeric, char, StrView, bool, literal-tuple)
- `ClassVar[T] = value` -> `static inline T NAME = VALUE;` (mutable, with
  C++17+ well-defined cross-TU semantics)
- `@native` classes don't emit class-body declarations; the user's header
  owns the storage. `ClassVar` is rejected on `@native` (mutable extern
  statics belong on module-level `native_global` instead)
- `Final[T] = native_field("cpp_name")` on a `@native` class binds the
  Python identifier to the renamed C++ static (`<qname>::cpp_name`),
  matching the per-symbol rename semantics of instance-field
  `native_field`

**Restrictions:**
- `Final[T]` allow-list: numeric / `char` / `StrView` / `bool` / tuple
  (same as module-level Final). `Final[str]` rewrites to `StrView` since
  string literals have static lifetime.
- `ClassVar[T]` allow-list: numeric / `char` / `bool` / tuple-of-allowed
  (tighter than Final). `StrView` and `str` are rejected -- a write
  `C.X = make_string()` would store a view into a temporary's storage and
  dangle. Use `Final[StrView]` for read-only string constants.
- `Final[T]` without an initializer is currently only supported on
  `@native` classes (PEP 591 instance-final on regular classes lands as a
  separate feature)
- `ClassVar[T]` without an initializer rejected (no useful semantics)
- Class constants on generic classes are supported when the inner type
  and initializer are *T-independent* (don't reference any of the
  class's type parameters). Each template instantiation gets its own
  `static constexpr` / `static inline` slot; instance-side access
  renders `C<int32_t>::X` from the receiver's type. For mutable
  `ClassVar` on a generic class, this means `C<int32_t>::counter` and
  `C<float>::counter` are independent slots in TPy -- writes through
  one don't affect the other. CPython has only one class object per
  generic class regardless of type-args, so cross-instantiation
  mutations are visible there; running the same code under CPython and
  TPy can diverge. T-dependent forms (`Final[T]`, initializers like
  `T()`) are deferred. Bare class-name access (`C.X`) and access
  through a non-generic subclass of a generic ancestor
  (`class Child(C[int32]): pass; obj: Child; obj.X`) are rejected --
  access via an instance of the parameterized class
  (`obj: C[int32]; obj.X`) or inside a method (`self.X`) instead

See `docs/CLASSVAR_DESIGN.md` for the full 10-phase plan and edge-case
table.

### Native Global Variables (Working)

Import extern C/C++ global variables:

```python
from tpy.extern import native_global
from tpy import int32, Ptr, int16

# C global (extern "C")
frame_count: int32 = native_global("DG_FrameCount", binding="C")

# C global without rename (Python name = C name)
tick: int32 = native_global(binding="C")

# C array global (extern "C" T name[]) -- for C arrays that decay to pointers
scores: Ptr[int16] = native_global("g_scores", binding="C", array=True)

# C++ global (possibly namespaced)
score: int32 = native_global("engine::score")
lives: int32 = native_global()
```


Generated C++ emits `extern` declarations before the module namespace. References use the C/C++ name directly. Must be at module level with a type annotation. For array globals, `native_global(..., array=True)` generates `extern "C" T name[];` (incomplete array type) which correctly links to C arrays and decays to a pointer when used.

### Module-Level Directives (Working)

Compiler directives are special comments that must appear in the file preamble (before any code):

```python
# tpy: include("mylib/mylib.h")            # add #include "mylib/mylib.h" to generated header
# tpy: include("<SDL2/SDL.h>")              # add #include <SDL2/SDL.h> (angle-bracket)
# tpy: include(<sys/time.h>, platform="linux")  # platform-filtered include
# tpy: link("SDL2")                         # add -lSDL2 linker flag
# tpy: link("m", platform="linux")          # platform-filtered: only link on Linux
# tpy: link("pcre2", managed=True)          # declare dependency on a registered third-party lib
# tpy: native_module                        # declaration-only module (no .hpp/.cpp generated)
# tpy: cpp_namespace("myproject::core")      # override C++ namespace
```

**Directives:**
- **`include(path)`** / **`include(path, platform=name)`** -- adds a C/C++ `#include` to the generated header. Quoted paths use `#include "..."`, angle-bracket paths (`<...>`) use `#include <...>`. Optional `platform` filter: `"linux"`, `"macos"`, `"windows"`. In `native_module` modules, includes propagate transitively into any consumer whose generated C++ reaches a type defined in this module -- whether named explicitly in the consumer's imports or surfaced only through field/method chains on imported types. Native-to-native chains follow the same rule (a hand-written native header that forward-declares a type from another native module does not need to be edited; the consumer header pulls in both).
- **`link(lib)` / `link(lib, platform=name)`** -- adds `-llib` linker flag. Raw, unconditional. Optional `platform` filter: `"linux"`, `"macos"`, `"windows"`.
- **`link(lib, managed=True)`** -- declares a dependency on a registered third-party C/C++ library (defined in `tpyc/build/third_party.py`). The build layer resolves this to concrete include dirs, link flags, and vendored-source inclusion based on the user-selected mode (`tpyc --<lib>={bundled,system,auto,none}`). Higher-level than raw `link()`: one declaration covers all modes consistently across direct-compile (`tpyc -x`/`-b`) and CMake-emit paths. The `none` mode triggers a compile error if anything in the build graph declares the dependency -- useful for embedded targets that want to strip out an optional module. Used today by `lib/tpy/_bindings/pcre2.py` for the `re` stdlib module; future stdlib C-binding modules (`gzip` -> zlib, etc.) will use the same directive. Composes with `platform=`.
- **`native_module`** -- marks the module as declaration-only: no `.hpp` or `.cpp` is generated. Use for modules that only declare `@native` bindings to existing C/C++ types. Each module must declare this explicitly (it does not propagate from parent packages).
- **`cpp_namespace(name)`** -- overrides the C++ namespace for the module (replaces the default `tpyapp::module_name`). In `__init__.py`, child modules inherit the namespace with their relative name appended (e.g., `cpp_namespace("mypkg")` in `__init__.py` makes `pkg/foo.py` use `mypkg::foo`). Also drives `@native` rename resolution: bare `@native` and `@native("name")` (no `::`) are relative to the namespace (e.g. `mypkg::name`); renames containing `::` are absolute and ignore the namespace (use `@native("::libc_fn")` to bind to libc from inside a namespaced module). See `docs/NATIVE_INTEROP.md` for the full rule.

Unknown directives produce a warning. Directives after the first line of code produce a warning and are ignored.

**Not yet supported:**
- `@export(binding="C")` class -- export TPy struct for C (planned)
- C header generation (`--emit-c-header`) (planned)
- Automatic marshaling at a C boundary -- `str` <-> `const char*`, `bytes` / `list[T]` <-> `(T*, size_t)`, in both the argument and the return direction. Those types are rejected by the C-linkage type gate today; convert by hand (planned)

---

### Platform Support (Working pattern; native builds only)

TPy targets Linux and macOS/*BSD today. Platform differences are handled by
**three** mechanisms, layered by where they resolve. The governing rule:
**keep stdlib `.py` platform-neutral; push platform specifics down into the
C++ facade.** A `.py` module should read the same on every platform; the
divergence lives in hand-written runtime `.cpp` or in per-platform constants.

1. **C++ `#ifdef` in hand-written runtime `.cpp`** (e.g.
   `runtime/cpp/src/stdlib/epoll_impl.cpp`'s `#if defined(__linux__)` epoll
   branch vs the `#else` kqueue branch, and `signal_impl.cpp`'s eventfd vs
   self-pipe). Use this for genuine **API** divergence (epoll/kqueue/IOCP,
   POSIX sockets vs Winsock). Resolved by the C++ compiler, so it is
   **target-correct**. The flat C ABI the facade exposes (`tpy_epoll_*`,
   `tpy_signal_*`) stays identical across platforms, so the consuming `.py`
   never sees the split.
2. **`native_global`** for per-platform **constant values** -- the value comes
   from the target's system header at C++ compile time, so it is also
   target-correct. Used for the `os` `O_*`/`SEEK_*` flags and the divergent
   `socket` constants (`SOL_SOCKET`, `SO_*`, `AF_INET6`, `EAGAIN`,
   `EINPROGRESS`), which are read from `extern "C"` globals in `socket_impl.cpp`
   rather than hardcoded. See "Module-Level Directives" above for `native_global`.
3. **`# tpy: include(path, platform=)` / `link(lib, platform=)`** directives --
   filter an include or linker flag by platform. Unlike the two above, these
   resolve against the **build host's** `sys.platform` when `tpyc` runs (there
   is no `--target` flag), so they are correct only for **native builds**
   (host == target).

**Known gap (cross-compilation / Windows).** Because layer 3 is host-based and
there is no target-selection facility, cross-compiling (e.g. building a Windows
binary on Linux) is not supported, and adding a third OS (Windows: Winsock2
sockets, an IOCP reactor, `WSAStartup`/`WSACleanup` lifecycle, `-lws2_32`) needs
a proper design for target-aware conditional compilation. There is also no
TPy-*source*-level platform branch (no compile-time `PLATFORM` constant with
dead-branch elimination), which would matter only once platform divergence
appears in *logic* rather than in a constant or a syscall wrapper. Tracked in
`docs/FEATURE_ROADMAP.md` (Phase H).

---

## CPython Extensions (Partial)

The inverse of native interop: a TPy module compiled to a `.so` that ordinary
CPython can `import`. Full design in `docs/CPYTHON_INTEROP.md` (abi3 / limited
API, floor 3.12, hand-rolled C-API glue, copy-in marshalling).

- **Working (model)**: a module marked `# tpy: ext_module` exports its
  `@export`-ed functions to the host interpreter. Inside an `ext_module`,
  `@export` means "expose to CPython" (it generates a wrapper + `PyMethodDef`
  entry); the function itself stays an ordinary TPy function. Conflicting
  forms (`@export(binding="C")`, a positional name) are compile errors, as is
  combining `ext_module` with `native_module` (declaration-only -- no module
  to export). `@export` signature constraints (param/return marshallability)
  are validated before codegen, so the glue emitter assumes valid input;
  marshallability is a per-type `TypeDef.boundary_marshal` fact (so the
  Python-facing `str`/`bytes` marshal but the tpy-native `String` and the
  mutable `bytearray`, which share their C++ representation, do not).
  Codegen emits an extension glue TU (`PyMethodDef` / `PyModuleDef` /
  `PyInit_<module>`) beside the normal module `.cpp`. The boundary uses a
  hand-mirrored limited-API facade (`tpy/interop/cpython_h.hpp`) so no
  `Python.h` enters a generated TU; the `.so` links with undefined Python
  symbols (no libpython) resolved at import. **Arguments cross positionally or
  by keyword** (`METH_VARARGS | METH_KEYWORDS` + `PyArg_ParseTupleAndKeywords`
  against a `kwlist` of the param names): a host caller may call an `@export`
  function, method, or constructor with positional args, keyword args, or a mix
  (`add(2, 3)`, `add(a=2, b=3)`, `add(2, b=3)`, `Counter(value=10, label="c")`),
  matching CPython's positional-or-keyword semantics; a zero-arg callable stays
  `METH_NOARGS`. **Default values, positional-only (`/`) and keyword-only (`*`)
  params cross too** (`greet("Ann", excited=True)` skips the defaulted
  `greeting` and still passes `excited` by keyword): the wrapper materializes
  each omitted slot from its default rather than dropping trailing arguments,
  so a caller may skip an earlier defaulted param. A *required* keyword-only
  param is enforced by the glue itself -- CPython's argument parser cannot
  express one -- and its `TypeError` text matches CPython's exactly. Param
  forms that do not cross yet -- **`*args`/`**kwargs`** -- are rejected with a
  located compile error rather than silently mishandled. Only the by-value
  boundary types can carry a default in the first place, since a default must
  be a constant expression. A dunder still takes none of
  these forms: it crosses as a type slot whose operand CPython supplies
  directly, so there is no argument tuple to parse. One shape does not build
  yet -- a defaulted parameter before a *required* keyword-only one
  (`def f(a, b=10, *, c)`) -- because TPy lowers defaults to C++ positional
  default arguments, which must be trailing; that limitation is plain-TPy, not
  boundary-specific, and is tracked in `BUGS.md`.
- **Working (the fixed-width int types + `int` + `float` + `bool`)**: functions
  taking and returning any fixed-width int (`int8`..`int64` / `uint8`..`uint64`;
  the unpack splits args/kwargs into `PyObject*` slots with `from_py` owning
  every conversion -- signed and <= 32-bit-unsigned widths read a `long long` and
  range-check against the target, `uint64` uses the unsigned accessors),
  `int`/BigInt (rung 2, a two-tier int64 fast path + hex string round-trip, so
  values beyond int64 cross losslessly), `float` (rung 3, C++ `double` via
  `PyFloat_AsDouble`/`FromDouble`; `int`/`bool` args coerce through `__float__`),
  and `bool` (C++ `bool` via `PyObject_IsTrue` / `PyBool_FromLong`; any argument
  coerces by truthiness, matching CPython's `bool()`). Args are coerced via
  `__index__` (`__float__` for `float`, truthiness for `bool`); marshalling
  failures raise the right Python exception (`OverflowError` for an int outside
  the target width's range or too large for a double, `TypeError` for a
  non-number, or whatever a custom `__bool__` raises). Verified end to end in
  `tests/interop/` (ext-exec == cpy-parity for the tested values).
  **Coercion model:** the scalar boundary marshals via Python's protocols
  (`__index__` for ints, `__float__` for floats, truthiness for `bool`) and
  enforces the declared type/width, so the compiled extension is bounded/typed
  while the untyped TPy source (run under CPython for the parity check) is not.
  For in-range ordinary values they agree; an out-of-range int (a `2**40` passed
  to an `int32`), or a non-conforming exotic arg (a `complex`, a bare
  `__float__`/`__index__` object, or a non-bool passed where the source would
  return it unchanged), makes the compiled extension wider/stricter than the
  source. This is inherent to "`@export` marshals an untyped CPython arg into a
  typed TPy value" and applies to every scalar marshaller; such args are
  deliberately kept out of the parity drivers.
- **Working (void return)**: an `@export` with no return annotation or
  `-> None` returns `Py_None` to the host (`call(); Py_RETURN_NONE`). `void` is
  valid only in return position; a `None`-typed parameter has no host value to
  unmarshal and stays rejected.
- **Working (`str` + `bytes`)**: functions taking and returning `str`
  (PyUnicode <-> `std::string` via `PyUnicode_AsUTF8AndSize` /
  `PyUnicode_FromStringAndSize`) and `bytes` (PyBytes <-> `::tpy::Bytes`
  via `PyBytes_AsStringAndSize` / `PyBytes_FromStringAndSize`). Both cross **by
  copy** (v1 copy-in; the zero-copy view borrow is the deferred phase-3.5
  foreign-borrow primitive). The marshaller produces the *owned* form
  (`std::string` / `::tpy::Bytes`); the generated wrapper passes it to
  the function's *borrow*-form parameter (`std::string_view` /
  `::tpy::BytesView`) by implicit conversion, the owned local outliving
  the call -- so the borrow/storage-form duality is absorbed with no change to
  the glue emitter. `str` and `bytes` are **immutable**, so the boundary copy is
  unobservable (no aliasing divergence -- the reason they copy soundly where
  `list`/`dict` cannot). **Coercion model** (same as the scalars): the boundary
  enforces the declared type -- a non-`str`/`bytes` argument is a `TypeError`,
  and a lone-surrogate `str` (no strict-UTF-8 encoding) is a
  `UnicodeEncodeError`; the untyped TPy source leaves the annotation unchecked,
  so those cases are kept out of the parity drivers. The borrow forms `StrView`
  / `BytesView` and the mutable `bytearray` are **not** marshallable across the
  boundary (a view needs the deferred foreign-borrow primitive; `bytearray`'s
  aliasing a by-copy `PyBytes` would silently drop) and stay rejected; use
  `str` / `bytes`.
- **Working (`list` / `dict` / `set` / `tuple`)**: functions -- and
  exposed-class methods and `__init__`, which share the same glue -- taking and
  returning containers, marshalled O(n) **by copy** recursively -- `std::vector`
  <-> PyList, `tpy::ordered_map` <-> PyDict (insertion order), `tpy::ordered_set`
  <-> PySet, `std::tuple` <-> PyTuple. Elements are the scalar/str/bytes leaves
  and arbitrarily nested containers of those (`list[list[int]]`,
  `dict[str, list[int]]`). The recursion is **glue-driven**, not
  C++-template-driven: the C++ storage type cannot be trusted to name the leaf
  at every depth, so the codegen glue -- holding the
  unambiguous TPy element types -- emits nested per-element conversion lambdas
  bottoming out at `from_py<leaf>` / `to_py`; `marshal.hpp` supplies the
  element-fn-parameterized container helpers. `is_function_boundary_marshallable`
  (split from the base `is_boundary_marshallable`, which stays scalar/leaf-only
  and still gates exposed-class getset *fields* and enum getset boundaries)
  recurses into element/key/value types and gates function AND method
  params/returns -- so an exposed class can take/return a container in a
  method but cannot *store* one in an exposed field. **Three acknowledged
  divergences from the
  aliasing source** (the "container cliff"): (1) a param is an owned copy, so a
  mutation through it (`append`, `d[k] = v`) is not visible to the caller -- a
  list/dict/set param sema proves is mutated **warns** (functions and methods
  alike); a read-only one stays quiet; tuple is exempt. The OUT direction
  mirrors it: a *borrow-form* container return (`-> list[T]`, not
  `-> Own[list[T]]`) marshals a fresh copy, losing write-through aliasing, and
  **warns** at the return with `Own[...]` as the acknowledged spelling (same
  model as the exposed-class `-> Cls` alias warning; unlike a class, a
  container return is never identity-preserving -- the container was copied
  IN, so no live PyObject backs it). (2)
  strict-by-container-kind IN: a wrong container kind
  / tuple arity is a `TypeError` where the untyped source accepts any iterable.
  (3) per-element scalar coercion (`list[int]` coerces `True -> 1`), so distinct
  keys can collapse (`{1, True}` -> `set[int]`). Exposed **enums and classes are
  valid TOP-LEVEL container elements** (`list[Color]`, `list[Counter]`,
  `dict[str, Counter]`, `set[Color]`, `dict[Color, V]`, `tuple[Color, int64]`):
  each element marshals through its module type handle, copy-in/out like every
  other element. An enum element preserves the member singleton; a class element
  copies, so a mutated class-element param is the same copy cliff -- the
  mutated-param warning already covers element mutation. An exposed enum crosses
  in any position; an exposed **class cannot be a `set` element or dict key** (a
  class doesn't conform to Hashable). **Deferred (located errors, not
  misbuilds):** an exposed **class as a tuple element** (a tuple's non-value
  element uses borrow form -- use a list/dict, or a value-type/enum tuple
  element), any exposed type **nested inside a container element**
  (`dict[str, list[Counter]]` -- per-element qualification below the top level),
  a **`@nocopy`** exposed class as a container element (each element is copied),
  and a **cross-module** exposed element. Verified in `tests/interop/containers/`,
  `tests/interop/class_method_containers/`, and
  `tests/interop/container_exposed_elements/`.
- **Working (`Span[T]` numeric, buffer protocol)**: a `Span[readonly[T]]` or
  `Span[T]` param (`T` a fixed-width int or `float`) binds to any
  buffer-protocol object -- `array.array`, `memoryview`, `bytes`/`bytearray`,
  numpy arrays -- via `PyObject_GetBuffer` (`PyBUF_ND | PyBUF_FORMAT`,
  requiring a 1-D C-contiguous buffer). The element format/itemsize must
  strictly match `T` (no coercion, same strict-by-kind family as
  containers/enums); the bytes copy into a fresh `std::vector<T>`, which
  implicitly converts to the function's `std::span<T>`/`std::span<const T>`
  param -- the same owned-copy-converts-to-borrow-param trick `str`/`bytes`
  use. **Span crosses only as a PARAMETER (of a function or an exposed-class
  method/`__init__`), never a return type** --
  return numeric data via `list[T]` instead. v1 is copy-in for both the
  read-only and mutable forms (no `PyBUF_WRITABLE`, no write-back): a
  `Span[T]` (non-readonly) param sema proves is mutated **warns** at compile
  time (the same `mutated_params` mechanism and no-escape-hatch bar as the
  container-mutation warning); `Span[readonly[T]]` can never warn (writing
  through it is already a compile error elsewhere). Verified in
  `tests/interop/span_numeric/`.
- **Working (`.so` build mode, Linux + macOS)**: `tpyc -b` on an
  `# tpy: ext_module` builds an importable `<mod>.so` directly -- every TU
  `-fPIC`, no `main()`, glue TU in the link set. The link recipe follows the
  compiler's target: ELF `-shared`, Mach-O `-bundle -undefined dynamic_lookup`
  (the undefined `Py*` symbols, functions and data alike, resolve against the
  host interpreter at import). Windows is not supported (MSVC, and no
  statement-expression extension). `--exec` on an ext_module is a clean error
  (a `.so` is not runnable). The ext-exec snapshot harness
  (`tests/test_interop_exec.py`) snapshots the glue + module C++, builds the
  `.so`, imports it under CPython against `driver.py`, and asserts parity with
  the same driver over the TPy source; a facade self-check guards the ABI
  mirror.
- **Working (built-in exceptions across the boundary)**: a built-in TPy
  exception raised in an `@export` body (or in module-level code run at
  `PyInit_`) crosses to the host as the matching CPython exception type with
  its message preserved -- `raise ValueError("bad")` reaches the caller as a
  catchable `ValueError("bad")`, not the old generic `RuntimeError`. The glue
  catches `const tpy::BaseException&` and routes it through a runtime cascade
  (`tpy/interop/exc_bridge.hpp`) that maps the dynamic type to its `PyExc_*`
  counterpart (most-derived first; an unlisted subclass degrades to its nearest
  listed base, e.g. an unmapped `OSError` subclass surfaces as `PyExc_OSError`)
  and sets `e.what()` as the message. A non-`BaseException` C++ exception still
  flows through the generic `catch(...)` -- which also preserves a Python error
  the marshaller already set (`TypeError`/`OverflowError`/`UnicodeEncodeError`
  from `from_py`/`to_py`) via the `PyErr_Occurred()` guard. Runtime-raised
  exceptions (e.g. `ZeroDivisionError` from `//`) cross by type too; only their
  message text differs from CPython's wording. `@error_return` `Err` -> raise,
  exception chaining (`__cause__`/`__context__`), and panic -> exception are
  **not yet bridged**.
- **Working (user-defined exception classes)**: a user exception class defined
  in an ext_module gets its own Python type, created at `PyInit_`
  (`PyErr_NewException`, inheriting its built-in base's `PyExc_*` or an already-
  created user base), added to the module (importable as `mymod.MyError`) and
  registered `typeid -> PyObject* + setter`; `set_py_err_from` consults that
  registry by exact dynamic type before the built-in cascade. So `raise
  NotFound("k")` (where `class NotFound(KeyError)`) reaches the caller as
  `mymod.NotFound`, catchable as `NotFound` **and** as `KeyError` -- no longer
  degrading to `KeyError`. A **data-carrying** exception class crosses its
  instance fields (scalar / str / bytes / exposed-enum) as **instance
  attributes**: the registered setter constructs an instance from the message
  (`pytype(e.what())`), marshals each field via `PyObject_SetAttrString`, then
  `PyErr_SetObject`s it. A field whose name begins with `_` is **internal** (the
  same rule as exposed-class getset fields) -- it never crosses as an attribute
  and may hold any type; an exception whose fields are all internal falls back to
  the message-only setter. The crossing is *transitive* (registry-driven).
  **Acknowledged divergence:** `str(e)` and `e.args` reflect the message field
  only, not the full constructor arg tuple (the C++ exception holds typed
  fields, not the original call tuple; CPython-native keeps `args =
  (message, *fields)`). This is a documented divergence, not a warning --
  unavoidable under TPy's model with no author-side action, so it does not warn.
  Excluded from v1 (deferred, tracked in
  `TODO.md`): container / exposed-class data fields (located error); full `args`
  fidelity; exception chaining; user exceptions defined in *another* module and
  raised at the boundary (only ext_module-defined classes get a type -- imported
  ones still degrade).
- **Working (exposed classes)**: a class marked `@export` in an ext_module is
  exposed as a real CPython type built at `PyInit_` via `PyType_FromSpec`
  (importable as `mymod.Counter`). The instance embeds the TPy C++ payload
  after the `PyObject` header (`tpy::interop::Instance<T>`): `tp_init` runs the
  TPy `__init__` (placement-new from marshalled args), `tp_dealloc` runs the
  C++ destructor (so `Own`/`Box`/`Rc`/container fields release). Baseline
  surface: construct from Python, plain instance methods (each a bound wrapper),
  and every annotated field as a getset descriptor (read/write on a reference
  class; read-only when the class is itself a value type -- see below). Method and
  `__init__` params/returns admit the same boundary set as free `@export`
  functions (scalars/str/bytes, exposed classes/enums, containers recursively,
  a numeric `Span` as a param -- the method glue shares the free-function
  marshal emit); getset *fields* admit scalars/str/bytes, an exposed **enum**,
  and an exposed **value-type** class (an enum field round-trips its member
  through the module enum type -- `enum_to_py` reconstructs the interned
  singleton, so `w.color is Color.RED` holds through the getset copy; a
  value-type field copies its immutable value out via `instance_to_py`; both
  are strict-by-type on set, and both copies are behaviorally invisible because
  the value is immutable). An exposed **value type is itself exposed read-only**
  (its getset descriptors have no setter -- matching the language rule that a
  value type's fields are set only in `__init__`), so on the holder the value
  field is r/w (you can *replace* the whole value: `b.origin = Point(3, 4)`) but
  the nested value's own fields loud-fail a mutation (`b.origin.x = 5` raises
  `AttributeError`), and each read is a fresh copy (`b.origin is b.origin` is
  `False`). A **never-reassigned reference class-typed field** (sema proves no
  assignment outside `__init__` with a bare-self receiver) crosses as a
  **READ-ONLY borrow-view getset**: reads return a PyObject aliasing the live
  field -- write-through, and `outer.inner is outer.inner` holds via the
  per-module view registry -- while a Python-side rebind (`outer.inner = ...`)
  raises `AttributeError` (loud; plain Python would rebind -- the missing
  setter is what keeps the never-reassigned gate honest). A *public*
  *container* field stays rejected (the per-field getter/setter path has no
  container emit), and a *reassignable* reference class-typed field stays
  rejected (a view aliases the storage SLOT, so the rebind would show through
  it) -- but a field whose name begins with `_` is **internal**: it never
  crosses as a Python attribute (it stays live C++ payload state the class's
  own methods use), so it may hold *any* type, including the container and
  reassignable reference-class members the getset path rejects. Reach an
  internal member through a method rather than an attribute (the reject
  diagnostic points here). The same `_`-prefix rule hides an
  exception's internal data fields. A **`@property`** crosses as a *computed*
  getset: the getter is a zero-arg method return site (full method boundary
  set, containers included), the setter a one-param method param site, so
  `r.area` computes on every read and `r.width = 5` runs the setter (a raise
  inside an accessor crosses like a method raise). A getter-only property is
  read-only (`AttributeError` on write, as in CPython -- message text
  differs), and `del` on any property raises `AttributeError` (there are no
  deleters; matches CPython's deleter-less property). Two setter carve-outs,
  both located errors because a setter's purpose is storing its value: a
  setter typed as an exposed class -- value-type or reference -- cannot cross
  (the value arrives as a borrow, but the setter's parameter is an ownership
  transfer; use a plain method), and a `Span[T]` setter is rejected (the
  buffer copy-in dies at the call boundary; store `list[T]`). A `_`-prefixed
  property is internal like a `_`-prefixed field. A getter returning a borrow
  behaves like a method borrow-return: `-> Cls` sourced from `self`/a param
  is identity-preserving, from a never-reassigned field of one it crosses as
  an aliasing borrow view, and `-> list[T]` (or a reassignable-field
  `-> Cls`) copies out and warns. Introspection divergence: on the `.so` the descriptor is a
  `getset_descriptor`, not a `property` object (no `fget`/`fset`). Because the
  PyObject *owns* the instance, a class crosses **IN as a borrow of the live
  embedded payload** -- a method call or a free function taking the instance
  (`def bump(c: Counter, ...)`) mutates through it and the change is visible on
  the same object (correct Python reference semantics, unlike the by-copy
  container boundary). Crossing **OUT**, a borrow-form return (`-> Counter`)
  is **identity-preserving when the returned reference is the receiver or a
  parameter**: the glue address-matches it against the boundary-crossed
  objects in scope and hands back the ORIGINAL PyObject (`Py_IncRef`), so
  `is`, write-through, and the dynamic type survive -- `return self`, fluent
  chains, and param pass-through behave exactly like plain Python (a derived
  instance returned as `-> Base` comes back un-sliced, as itself). A
  **never-reassigned field borrow** of the receiver or a param
  (`return self._inner`) crosses as an aliasing **borrow view**: a PyObject
  of the field's exposed type pointing into the holder's live storage, with
  a keepalive ref on the holder and registry-deduped identity, so repeated
  accesses are the SAME object and mutation writes through -- plain-Python
  attribute aliasing (the field must be declared as exactly the return
  class; upcast sources stay on the copy path). The decision is per return
  path: a source with no live object behind it (a reassignable field, a
  module global) still copies out into a fresh instance (`instance_to_py`)
  -- a body with such a path **warns** (return `Own[...]` to make the copy
  explicit; the copy is a new object, and a copied derived instance slices
  to the declared type). Operator/container dunder slots are
  not identity-capable (their borrow returns always copy and warn; the
  in-place group returns `self` identity-preserved by construction, and
  `__iter__` threads the receiver candidate, so the canonical `return self`
  iterator crosses as the SAME object -- shared cursor state, sticky
  exhaustion -- while an `Own[Cls]` fresh-iterator return stays fresh), and a
  `readonly[Cls]` self/param return takes the identity path like the
  mutable form (readonly is a TPy-side contract; the Python consumer gets
  the original object, as in plain Python). Returning a fresh/`copy()`'d
  `Own[Counter]` is the normal always-fresh form.
  **Inheritance**: an `@export class Derived(Base)` whose single base
  is itself an `@export` class in the same module crosses as a real CPython
  subtype (`PyType_FromSpecWithBases` wires the base as `tp_base`):
  `isinstance`/`issubclass`/`__mro__`, inherited methods/fields/dunders, and
  ctor inheritance (a derived class with no own `__init__` accepts the base's
  signature) all behave as real Python inheritance, and a base-typed param
  borrows a derived payload with write-through. Partial dunder sets resolve across the hierarchy for every
  combined CPython slot: the derived richcompare dispatcher delegates ops it
  doesn't define to the nearest ancestor's (`__ne__` derives from `__eq__`
  only when no ancestor defines an explicit `__ne__`), and a partial
  override of the `__setitem__`/`__delitem__` or `__add__`/`__radd__`-style
  pairs keeps the inherited other half working, as does an own `__hash__`
  next to inherited comparisons (the `tp_hash`/`tp_richcompare` slot pair is
  re-wired explicitly). TPy's static
  dispatch still applies INSIDE the module (a base method calling an
  overridden method runs the base version -- the method-hiding warning fires
  at the override site; `@dynamic` is the hatch); Python-side calls dispatch
  via the MRO and match CPython. A **leaf** exposed type is **final** (no
  `Py_TPFLAGS_BASETYPE`): not subclassable from
  Python (an honest `TypeError` rather than partial subclassing that would
  silently diverge in method dispatch); a class that IS another exposed
  class's base carries `BASETYPE` (the C-API requires it), which legalizes a
  Python-side subclass *statement* -- instantiating one raises `TypeError`
  from the tp_init exact-type guard. An exposed instance has **no instance `__dict__`**
  (fixed layout -- ad-hoc attribute assignment raises `AttributeError`); a
  wrong-typed field set or `__init__` arg raises `TypeError`. These last three
  diverge from the plain Python source class (subclassable, `__dict__`, untyped)
  -- declared divergences kept out of the parity driver and exercised in
  `ext_checks.py`. The ext-module validator rejects, with a located error, an
  exposed class the glue can't yet emit: a field/method type that doesn't
  marshal, a **REASSIGNABLE public reference class-typed field** (a live
  borrow view aliases the field's storage slot, so a post-`__init__` rebind
  would show its replacement through the view where CPython keeps the old
  object; the diagnostic steers to dropping the reassignment or making the
  field *internal* -- a `_`-prefixed name never crosses as an attribute, so
  it may hold any type -- plus an accessor method that mutates through
  `self` or an explicit `copy()`.
  A *never-reassigned* reference class-typed field is admitted as a
  read-only borrow-view getset (see above), a *value-type* class-typed
  field is exempt -- immutability makes the copy-out invisible, so it is
  admitted, above -- and a `_`-prefixed field of *any* type is exempt too
  -- it is internal payload state, never a Python attribute), an `Own[Cls]` param (the host
  keeps its reference, so ownership can't transfer -- use the borrow form), a
  `@nocopy`
  class returned by reference (the boundary can't copy it out -- return
  `Own[Cls]`; covers every borrow spelling: methods, `readonly[Cls]`,
  property getters, and dunder slots -- in-place dunders exempt, they
  hand back the same `self` PyObject with no copy), static/async/generic/overloaded/`@error_return`
  methods (the same shape checks cover property accessors),
  multiple bases (CPython rejects two bases with distinct C instance
  layouts -- permanent), a non-`@export` base, a cross-module base, a
  declared `@dynamic` protocol base (the vptr breaks the embedded payload
  layout), generics, an exposed class defined in *another* module
  (cross-module exposed types are deferred, mirroring the cross-module enum
  guard below -- define and `@export` the class in the module that uses it),
  or `@export` on an exception class (those cross via the
  separate `PyErr_NewException` path). The exposed type carries no GC traversal: an owned
  instance's fields are held by value (no `PyObject` reference), and a borrow
  VIEW holds exactly one strong ref -- its owner -- while no exposed instance
  holds a ref to any view, so the reference graph is acyclic by construction
  and plain refcounting reclaims everything (a Python-side subclass could add
  referencing attributes, but instantiating one is rejected). `__repr__`/`__str__` (-> `Py_tp_repr`/`Py_tp_str`, must return `str`),
  `__eq__`/`__ne__`/`__lt__`/`__le__`/`__gt__`/`__ge__` (-> one
  `Py_tp_richcompare` wrapper, one param beyond self typed as the record's
  OWN exposed-class type -- richcompare's one shared type-guard covers every
  comparison op, unlike arithmetic operators, so comparing against a scalar
  or a different exposed class is rejected at sema; must return `bool`), and
  `__hash__` (-> `Py_tp_hash`, must return a
  fixed-width int) are **wired into the host type**: a slot is present iff the
  record defines that dunder. `__ne__` auto-derives from `__eq__` when absent
  (same fallback as the non-exposed operator path); a richcompare dunder
  defined without `__hash__` makes the type **unhashable**
  (`PyObject_HashNotImplemented`). Comparing against an unrelated type returns
  CPython's own `NotImplemented` fallback. Two acknowledged divergences: (1) a
  wrong-typed comparison operand raises the operator-protocol `TypeError`
  *before* the method body runs (the C++ signature requires the declared
  type) -- a duck-typed source would instead raise whatever the body's first
  bad attribute access produces; this is TPy's pre-existing static-typing
  divergence from CPython surfacing cleanly, not a new one. (2) the
  unhashable-without-`__hash__` rule is **not** `__eq__`-specific the way a
  plain Python class-statement's rule is -- `PyType_Ready` nulls the hash
  whenever *any* richcompare dunder is defined (there's no per-dunder-name
  introspection available at the C level), so a class defining only
  `__lt__` becomes unhashable once exposed even though the plain-Python
  source stays hashable; unavoidable, no escape hatch beyond declaring
  `__hash__` explicitly. Arithmetic/ordering operators (`__add__`/`__sub__`/`__mul__`/
  `__truediv__`/`__floordiv__`/`__mod__`/`__pow__`/`__lshift__`/`__rshift__`/
  `__and__`/`__or__`/`__xor__`, their reflected `__r*__` counterparts, unary
  `__pos__`/`__neg__`/`__invert__`, and in-place `__i*__`) are also **wired**,
  into `Py_nb_*` slots: each binary op's wrapper handles both the forward and
  reflected dunder (whichever the record defines) with the same
  wrong-type-downgrades-to-`NotImplemented` behavior, and the non-self
  operand can be a scalar/enum as well as another `@export` class (e.g.
  `def __mul__(self, scalar: int64)` for `vec * 3`) -- not restricted to the
  record's own type. `__pow__`/`__rpow__` reject a real 3-argument modulus
  (`NotImplemented`, since TPy has no 3-arg `__pow__`). In-place ops mutate
  `self` and return the *same* object by construction (their slot never
  marshals a return) -- TPy already requires them to return `self`, not
  `Own[T]`. The container protocol is also **wired**: `__len__` -> both
  `Py_mp_length`/`Py_sq_length`; `__getitem__` -> `Py_mp_subscript` (a
  wrong-typed key's `TypeError` propagates directly -- no `NotImplemented`
  fallback, and slicing, `__getitem__(self, s: slice)`, is out of scope);
  `__setitem__`/`__delitem__` share one `Py_mp_ass_subscript` slot (`del
  obj[k]` when only one of the pair is defined raises `TypeError`, like a
  plain Python class with the same shape); `__contains__` -> `Py_sq_contains`
  (omitted when undefined -- `in` then falls back to iterating via
  `__iter__`/`__next__` for free, CPython's own behavior); `__iter__` ->
  `Py_tp_iter` (no self-identity requirement -- it may return a fresh
  iterator object, marshalled like any other exposed-class return);
  `__next__` -> `Py_tp_iternext` (implicitly `@error_return(StopIteration)`;
  exhaustion crosses as a bare Python `StopIteration` -- no argument, `value`
  `None` -- set straight from the returned error value, which is a plain value
  and never a thrown exception. An exported `__next__` must end with
  `StopIteration`: a user return-only exception is a compile error there, as is
  `@export` on a `ReturnException` class, since such a value never crosses to
  Python; a borrow-form return -- `-> Cls` / `-> list[T]` -- is
  unwrapped from its `val_or_ref` borrow slot before dispatch). Every
  marshalled-return dunder slot (arithmetic, `__getitem__`, `__iter__`,
  `__next__`) threads the same identity/view candidates a method wrapper
  does, so a borrow return of `self`, an exposed-class operand, or a
  never-reassigned field crosses as the ORIGINAL PyObject / a
  registry-deduped borrow view -- plain Python's aliasing (`x[0] is x[0]`,
  a `return self` subscript is `x` itself, inherited slots un-sliced);
  only the residue (reassignable fields, containers) copies with the
  standard warning. The arithmetic slots' identity path is currently
  unreachable from TPy source: two operator defects (the shim
  const-return mismatch; the `Own[readonly[Cls]]` result binding --
  BUGS.md) reject every arithmetic-dunder borrow return before codegen.
  `list(x)`, `iter(x)`/`next(it)`, and a `for` loop all
  work once these are wired. Any other dunder (`__call__`, `__bool__`, ...)
  is still **warned** -- not yet wired into the host type, so it would be
  silently absent otherwise.
- **Working (enums + constants)**: an enum marked `@export` in an ext_module is
  recreated at `PyInit_` as a **real CPython enum type** -- `enum.IntEnum` for an
  `IntEnum`, `enum.Enum` for a plain `Enum` -- via the stdlib `enum` functional
  API with `module=` set, so `mymod.Color.RED` is observably identical to a
  source-level `class Color(IntEnum)`: members, `.name`/`.value`, member
  identity/singletons, iteration, value/name lookup, `__name__`/`__qualname__`/
  `__module__`, and the IntEnum-vs-Enum `== int` / `isinstance(_, int)`
  distinction all match (CPython-parity-verified). Module-level `Final` constants
  of a boundary type (`int`/`IntN`/`bool`/`float`/`str`) are surfaced as
  module-attribute **snapshots** taken after module init (`Final` => immutable, so
  the snapshot can't go stale). The validator rejects `@export` on a `@native`
  enum (its values come from C++) or a nested enum (only top-level module enums
  are exposed). A `Final` constant of a non-boundary type (`char`, `tuple`, ...)
  is simply not part of the exposed surface -- like a non-`@export` function, it
  isn't on the `.so`. An exposed enum is also a valid `@export` **function
  param/return type**: the value crosses as its CPython member (`def f(c: Color)
  -> Color` receives/returns a `mymod.Color` member). IN marshalling is strict
  by type -- the arg must be an instance of the enum (`enum_from_py` reads its
  `.value`), so a bare int is rejected with `TypeError` even when its value
  matches a member; this differs from a lenient `int` param (which accepts an
  `IntEnum` member via `__index__`) and from the untyped Python source (where the
  annotation is a hint and `f(1)` runs). OUT marshalling reconstructs the member
  via `Color(value)`, preserving singleton identity. Deferred (tracked in
  `TODO.md`): nested/cross-module enums (a cross-module enum param is a located
  error), `bytes`/`BytesView` constants, and `int(enum)` conversion inside a
  body (orthogonal to the boundary).
- **Working (docstrings)**: a docstring on the exposed surface crosses as
  `__doc__`, so `help(mymod)` reads the same text the TPy source carries --
  the module (`m_doc`), `@export` functions and exposed-class methods
  (`ml_doc`), the class itself (`Py_tp_doc`), a `@property` (the getter's
  docstring, via `PyGetSetDef.doc`), an `@export` enum (set on the type after
  the stdlib functional API builds it, since that API takes no doc) and a user
  exception type (`PyErr_NewExceptionWithDoc`). The crossing text is DEDENTED
  the way CPython's compiler dedents it since 3.13 -- the first line's leading
  whitespace and the common indent of the remaining lines come off, so
  `__doc__` is what the equivalent Python source would report under a modern
  interpreter rather than the raw literal. (Under a 3.12 *host* that makes the
  exposed `__doc__` differ from what the interpreter reports for the same
  source: the emitted TU must not depend on which interpreter compiled it, and
  3.13+ is the behavior worth matching.) An undocumented callable keeps
  `__doc__` None. A plain annotated field has
  no docstring in Python either, so its getset carries none. **Excluded:**
  - A **DUNDER's** docstring (`__init__`, `__repr__`, `__eq__`, the operators,
    the container protocol). A dunder reaches CPython as a *type slot* -- a
    bare function pointer with no doc field -- so the text cannot cross, and
    CPython substitutes its own generic wording (`__repr__` reports
    `'Return repr(self).'`, `__init__` reports `'Initialize self. ...'`).
    Routing the dunder through a method descriptor instead WOULD carry the
    doc, but it replaces the direct slot call with a dict lookup on every
    invocation -- documentation is not worth taxing operator dispatch, so TPy
    keeps the fast slot and **warns once per class** naming the dunders whose
    text is dropped. Put that text in the **class docstring**, which does
    cross at zero cost; this is the same convention hand-written C extensions
    use (`help(dict)` shows `dict(mapping)` out of the class doc).
  - A docstring containing an embedded **NUL**, which no CPython doc slot (a
    NUL-terminated C string) can carry -- the glue drops it and warns rather
    than hand the host a truncated text.
  - An **empty** docstring on a function or method reports `__doc__` as
    `None` rather than `''`: CPython reads an empty `ml_doc` as no doc at
    all. Its own behavior for C-level callables, and no text is lost, so it
    is documented rather than warned. A module is the exception -- an empty
    `m_doc` does come back as `''`.
- **Planned**: zero-copy `str`/`bytes` view input (`StrView`/`BytesView` via
  the phase-3.5 foreign-borrow primitive),
  container getset fields at the exposed-class boundary, the PEP 517
  wheel backend, and the `nogil` GIL capability. See `docs/CPYTHON_INTEROP.md`.

---

## Variables & Scope

- **Working**: Local variables (inferred and annotated)
- **Working**: Global variables (typed)
- **Working**: Contextual type inference from assignment/return/nested-call context for generic functions, record constructors, and module-type constructors; partial explicit type args; `_` wildcard type arguments
- **Working**: CPython's function-scope binding rule -- inside a function a bare write to a name binds a fresh **local** for the whole body, even when a module global of that name exists and holds a different type; the local takes its own type from that write. Only `global X` makes the write land on the module variable. A read placed before the bare write is rejected at compile time (CPython raises `UnboundLocalError` at runtime; the TPy diagnostic still names `name.global_read` rather than the unbound local -- `BUGS.md#unbound-local-read-reported-as-global-read`), except when the read is an ATTRIBUTE of a record global, which still compiles and resolves to the global -- `BUGS.md#read-before-local-shadow-resolves-global`. It is the *function* boundary that starts a fresh binding, not a block: block boundaries inside one function create no scope, so the one-type-and-one-storage rule described under "Loops" -- the join of every binding to the name, wherever it stands -- still holds across them.
- **Working**: `global` keyword for explicit global mutation from functions and methods (annotated or bare module globals); the named global must already exist at module level (unlike CPython, `global x` cannot create a new one). Honoured by plain assignment, augmented assignment, tuple unpacking and `:=`; a `for`, `with ... as` or `match`-capture target still binds a local shadow instead -- see BUGS.md
- **Working**: `:=` walrus operator (assignment expression) -- `if`, `while`, `and`/`or` chains, general expression positions. Which branch of an `if` or `while` head may READ the target is the one definite-assignment rule stated under "A walrus in the head" above -- `if` and `while` answer it identically. Value types use `T x{}`; non-value types bound to a fresh rvalue use `std::optional<T>` wrapping; a non-value walrus bound to a BORROW (reference-returning call, field or subscript of a live lvalue) binds a `T*` pointer aliasing the source (mutations through it are visible on the source, matching CPython). A field of a const-inferred parent binds a `const T*` alias, so a read-only use builds and a mutating use fails the C++ build (loud, not silent; `BUGS.md#container-field-alias-mutation-not-credited`); a subscript on an RVALUE container is misclassified as a borrow -- see the BUGS.md `is_rvalue_source` subscript entry. Comprehension scope leak (PEP 572) supported. Reassigning an existing local via `:=` is type-checked against the local's type for value-typed locals (scalars, str/bytes, views, Optional-of-value); reassigning an existing non-value local via `:=` is rejected (use a separate assignment) -- see BUGS.md. A target declared `global` writes the module variable (coerced to the global's declared type), like the plain assignment; a reference-typed global target is rejected with the same diagnostic `g = ...` produces.
- **Working**: PEP 484 string type annotations (`def f() -> "ClassName"`, `def f(x: "ClassName")`, `children: list["Tree"]`). Strings are re-parsed as Python expressions at parse time and resolved by the same deferred type-resolver pass as bare annotations -- forward references to classes/aliases defined later in the same module work for names, generics (`"list[T]"`), and unions (`"A | B"`). A string that is not a valid Python expression is rejected with a clean tpyc diagnostic (no SyntaxError leak). `from __future__ import annotations` is accepted and ignored: it is a CPython runtime directive (PEP 563) that does not affect `ast.parse` output, so tpyc sees the same annotation AST nodes whether the import is present or not.

---

## Expressions

- **Working**: Binary/unary ops, calls, field access
- **Working**: Ternary `x if cond else y` (see [Conditionals](#conditionals))
- **Working**: List comprehensions `[expr for x in iterable if cond]`. When output size is compile-time known (`range()` with literal args, `Array[T,N]` source, no filter) and at most 1024, produces `std::array<T, N>` (zero heap allocation) built by aggregate construction via `tpy::array_from_index<T, N>` -- the element expression is evaluated exactly N times, left-to-right, each result constructed directly into its slot (no element default-construction or assignment, matching CPython's side-effect count), which also admits non-copyable / non-default-constructible elements (`@nocopy`, `__del__`-bearing, `Box`/`Rc`/`Task`). An element expression calling an `@error_return` function stays on the vector path (its unwrap emits function-targeting control flow that cannot cross the builder lambda). Otherwise `std::vector<T>` built in a GCC stmt-expr block with `push_back` and `reserve()` for Sized iterables. Supports tuple unpacking, annotation propagation. Loop variables use `const auto&` for non-value and expensive-to-copy types by default (matching for-loop const-ref optimization), but sema drops the const when the body calls a non-readonly method on the loop var (e.g. `[x.clone() for x in xs]` over `list[Rc[T]]`) -- the unpack-tmp + per-slot bindings drop const symmetrically. When the source yields `Own[T]` (a generator of owned values, or `Iterable[Own[T]]`), a bare loop-var element is *moved* into the result rather than copied -- the consuming-for-append move applied to the comprehension's last-evaluated sink (`auto&& node` + `push_back(std::move(node))`), so `@nocopy` owned elements collect without a copy error and the storage-copy warning is suppressed. This covers list, set, AND dict comprehensions: the dict *value* is the last sink and moves (`{node.id: node for node in g()}` -- the key, evaluated first, is sequenced into a local before the value move so a key that reads the same element is not a use-after-move); the dict *key* moves only when it is the genuine last use (`{node: node.id}` keeps the key copy -- the value reads the element after it). A filtered element (`[x for x in g() if p(x)]`) still moves -- the element runs after the filter, so it is the last use. A derived sink (`x.field`, `f(x)`) and a borrowed (list) source still copy.
- **Working**: Dict comprehensions `{key: value for x in iterable if cond}` -> GCC stmt-expr with loop + `insert_or_assign`. Supports tuple unpacking, annotation propagation, all iteration strategies, const-ref loop variable binding (dropped on body mutation, same as list comp)
- **Working**: Set comprehensions `{expr for x in iterable if cond}` -> GCC stmt-expr with loop + `insert`. Supports tuple unpacking, annotation propagation, all iteration strategies, const-ref loop variable binding (dropped on body mutation, same as list comp)
- **Working**: Lambda expressions `lambda x: expr` with `Fn` type inference (see Lambda / Closures section)
- **Working**: String slice `s[start:end]` -> `StrView` (zero-copy). Stepped `s[start:end:step]` -> owned `str`
- **Working**: Container slice `items[start:end]` -> `Span[T]` (zero-copy). Stepped `items[start:end:step]` -> owned `list[T]`. Supports list, Array, Span, Span[readonly[T]]
- **Working**: Bytes slice `b[start:end]` -> `BytesView` (zero-copy). Stepped `b[start:end:step]` -> owned `bytes`
- **Working**: User-type slice via `@overload __getitem__(self, index: basic_slice)` or `__getitem__(self, index: slice)` with `tpy::BasicSlice`/`tpy::Slice` dispatch. `basic_slice` coerces to `slice`
- **Working**: `basic_slice(start, stop)` and `slice(start, stop, step)` constructors with `int32 | None` args

---

### Evaluation Order (Open)

The subexpressions of ONE expression evaluate in the C++ compiler's order,
not Python's guaranteed left-to-right: g++ runs call arguments right to
left, clang left to right, and the standard guarantees neither. Affected are
the positional arguments of a call, constructor or method, both operands of
a two-operand binary operator, f-string interpolations, and the operands of
a moving container literal (including a dict literal's key-before-value);
brace-initialized literals, `<<` chains and 3+-operand operator chains are
sequenced. Statements always run in order; only side effects INSIDE one
expression can be observed out of order (`f(a(), b())` where both print).
Whether TPy will guarantee Python's order everywhere, only outside hot-path
annotated sections, or declare the divergence, is an open language decision
(TODO.md "Evaluation-order policy"); until then the sites are tracked, not
fixed, under `BUGS.md#subexpression-right-to-left-eval`.

## Error Handling

- **Working**: Runtime panics (e.g. fixed-int overflow, internal invariants -> abort)
- **Working**: `assert` (`assert cond`, `assert cond, msg`)
  - Emits `throw ::tpy::AssertionError(msg)` when condition is false (catchable)
  - Message can be any string expression (literal, variable, field access, method call)
  - Contributes control-flow narrowing facts
- **Working**: `@error_return(E)` -- zero-cost error returns via `std::expected<T, E>`
  - `@error_return(E)` requires E to be a `ReturnException` type: `class MyError(Exception, ReturnException): pass`
  - `ReturnException` is a marker protocol that splits exception types into return (zero-cost) vs throw (C++ exceptions) categories
  - `StopIteration` is a built-in `ReturnException` type; user-defined types opt in via `ReturnException` marker
  - A `ReturnException` is a **plain value, never thrown**: its C++ struct derives from the EMPTY `::tpy::ReturnException` value base (no vtable, no members), not from `::tpy::Exception` / `std::exception`, so a `pass` class is an empty struct; `::tpy::StopIteration` carries nothing and is an empty struct, so a scalar iterator step result is passed in registers. The class must derive directly from `Exception` (`class E(Exception, ReturnException)`) -- the Python base stays because CPython only raises `BaseException` subclasses -- and cannot itself be subclassed, with or without the marker (handling is by exact type, so a subclass would add no dispatch)
  - **Acknowledged CPython divergence:** a return exception carries only the fields it declares -- the inherited `message` field and `Exception(message)` constructor are not part of it, so `raise E("why")` on a `pass` class and `super().__init__(msg)` in its `__init__` are compile errors, a declared `message` must be `str` (it is what `str(e)` renders), and a caught return exception cannot yet be stored as an owned value -- copy the fields out. The error is moved, never copied, on its way up (each propagation moves it out of the dying result), so a `@nocopy` class or one with `__del__` works as a return exception (declare `message: str` and an `__init__` to carry one; only that class pays for the string). A bound one (`except E as e`) supports `str(e)` (its declared `message`, else `""`), its fields/methods and a bare `raise`, but it is NOT an `Exception` value: passing it where `Exception` / `BaseException` / `Throwable` is expected, or calling `e.clone()`, is a compile error naming the return-only exception (CPython accepts both), and `except Exception:` does not handle an `@error_return` call. Escape hatch: build a regular exception from it, `RuntimeError(str(e))`
  - `BaseException`/`Exception`/`ValueError`/`OSError`/`FileNotFoundError`/`PermissionError`/`FileExistsError`/`NotADirectoryError`/`IsADirectoryError`/`ConnectionError`/`BrokenPipeError`/`ConnectionResetError`/`ConnectionRefusedError`/`ConnectionAbortedError`/`BlockingIOError`/`AttributeError`/`AssertionError`/`LookupError`/`IndexError`/`KeyError`/`TypeError`/`NotImplementedError`/`ArithmeticError`/`ZeroDivisionError`/`OverflowError`/`FloatingPointError`/`RuntimeError`/`RecursionError`/`EOFError`/`MemoryError`/`StopAsyncIteration`/`CancelledError`/`GeneratorExit`/`KeyboardInterrupt` are throw-tier; `StopIteration` is `ReturnException` (return-tier, used via `std::expected<T, E>`). All defined as `@native` classes in `lib/tpy/_builtins/_exceptions.py`, mapping to `::tpy::` runtime structs (the throw-tier ones inherit from `std::exception`; `::tpy::StopIteration` is an empty value struct outside that hierarchy). The base/subtype edges match CPython's hierarchy: `ArithmeticError` parents `ZeroDivisionError`/`OverflowError`/`FloatingPointError`, `LookupError` parents `IndexError`/`KeyError`, `OSError` parents `FileNotFoundError`/`PermissionError`/`FileExistsError`/`NotADirectoryError`/`IsADirectoryError`/`ConnectionError`/`BlockingIOError`/`TimeoutError`, and `ConnectionError` in turn parents `BrokenPipeError`/`ConnectionResetError`/`ConnectionRefusedError`/`ConnectionAbortedError` (PEP 3151) (`BlockingIOError` raised on EAGAIN/EWOULDBLOCK/EINPROGRESS by non-blocking socket calls and by `os.read`/`os.write` on a would-block fd; `TimeoutError` raised on a `socket` timeout-mode expiry, matching CPython where `socket.timeout is TimeoutError`; the `ConnectionError` subclasses raised by the `socket` layer's errno mapping -- EPIPE -> `BrokenPipeError`, ECONNRESET -> `ConnectionResetError`, ECONNREFUSED -> `ConnectionRefusedError`, ECONNABORTED -> `ConnectionAbortedError`; the `FileExists`/`NotADirectory`/`IsADirectory` trio raised by the `os` syscall errno table), `RuntimeError` parents `RecursionError`. `OSError` (and every subclass) carries CPython's structured `.errno` / `.strerror` / `.filename` / `.filename2` attributes and CPython's constructor forms: `OSError(message)`, `OSError(errno, strerror)`, and `OSError(errno, strerror, filename)` (builtin-stub `@dispatch` variants, inherited by the whole family via `using Base::Base` -- both direct construction and `raise X(...)`). The errno-taking ctors format the message to CPython's exact `str(e)` at construction time (`"[Errno N] strerror[: 'filename'[ -> 'filename2']]"`). Attributes are populated everywhere errors originate: the `socket` layer, the asyncio `sock_connect` completion, and the os/file C++ raise sites (one shared PEP 3151 errno -> subclass table, `tpy::raise_mapped_os_error`; `os.rename`/`replace`/`link`/`symlink` carry `.filename2`), and are comparable against the `errno` module's constants (`e.errno == errno.ENOENT`); `socket.gaierror` (also an `OSError`) carries the EAI_* code there. CPython's `OSError.__new__` errno -> subclass mapping (`FileNotFoundError` for ENOENT, ...) is reproduced at RAISE time: the errno-taking ctors record the mapped kind at construction (like `__new__`; post-hoc attribute assignment never re-maps, and explicit subclasses keep their type), and raising the object -- both `raise OSError(err, ...)` directly and a bound `e = OSError(err, ...); raise e` -- surfaces the mapped subclass, so `except FileNotFoundError` catches exactly like CPython. The mechanism is the library-declared `@virtual_raise` marker on the stub plus the dispatching C++ `__raise__`; the compiler holds no OSError-specific logic. Divergences, all declared: unset values are `0` / `""` rather than `None` (`e.errno is None` is a compile error; use falsiness); `str(e)` is fixed at construction, so a post-hoc attribute assignment does not reformat it (CPython recomputes from attributes); type introspection of an UN-raised errno-form bare `OSError` value (`isinstance(e, FileNotFoundError)` before any raise) sees plain `OSError` where CPython already sees the subclass; CPython's 5-arg `(.., winerror, filename2)` ctor and single-int `OSError(2)` forms are compile errors; user-defined subclasses (`SocketError`, `gaierror`, the requests tree) expose a single `(errno, strerror)` `__init__` until user-class ctor overloads exist (BUGS.md). Errnos whose CPython class TPy lacks (EINTR/ECHILD/ESRCH -> `InterruptedError`/`ChildProcessError`/`ProcessLookupError`) surface as plain `OSError`. `CancelledError`, `GeneratorExit`, and `KeyboardInterrupt` inherit `BaseException` directly (not `Exception`) so `except Exception` does not silently swallow them; `GeneratorExit` is also constructed by the frame destructor and passed to `with.__exit__` when an abandoned generator/coroutine closes a suspended `with` region; `KeyboardInterrupt` is raised by `asyncio.run` on an uncaught SIGINT graceful shutdown. `StopAsyncIteration` is the loop-termination signal for `async for`.
  - Decorator on functions: `raise E` compiles to `return std::unexpected(E{})`; `raise E(args)` passes constructor arguments
  - Callers must use `try/except E` or be `@error_return(E)` themselves (auto-propagation)
  - `try/except/else` supported; `except E as e` binds the error value for field access
  - Exception types can have data fields: `class ParseError(Exception, ReturnException): line: int32`
  - Goto-based dispatch: error_return calls inside nested if/for work correctly
  - `except E as e` uses `std::optional<E>` for zero happy-path cost error capture
  - Branch-aware flow analysis (narrowing, init tracking, variable hoisting)
  - Expression-level unwrap: `@error_return` calls work in sub-expression position (function arguments, binary operators, method chaining) -- uses GCC/Clang statement expressions (`({ ... })`) for inline unwrap with early return. This is a non-standard C++ extension supported by GCC, Clang, and all LLVM-based compilers but not MSVC.
  - Non-value return types use `val_or_ref<T>` inside `std::expected` to preserve reference semantics (pointer-based storage, same approach as iterator `__next__()`)
  - Statement-level unwrap moves owned success payloads out of the dying `std::expected` (`unwrap_ref_move`), so `@nocopy` results bind to locals/fields without a copy; a borrowed (`val_or_ref`) payload bound to a local ALIASES the source (pointer binding -- mutations through the local are visible on the source, matching CPython) and is never moved from. Field targets still copy (warned; `copy()` is the escape hatch).
  - `__next__` methods auto-apply `@error_return(StopIteration)` -- for-loops use direct `std::expected` check
  - Built-in exceptions (`StopIteration`, `Exception`, `BaseException`) emit as `::tpy::X` in C++ to avoid clashes with user-defined classes of the same name
  - See `docs/ERROR_RETURN_DESIGN.md` and `docs/EXCEPTION_DESIGN.md` for full design
- **Working**: General C++ exceptions (`throw`/`catch`) for non-ReturnException exception types
  - `raise E`, `raise E(args)`, or `raise <expr>` in non-`@error_return` functions compiles to C++ `throw`
  - `raise <expr>`: raise pre-constructed exception variables (`e = MyError(42); raise e`) or function/method results
  - `try`/`except E`/`else`/`finally` with C++ `try`/`catch` for throw-tier exceptions
  - Multiple `except` handlers with type matching (first match wins)
  - The tuple form `except (A, B):` catches any of several types with one handler body. C++ has no multi-type catch, so the parser expands the clause into one ordinary single-type handler per element, in source order; each element must be a simple or dotted name. `except (A, A)` (or a type repeated by spelling) collapses to one catch arm.
    - Because each element becomes its own clause, an `as e` binding is typed to that arm's exact type -- so a body may use any member the caught type has, even one the elements declare independently rather than inheriting from a common base. The body must type-check under every element's type.
    - `except ():` is rejected (it catches nothing; CPython accepts it as a no-op clause).
    - Not supported on a return-tier (`@error_return` / `ReturnException`) try/except, which carries a single error type; catch one type per clause there.
    - Two limitations follow from the per-element expansion, both shared with hand-written sibling `except` clauses and tracked in BUGS.md. Naming a class **and a subclass of it** in one clause (`except (OSError, PermissionError):`) makes the later element's arm unreachable: it runs correctly and matches CPython under the default CLI, but a strict-warning build rejects the dead arm. And because the arms share one written body, a **borrow/reference local** assigned in it (`v = e.payload.get(...)`) hits the sibling-scope declaration bug -- with no workaround, since the arms cannot be given distinct names.
  - Bare `except:` catches any exception (maps to `catch(...)`)
  - `except E as e` binds the caught exception for field access
  - `except mod.E` / `except pkg.sub.E`: module-qualified exception class names (after `import mod` / `import pkg.sub`); a local non-exception class with the same bare name does not shadow the qualified target
  - `finally` block via catch-all + explicit execution (runs on all exit paths)
  - `raise` inside `finally` (replaces pending exception, Python semantics)
  - `return`/`break`/`continue` inside try-with-finally (goto transformation)
  - `return <expr>` inside try-with-finally (or `with`) evaluates the return expression BEFORE the finally body / `__exit__` runs, into a temp typed with the function's return type -- and still evaluates it (side effects included) when the finally's own return/raise overrides the pending value (CPython evaluation order)
  - `return <reference-type local>` inside try-with-finally (and `return self` in a consuming method, whose receiver takes the same deferred capture) captures only a borrow before the finally chain and materializes (moves) the value AFTER it, so a finally that mutates the returned local is visible in the returned object -- CPython's pending return is an alias, and the deferred move also makes the shape legal for `@nocopy` types and list/dict/set locals (no `copy()` required). Rebinding the local in the finally leaves the pending (pre-rebind) object intact, and value-type locals keep the eager capture -- both matching CPython. `del` of the borrowed local inside the finally is rejected, as is `nonlocal`-`del` of it inside a closure (the pending return still reads its storage; restructure by moving the `del` past the `try` or dropping it -- the local dies at function exit anyway). A closure that REBINDS the returned name keeps the eager capture (pre-rebind copy, matching CPython for the pure-rebind shape -- see BUGS.md for the nested-def rebind slot-model gap). Deferral is structural -- every eligible return under a non-suspending finally defers, so mutations through ANY channel (direct, alias, closure) are visible. Residual eager-capture shapes (tuple returns with reference elements; a finally that itself suspends via `await`/`yield`) are tracked in BUGS.md.
  - Variables first bound inside a `finally` body hoist like try-body bindings: usable across the duplicated finally emissions and visible after the `try` statement (Python function scoping)
  - A variable assigned on every non-terminating path of a `try`/`except` (the try body and each handler) and read after the block hoists to the outer scope and is visible after, like an `if`/`else` var assigned in all branches; one assigned on only some paths is rejected (`'x' may not be assigned`)
  - Nested `try`/`finally` (pending actions propagate through all levels)
  - `try`/`finally` without `except` (pure cleanup)
  - Re-raise: bare `raise` inside `except` block re-throws (`throw;` in C++)
  - Throw-tier raises allowed inside `@error_return` functions (orthogonal to return-tier)
  - Built-in exceptions: `ValueError(message)` etc. with `message: str` field; the `OSError` family adds the CPython `(errno, strerror[, filename])` ctor forms (see the exception-hierarchy bullet above)
  - User-defined throw exceptions: `class MyError(Exception)` with optional `__init__` + data fields
  - Tier separation enforced: ReturnException and non-ReturnException types cannot be mixed in same `try`/`except`
- **Working**: Polymorphic exception storage via `Box[Throwable]` (Phase 20)
  - `Throwable` is the ABI protocol all exceptions implement (declared in `tpy._core._types` as `@native + @dynamic`, bridged to `::tpy::Throwable` in the runtime header)
  - User-facing pattern: `self.exc: Box[Throwable] | None` stores a caught exception, preserving its dynamic subclass through the cycle
    ```python
    try:
        risky_call()
    except BaseException as e:
        self.exc = Box(e.clone())
    # ... later ...
    if self.exc is not None:
        raise self.exc   # rethrows as the original concrete subclass
    ```
  - `e.clone()` returns `Own[Throwable]` (heap-allocated polymorphic copy at the concrete type); `Box(...)` wraps it into an owned slot
  - `raise <expr>` lowers through the Throwable vtable's `__raise__()` virtual override (`throw *this` at the concrete subclass). For Box-wrapped expressions, codegen auto-inserts `.__deref__()` so `raise self.exc` works directly. `raise X(args)` keeps the idiomatic `throw X(args)` form via a fresh-construction peephole (mechanically equivalent — the macro-emitted `__raise__()` body does `throw *this`)
  - Slicing-site sema rejection: storing a polymorphic-exception borrow into an owned slot of the same (or strict-subclass-of) type is rejected at compile time, with a diagnostic pointing at `Box[Throwable]`. Fresh constructor calls, `Own[T]` moves, and `None` are allowed
  - Throwable subclass requirements (sema-enforced): must inherit `BaseException` (or descend through one); must be copy-constructible (auto-emitted `clone()` + `__raise__()` need a usable copy ctor); cannot user-define `clone()`/`__raise__()`/`what()` (codegen auto-emits these on every concrete Throwable subclass; user override would collide at the C++ level)
- **Open**: Warning when exceptions are used for control flow (e.g., `try: Color(99) except ValueError` to test validity) -- prefer safe alternatives like `try_parse()`

---

## Concurrency

### Thread Safety Markers (Send/Sync)

The full marker-layer design lives in `docs/SEND_SYNC_DESIGN.md`; Phase 2
(marker-layer correctness, wrappers, bounds, frames, override kit) is done.

- **Working**: `is_send()` / `is_sync()` methods on all types in the type system
- **Working**: Auto-derivation for user records based on field types
  - A record is Send if all fields are Send (safe to transfer across threads)
  - A record is Sync if all fields are Sync (safe to share references across threads)
  - Parent class Send/Sync status is also considered
  - Generic records answer per concrete instantiation (use-site field walk)
- **Working**: `Send[T]` / `Sync[T]` marker wrapper types. For non-erased
  types the wrapper is a static assertion checked at resolve time
  (`Send[int32]` is just `int32`; `Send[Ptr[int32]]` is a compile error).
  For erased types (`Callable[...]`, `@dynamic` protocols) the wrapper
  persists sema-side with the same C++ representation as the bare type, and
  the assertion fires at every erasing conversion against the concrete
  value. Canonicalization (resolver-time): distributes over unions /
  optionals, idempotent, `Send` floats outside `Sync`, markers float
  outside `readonly` / `Own`.
- **Working**: `T: Send` / `T: Sync` generic bounds on functions and records
  (conformance is the type's own derived trait). A `Send`/`Sync`-bounded type
  parameter also satisfies that marker bound when forwarded to a nested generic
  (e.g. `inner[T](x)` inside `def outer[T: Send]`) -- the bound guarantees the
  substituted type is Send/Sync.
- **Working**: closure/coroutine/generator frame classification (`FrameType`).
  A lambda / nested def / free function converts into `Send[Callable[...]]`
  when its captured-state frame is Send (no captures, by-value captures of
  Send types); borrowed/view captures reject. Async coroutines and
  generators classify their frame slots (params, hoisted locals, awaited
  sub-frames) conservatively: anything sema cannot classify makes the frame
  non-Send/non-Sync.
- **Working**: opt-in / opt-out kit: `class Foo(Send)` (checked claim --
  errors naming the offending field if the structural answer disagrees),
  `@unsafe_send` / `@unsafe_sync` (force true; records and function
  frames), `@nosend` / `@nosync` (force false). Contradictory or redundant
  combinations on one target are rejected.
- **Working**: conditional override -- `@unsafe_send`/`@unsafe_sync` with the
  `if_params_send` / `if_params_sync` bool kwargs on a generic record: the trait holds at an
  instantiation iff every type parameter satisfies the listed markers,
  else it falls back to the structural answer. The analog of Rust's
  `unsafe impl<T: Send + Sync> Send for Arc<T>` -- lets a structurally
  non-Send handle (raw-pointer fields) be Send/Sync exactly for the type
  args that make it sound. Backs `Arc[T]` (Send + Sync iff `T` is) and
  `Mutex[T]` / `RwLock[T]` (Send + Sync iff `T: Send`). The why-not chain attributes a
  failure to the offending type parameter and the marker it lacks (naming
  the *other* required marker on a cross-trait miss), not to the internal
  fields the override abstracts over. Conflicts with the unconditional
  `@unsafe_send` / `@nosend` on the same trait are rejected.
- **Working**: `Send[Pet]` for `@dynamic` protocols -- same Adapter /
  RefAdapter representation as bare `Pet`; the concrete argument's
  Send-ness is checked at the erasing conversion (pointee-level assertion;
  see SEND_SYNC_DESIGN.md OQ5 for the pointee-vs-handle model).
- **Working**: C++ `is_send<T>` / `is_sync<T>` traits and `Send` / `Sync` concepts in runtime
- **Working**: test annotations `# tpyc: is_send(yes|no)` / `is_sync(...)`
  (declaration lines) and `# tpyc: frame_send(yes|no)` / `frame_sync(...)`
  (async/generator def lines)
- **Working (Phase 3)**: diagnostic surface. `tpy.assert_send[T]()` /
  `tpy.assert_sync[T]()` are zero-cost compile-time assertions (no emission;
  CPython no-ops) that fail the build with a why-not chain when `T` is not
  Send / Sync. The same chain is appended to the Send/Sync enforcement-site
  errors (`Send[T]` conversion, `T: Send` bound, `class Foo(Send)` opt-in).
  At a `Send[Callable[...]]` conversion the chain names the offending
  captured slot of the concrete lambda / function-reference value (e.g.
  `captured 's' is not Send`) rather than the generic erased-callable leaf.
  `tpyc --explain-send TYPE` / `--explain-sync TYPE` prints the structural
  derivation for any named type (e.g. `list[Order]`).
- **Working (Phase 4)**: first enforcement site -- `Channel[T]`. The
  `channel[T: Send](cap) -> (Sender[T], Receiver[T])` factory (in
  `tpy.channel`) rejects a non-Send payload via its `T: Send` bound, with the
  why-not chain. SPSC, `Rc`-backed FIFO ring on the single-threaded async
  executor; blocking `await tx.send(v)` / `await rx.recv()` and explicit
  `tx.close()` (`recv` raises `ChannelClosed` once closed + drained). See
  `docs/CHANNEL_DESIGN.md`.
- **Working (V1)**: real OS threads -- `tpy.thread.spawn[R: Send, T:
  ThreadTask[R]](task: Send[Own[T]]) -> Own[JoinHandle[R]]` (in `tpy.thread`).
  Moves a `Send` task struct (structural `run() -> R`) onto a `std::thread`;
  `JoinHandle.join()` blocks and returns the result or re-raises the task's
  exception (via `std::packaged_task`/`std::future`); `detach()` abandons it.
  The result slot is `Own[R]`, not a bare `R`: it is moved out of the future,
  so a reference-typed result (a `list`, a record) lands owned in the joiner
  rather than as a borrow of a task the thread already destroyed.
  Both the task (`Send[Own[T]]` wrapper) and the result (`R: Send` bound) are
  Send-checked at the call site with the why-not chain. `JoinHandle` is
  `@nocopy` and aborts loudly if dropped without `join`/`detach`. The call is
  fully inferred -- `spawn(task)` solves `T` through the `Send[]` wrapper and
  `R` from the conformer's `run()` return type (associated-type inference);
  a fire-and-forget `run() -> None` task works in both forms. The inferred
  form is valid CPython (stub in `lib/cpy/tpy/thread.py`); the explicit
  `spawn[R, T](...)` form remains TPy-only.
  `-lpthread` is import-gated. This is Rust's Send+move model, not CPython
  threading -- see `docs/THREADING_DESIGN.md`.
- **Working (V3)**: `Mutex[T]` / `RwLock[T]` (in `tpy.sync`, over `std::mutex`
  / `std::shared_mutex`). The interior-mutability primitives: `@nocopy`.
  both `Mutex[T]` and `RwLock[T]` are `Send + Sync` iff `T: Send` (conditional
  override). For `RwLock` this is looser than Rust's `T: Send + Sync` and sound
  on the safe surface -- a safe not-`Sync` `T` is always a container whose
  shared-mutability the readonly read guard removes; TPy has no safe interior
  mutability (unlike Rust's `Cell`). It is *not* sound for a
  `Send`-but-not-`Sync` interior-mutable payload built with the unsafe
  `unsafe_interior_mutable` hatch (a user `Cell`-analog) -- a latent hole, low
  priority (see `BUGS.md`). `lock()`
  / `read()` / `write()` are `@readonly` -- acquired through a shared handle, so
  `arc.lock()` works via Arc's deref chain -- and return a `@nocopy`
  `Deref[T]` guard used as a context manager (`with m.lock() as g:
  g.append(x)`, deref forwarding to the payload; value-type payloads via
  `g.get()`/`g.set()`; RwLock read guard yields `readonly[T]`, write guard
  mutable `T`). The guard acquires in `__enter__` and releases in `__exit__`,
  so release is guaranteed on every exit path; a guard never entered never
  blocks. Canonical shared-mutable form is `Arc[Mutex[T]]`, reached per-thread
  via `arc.clone()`. `-lpthread` import-gated; CPython-parity via
  `lib/cpy/tpy/sync.py`. See `docs/THREADING_DESIGN.md` V3. (Current cost:
  `Arc[Mutex[T]]` is two heap allocations vs Rust's one -- a single-alloc
  fusion is a filed perf TODO.) **Guard misuse is caught at runtime:** a guard
  carries a `_locked` flag and payload access (`get`/`set`/`__deref__`) raises
  `RuntimeError` if the lock isn't held, so a guard used outside its `with`
  block -- bound but never entered, or read after the block -- aborts loudly
  instead of racing the payload. **Residual limitations (BUGS.md):** a guard
  outliving a dropped *bare* (non-`Arc`) `Mutex`/`RwLock` still dangles (the
  lifetime half, region-model-gated -- the flag can't catch it); and a payload
  borrow pulled out of a guard and used past the `with` block races/dangles even
  under `Arc` (also region-model-gated).
- **Working**: `Condvar` (in `tpy.sync`, over `std::condition_variable`) --
  a blocking condition variable pairing with `Mutex` for producer/consumer
  handoff. `@nocopy`, unconditionally `Send + Sync`. `wait(g)`
  releases the held lock, blocks, and reacquires; `notify_one`/`notify_all`
  wake waiters. It is the prerequisite for the blocking cross-thread MPSC
  channel (below); see `docs/CHANNEL_DESIGN.md`.
- **Working**: blocking cross-thread MPSC channel (`tplib.channel`) -- the
  Go-style channel over real OS threads (`tpy.thread.spawn` is the goroutine),
  distinct from the async SPSC `tpy.channel`. `channel[T: Send](cap)` returns a
  `(Sender[T], Receiver[T])` pair; `send`/`recv` block the calling thread. State
  is `Arc[_Chan[T]]` where `_Chan` bundles a `Mutex[_Buf[T]]` fixed-capacity ring
  with `_not_full`/`_not_empty` `Condvar`s -- so handles are `Send` iff `T` is and
  cross threads (unlike the `Rc`-backed async channel). `Sender.clone()` mints
  another producer (multi-producer); `Receiver` is single-consumer, draining via
  `recv() -> Own[T]` (raises `ChannelClosed` once closed + empty) or a generator
  `__iter__` (`for v in rx:`). Close is drop-based: dropping a `Sender` = "this
  producer is done", the last sender's `__del__` auto-closes (takes the lock,
  flips closed, notifies -- relies on the `__del__` fail-fast + drop-at-`run()`-
  completion semantics), and any `close()` force-closes. Caveat: the
  last-`Sender`-drop close fires when the `Sender` goes out of scope (or its
  owning spawned task completes), NOT when a named `Sender` local is moved into
  a helper to drop it early -- that defers to the caller's scope end (a filed
  drop-timing limitation, see `BUGS.md`), so call `close()` explicitly for an
  early close. `no_cpython` (the
  `channel[T](cap)` factory needs an explicit type arg that isn't valid runtime
  Python, and there's no `lib/cpy` threading runtime). `tests/cases/threading/
  channel_mpsc` (fan-in, `@nocopy` payload forces move-through),
  `error_channel_mpsc_not_send`, `panic_channel_mpsc_capacity`. See
  `docs/CHANNEL_DESIGN.md`.
- **Planned**: closure `spawn`,
  scoped threads / `TaskGroup`, multi-threaded executor; channel MPMC
  (multi-consumer), `select`, unbuffered/rendezvous (deferred -- see
  `docs/CHANNEL_DESIGN.md` / `docs/THREADING_DESIGN.md`).

Send/Sync rules for built-in types:

| Type | Send | Sync | Notes |
|------|------|------|-------|
| Value types (int32, bool, float, str, ...) | Yes | Yes | Copied, no aliasing |
| `bytearray` | Yes | No | Mutable buffer; same Sync rule as `list[T]` |
| `Ptr[T]` | No | No | Raw pointer, no ownership guarantee |
| `Ptr[readonly[T]]` | No | Yes (if T Sync) | Read-only shared access |
| `list[T]` | Yes (if T Send) | No | Mutable container |
| `dict[K, V]` | Yes (if K,V Send) | No | Mutable container |
| `Array[T, N]` | Yes (if T Send) | Yes (if T Sync) | Fixed-size |
| `Span[T]` / `Span[readonly[T]]` | No | ReadOnly: Yes (if T Sync) | Non-owning view |
| `StrView` | No | Yes | Non-owning read-only view |
| `readonly[T]` | Same as T | Yes (if T Sync) | Borrow-side restriction, not a freeze |
| `Own[T]` | Yes (if T Send) | No | Single-owner move slot |
| `Callable[[...], R]` | No | No | Erased closure; opt in via `Send[Callable[...]]` |
| `tuple[T1, T2, ...]` | Yes (if all Ti Send) | Yes (if all Ti Sync) | Composite |

- **Working (v1)**: `async`/`await` -> resumable-frame state machines.
  Declaring an async function or method needs no `asyncio` import: the compiler
  adds its `tpy.coro` runtime dependency automatically, including for unused
  inline async declarations. This adds no Python name or runtime import call.
  The dependency follows configured library paths; if the coroutine runtime is
  unavailable (for example with `--no-stdlib`), compilation reports a located
  configuration error instead of emitting an incomplete frame.
  `async def f() -> T:` lowers to a struct with
  `__poll__(Waker) -> Own[Poll[T]]`. `await <call-to-async-def>` inlines
  the sub-coroutine struct in the parent's frame; `await <Task[T]>` /
  `await <Future[T]>` / `await <user awaitable>` use structural dispatch
  on a `__poll__(Waker) -> Own[Poll[T]]` method (dunder name matches
  `__iter__` / `__hash__` / etc.). `Poll[T]` is a pure-TPy `@nocopy`
  record (`lib/tpy/tpy/_core/_types.py`); the previous
  `tpy::Poll<T>` / `Poll<void>` / `Poll<T&>` C++ template + its two
  specializations were removed in the v1.2 Poll-port. The single TPy
  body's `UninitArrayStorage[T, 1]` + `_has` storage matches the old
  `std::optional<T>` layout byte-for-byte, with `T = None` lowering to
  `std::monostate` to cover the void analog and reference-T positions
  going through the caller's storage form (no separate specialization
  needed). Value-type awaitables are moved into the frame; reference-type
  awaitables are stored as borrowed frame pointers so object identity is
  preserved across a suspension. Awaits in unconditional non-statement positions (call
  args, BinOps, conditions evaluated once) are lifted to preceding
  `__await_lift_<n>` vardecls. Awaits in *conditional / repeated*
  positions -- short-circuit `and`/`or` operands, ternary branches,
  chained comparisons, and `while` conditions -- are first rewritten by a
  pre-sema desugar (`tpyc/parse/desugar_suspensions.py`) into
  statement-position suspensions guarded by explicit control flow, so
  Python evaluation order is preserved (the skipped short-circuit operand
  is not awaited; the loop condition is re-evaluated each iteration).
  `await` inside a comprehension and divergent-type ternary branches are
  rejected (see TODO.md / BUGS.md).
  **Arbitrary `await` placement** (v1.5): `await` is allowed inside
  `if` / `elif` / `else` branches (including nested), `while` loop
  bodies (including with `break` / `continue` from nested if-branches),
  sync `for` loop bodies (range, list, any iterable -- v1.5 M3.1),
  sync `with X:` / `with X as t:` bodies (single or multiple context
  managers, optional `__exit__` suppression -- v1.5 M3.2),
  general `try` / `except` / `finally` blocks (multiple awaits per
  try body, awaits inside except handlers, nested try/except/finally
  with correct cleanup ordering -- except a `try` whose handler catches
  a `ReturnException` (e.g. `StopIteration` from a handled `next()`)
  and that itself needs frame states, which is rejected, see
  `BUGS.md#frame-try-next-error-return-miscompiles`), and `await` inside `finally` bodies
  (v1.5 M3.3 + M3.3.1 + M3.3.2 -- in-flight exception saved to a
  `std::exception_ptr` frame slot; works with `except` handlers; a
  `return` inside the try body, any handler, or the suspending
  `finally` itself is parked in a shared return-value slot and emitted
  after the finally body completes -- a return-in-finally also
  swallows any captured in-flight exception, per Python semantics). The lowering uses a localized CFG
  built per async def body (`tpyc/codegen_cpp/resumable_cfg.py`); each
  suspension is a CFG yield-edge and the state machine emits
  `while (true) switch (state)` with each resume case body wrapped in
  the source-level try/except/finally stack active at that suspension
  point (replays the region stack inside each case -- the C# Roslyn
  pattern, forced by C++'s prohibition on jumping into try blocks
  from outside). `for`-with-await uses the universal `::tpy::__iter__`
  / `__next__()` path with iterator + next-result stored as
  `std::optional<decltype(...)>` frame fields (no range-counter
  peephole inside async functions; peepholes still apply to non-async
  for-loops). The CFG infrastructure is shape-neutral; generators
  consume the same module.
  Function-level locals become frame fields so they survive a
  suspension, including **destructuring-assignment targets**
  (`a, b = make_pair()`): value targets get a value field, `Own[T]`
  targets an owning `frame_slot<T>` (moved out of the consumed tuple),
  and a **borrow alias** -- a non-value local aliasing existing storage,
  whether single-assign (`a = items[0]`) or a tuple-unpack borrow target
  (`a, b = first_two(items)`) -- a `T*` (or `const T*`) field aliasing the
  live source, so mutation through it after the suspension is visible on
  the source, matching CPython (a value-copy field would silently diverge).
  An exception-handler binding aliased into a local keeps an owning copy
  (the caught exception is only live inside the handler).
  Nested suspending `finally` works (the inner `AsyncFinallyExit`
  forwards any pending exception or pending return into the outer's
  parking slots before transitioning to the outer's finally entry).
  `await` inside a `match` works (the `match` is decomposed like the
  other compounds: arm bodies become states and the type-aware dispatch
  is reused; pattern bindings are frame fields). Throw-tier exceptions
  only; including catching `CancelledError`.
  `Task.cancel()` flips a flag that the next poll checks and throws
  `CancelledError`. `asyncio.run(coro)` drives an executor whose body is
  TPy code (`lib/tpy/asyncio/_executor.py` -- slot table for parked
  tasks, runnable deque, timer min-heap). Dispatch from suspended
  awaitables back to the executor goes through the `@dynamic Awaker`
  protocol vtable: `Executor` inherits `Awaker`; `Waker` (a pure-TPy
  `ValueType` in `tpy.coro` -- 16-byte POD passed by value through
  every `__poll__` call) carries a non-owning `Ptr[Awaker]` to the
  running executor plus slot id/generation, and `Waker.wake()`
  schedules the parked task by id/generation via the protocol vtable.
  `runtime/cpp/include/tpy/async.hpp` is down to ~34 lines containing
  only `CancelledError`. `asyncio.run` installs a **SIGINT graceful-shutdown
  handler** for the duration of the run (matching CPython's asyncio.run):
  Ctrl-C cancels the root task so its `finally` / `__aexit__` / `wait_closed`
  cleanup runs, then `asyncio.run` raises `KeyboardInterrupt`. An
  async-signal-safe handler sets a flag and writes a wakeup fd (an `eventfd`
  on Linux, a self-pipe on macOS / *BSD) registered in the executor's reactor,
  so a signal wakes a blocked `epoll_wait` / `kevent` race-free; the prior
  disposition is restored on exit. SIGTERM
  is left at its default (terminate), also matching CPython. (`raise_signal` /
  `SIGINT` / `SIGTERM` are exposed by a minimal `signal` stdlib module.)
  `asyncio.sleep(s)` is a real
  wall-clock sleep; `asyncio.create_task(coro)` registers the task with
  the executor for concurrent scheduling and returns an `Own[Task[T]]`
  handle (T inferred from the async def's return type) that shares state
  with the executor -- the caller binds it to a `Task[T]` local via the
  implicit `Own[]` unwrap and can then `await` the local or pass it as a
  borrow; `await asyncio.create_task(...)` on a temporary works too
  (move-constructed into the awaiter frame). A coroutine can also be
  BOUND first and consumed later: `c = f()` binds an owned, move-only,
  single-use handle holding the CONCRETE coro frame inline
  (C++: `std::optional<__coro_f>`) -- **binding a direct async-def or
  async-method call never allocates**. `await c` polls the handle's own
  slot in place (identical cost to inline `await f()`); the one heap
  allocation happens exactly at type-erasure boundaries --
  `create_task`/`run` args and `Own[Cancellable[T]]` params/returns --
  via the `make_adapter` wrap
  (`unique_ptr<Cancellable<T>>`). This is a guaranteed representation
  rule, not an optimization: no generated-code inspection needed.
  So `create_task(coro)` heap-boxes the coroutine FRAME exactly once
  (that `make_adapter` box is the frame's one allocation -- `Box[Cancellable[T]]`
  in `_build_task` adopts the same pointer; the Task's shared state is a
  separate `Rc` cell, and `@noalloc` reports both), while `await f()` on a
  concrete coroutine is allocation-free.
  A handle can be awaited (`await c`), passed to `create_task`/`run`,
  held across a suspension (a concrete frame field), moved to another
  binding, or returned from a sync coroutine factory
  (`-> Own[Cancellable[T]]`, an erasure boundary). Two shapes stay
  erased by construction (each an author-visible signature boundary):
  values received through `Own[Cancellable[T]]` (factory returns,
  params), and bindings of template-frame callees (async defs with
  static-protocol / `Fn` params, whose struct name needs call-site
  deduction). A concrete
  binding holds exactly one frame type: rebinding or branch-binding a
  DIFFERENT coroutine under the same name is rejected (bind to a new
  name; `Box[Cancellable[T]]` is the intended heap-storage vocabulary
  when one slot genuinely must hold different coroutines -- but note
  only CONSTRUCTION is wired today; consuming a coroutine through a
  Box (await / create_task via take()) is not, see TODO). RECURSIVE bindings
  (direct or mutual) are rejected for the same reason inline recursive
  awaits are -- the by-value frame embedding would be infinite-size --
  with the working escape named in the diagnostic: drive the recursive
  call through `asyncio.create_task` (the Task provides the heap
  indirection). A bound async-METHOD coroutine
  borrows its receiver by reference for the handle's lifetime: the
  receiver must be a stable lvalue at the binding, the borrow is
  registered until consumption, and escapes that can outlive the scope
  (`create_task(c)`, returning the handle) warn -- receiver lifetime
  is not tracked across those escapes (post-THIR escape analysis).
  Consumption moves the handle: a second `await c` / `create_task(c)`
  or a use after `d = c` is a compile-time error (CPython's runtime
  "cannot reuse already awaited coroutine" surfaced early; note the
  divergence -- CPython *aliases* coroutine objects on assignment, TPy
  *moves*). A handle never consumed warns at compile time ("bound
  coroutine is never consumed"), as does rebinding over an unconsumed
  one -- the CPython "coroutine was never awaited" RuntimeWarning at
  compile time; execution matches (the coroutine never runs). Caveat:
  the warning is cleared by ANY read of the handle, not only a true
  consumption (e.g. `c.cancel()` on a fresh handle silences it) -- see
  the TODO exclusions entry. A bare
  `f()` statement (immediate drop) and storing a coroutine into a
  field/element stay compile errors (`Box[Cancellable[T]]` is the
  intended owned-storage spelling; construction only today -- consuming
  through the Box is not yet wired, see TODO), and borrowing a handle (protocol-annotated
  alias, bare-`Cancellable[T]` borrow param, ternary over handles) is
  rejected -- handles are consume-only in v1. Both `asyncio.run` and
  `asyncio.create_task` enforce CPython's coroutine-only contract at
  compile time as a STATIC approximation of `iscoroutine`: the arg's
  static type must be `Cancellable[T]` (the async-def call-result
  shape) -- an already-spawned `Task`, a `Future`, or another
  nominally-typed conforming awaitable is rejected where CPython raises
  `TypeError` (`create_task`) / `ValueError` (`run`), and passing the
  async def itself (uncalled) gets a targeted "call it" hint. Being
  static, the check cannot see through laundering: a hand-written
  conforming record returned from a factory typed `-> Own[Cancellable[T]]`
  passes and runs as a task where CPython would raise -- a declared
  divergence (TPy accepts a superset; CPython fails loudly at the same
  call). Module-scope
  (top-level) coroutine bindings are not supported (rejected at the
  consumption site; bind inside a function -- see TODO).
  `asyncio.Future[T]` provides manual completion via
  `set_result` / `set_exception`; `Future[None]` works after the
  position-aware-None compiler fix lowered `None` type-args to
  `std::monostate`. `asyncio.Event` is the no-payload completion-signal
  primitive: `set` / `clear` / `is_set` / `wait` match CPython
  (`await event.wait()`); TPy additionally allows awaiting the Event
  directly (`await event`) as a shorthand. `asyncio.Lock`,
  `asyncio.Semaphore`, and `asyncio.BoundedSemaphore` are the v2 sync
  primitives (FIFO waiter queue, `acquire` / `release` / `locked`,
  usable as `async with`; `BoundedSemaphore.release` rejects an
  over-release). `asyncio.Queue[T]` is a bounded FIFO (`put` / `get`
  block on full / empty; `put_nowait` / `get_nowait` raise `QueueFull` /
  `QueueEmpty`; `qsize` / `empty` / `full` / `join` / `task_done`).
  `tpy.coro.poll_once(aw)` is a synchronous one-step driver useful for
  tests and non-asyncio contexts; sema-types `f()` (for `async def f`)
  as `Cancellable[T]` (a `@dynamic` protocol with `__poll__` + `cancel`,
  defined in `tpy.coro` alongside `Awaitable`) so the cancellable-API
  consumers (`run` / `create_task` / `wait_for`) accept coroutine calls
  directly. Awaitable-only consumers (`await`, `poll_once`, structural
  `Awaitable[T]`-typed params) still match via Cancellable's `__poll__`
  member; this mirrors how generators surface as `Iterator[T]`.
  Because `async def f` sema-types as a coroutine factory, a *reference*
  to `f` (the function as a value, not called) coerces to a
  `Callable[[...], Own[Cancellable[T]]]` (or `Fn[...]`) -- so an async
  handler can be passed as an argument, stored in a field, or returned,
  then called per item and `create_task`'d (the higher-order-async shape
  `asyncio.start_server` is built on). Codegen synthesizes a wrapper that
  calls the frame factory and adapts the result into the owning dynamic
  handle (the same `make_adapter` step the direct `create_task(f(...))`
  arg coercion emits). Free async defs are covered; a bound async *method*
  reference (`obj.m`) is the general no-method-value gap, and an `Fn`
  (template) target is blocked on a coro-frame codegen gap (see BUGS.md).
  An intermediate `Own[Cancellable[T]]` local (the factory-call binding
  `coro = factory(n)` inside an async body) works -- it is the same
  owned-erased local as a bound coroutine.
  Sema rejects user-defined `__await__` and bare-coroutine drops. See
  `docs/ASYNC_DESIGN.md` and `docs/ASYNC_PROGRESS.md`.
  At `asyncio.run` exit, remaining spawned tasks are cancelled and
  drained so wrapper-try `finally` blocks run for fire-and-forget
  tasks.
- **Working (v2 I/O reactor M1)**: the executor gains a second wake
  source -- an epoll `Reactor` -- alongside the timer heap. `EpollReactor`
  owns an epoll fd + a single-waiter `fd -> Waker` registry (one-shot
  arming); `Executor.wait_for_event` blocks in `epoll_wait` bounded by the
  nearest timer deadline (forever when only fds are pending), so a coroutine
  can wait on fd readiness without busy-looping. Low-level socket surface:
  `asyncio.get_running_loop()` returns an `EventLoop`; `loop.sock_recv(sock,
  n)` / `loop.sock_sendall(sock, data)` / `loop.sock_accept(sock)` /
  `loop.sock_connect(sock, addr)` operate on a non-blocking socket
  (`socket.socket.setblocking(False)`), driving the public `socket` methods
  and parking on `EPOLLIN` / `EPOLLOUT` via the reactor when they raise
  `BlockingIOError` (the errno-keyed `OSError` subclass `socket` raises on
  EAGAIN/EWOULDBLOCK/EINPROGRESS) -- mirroring CPython's `loop.sock_*`, which
  catch the same exception rather than reaching into socket internals.
  `sock_accept` returns the CPython-faithful `(conn, (host, port))` (the conn
  set non-blocking); `sock_connect` checks `SO_ERROR` after the non-blocking
  connect resolves. All four are sync factories returning hand-written
  `_SockRecv` / `_SockSendAll` / `_SockAccept` / `_SockConnect` awaitables (the
  `gather(...) -> Own[...]` shape), so they sidestepped the async-def
  view-param coro-frame gap (since fixed: view-family params AND locals
  are now stored owned in the frame). The `Reactor` protocol
  (`register_fd` / `unregister_fd` / `poll` / `count` / `close`) is the
  documented interface a backend implements. The reactor binds through
  `lib/tpy/_bindings/posix_epoll.py` over flat `tpy_epoll_*` wrappers in
  `runtime/cpp/src/stdlib/epoll_impl.cpp`, which holds two backends behind that
  one ABI: epoll on Linux, kqueue on macOS / *BSD (mapping EPOLLIN/EPOLLOUT to
  EVFILT_READ/WRITE) -- so the same async code runs on both. An io_uring
  backend and a protocol-level user swap-in are follow-ups. The high-level
  **streams** layer
  is built on this: `asyncio.open_connection(host, port)` returns a
  `(StreamReader, StreamWriter)` sharing the socket via `Rc[socket]`;
  `StreamReader` buffers bytes and fills via `sock_recv` (`read` /
  `readexactly` / `readline` / `readuntil(sep)` / `at_eof`,
  `IncompleteReadError` on short reads), `StreamWriter` buffers writes and flushes via `sock_sendall`
  (`write` / `drain` / `close` / `wait_closed` / `is_closing`; you must
  `drain()` before `close()` -- closing with unflushed bytes raises).
  The server side is `asyncio.start_server(handler, host, port)`: it binds
  a non-blocking listener, spawns a background accept loop, and returns a
  `Server`; each accepted connection runs `handler` (an `async def
  handler(reader, writer)`, passed directly via the async-fn->Callable
  coercion) as a task with its own `(StreamReader, StreamWriter)` pair.
  `Server` is already accepting when returned (CPython parity); `close()`
  stops accepting by cancelling the accept task (in-flight connections keep
  running), `serve_forever()` blocks until closed, and it is an async
  context manager. The bound address is read via
  `server.sockets[0].getsockname()` as in CPython -- `Server.sockets` is a small
  proxy returning a (readonly) borrow of the listener per index (enough for
  `getsockname`; mutating socket methods aren't exposed). `serve_forever()`
  serves until cancelled then re-raises `CancelledError` (a `close()` elsewhere
  cancels the accept task it awaits), matching CPython -- both lifecycle tests
  run under real CPython asyncio. One documented v1 divergence remains (no test
  exercises it): `wait_closed()` is a no-op (does not await connection drain).
  `__aenter__` returns `self` (a borrow), so `async with server as s` binds an
  alias of the server, matching CPython.
  `StreamReader.readuntil(sep)` reads through a `bytes` separator (raising
  `IncompleteReadError` on EOF first); like `readline`/`read` it enforces no
  buffer limit (`LimitOverrunError` is a deferred follow-up, TODO.md). See
  `tplib/stream_server.py` in the `tpy-examples` repo for a `start_server` echo server, and
  `async_echo_server.py` + `async_echo_client.py` for the low-level
  (manual accept loop) form.
- **Working (v1.5 M4)**: async methods on user classes. `async def m(self, ...)`
  lowers to a per-record coro struct `__coro_<Record>_<method>` with
  `__self: <Record>&` captured as the first ctor arg (parallels
  generator-method codegen). The class declares the method with the
  coro struct as its return type; the inline factory body is
  `return __coro_Class_method(*this, args)`. `await obj.method(args)`
  uses INLINE mode with emplace `(obj, args)`. `async @staticmethod`
  / `async @property` rejected at parse.
- **Working (v1.5 M5)**: `async with X as y:` for cleanup-only context
  managers. `__aenter__` / `__aexit__` are async methods; CFG synthesis
  in `_build_async_with` reuses the M3.3 try-finally-with-await
  machinery so exception propagation and `return`-walks-aexit work
  uniformly. v1.5 ships with `exc_val: None` only -- inspecting
  `Optional[BaseException]` across the suspension requires polymorphic
  exception storage (E9 / Phase 20). Multi-item `async with X, Y:` and
  direct nesting are rejected (M3.3 nested-await-in-finally limit);
  workaround: factor into separate `async def` helpers.
- **Working (v1.5 M6)**: `async for y in ait: body`. Lowers to
  `__aiter_<uid> = ait.__aiter__()` setup + a Yield for
  `await __aiter.__anext__()` wrapped in a synthesized TryRegion +
  ExceptRegion catching `StopAsyncIteration` whose handler is `break`.
  Body lives outside the TryRegion so a body-side StopAsyncIteration
  propagates rather than silently exiting the loop. Reuses M3.1's
  `__for_itr_<uid>` frame field (the `AsyncForIterSetup` synthetic
  takes an `is_async` flag selecting `.__aiter__()` vs
  `::tpy::__iter__()`). Tuple unpacking `async for (a, b) in pairs:`
  works via the parser's existing `__for_tup_<n>` rewrite. `else:`
  clause is parser-rejected (same restriction as `await` in
  `for`/`while`/`else:`). `StopAsyncIteration` shipped as a builtin
  exception in the same milestone. The frame holds the result of
  `__aiter__()` BY VALUE, so an `__aiter__` returning `self` -- the
  canonical CPython idiom -- copies the iterable into the frame and
  everything the iteration advances is lost to the caller
  (BUGS.md#aiter-self-copied-into-frame). An `async for` takes the sync
  for-each's iterable rule unchanged: it files the same iteration
  borrow through the same two registrations (`__aiter__` is user code
  and may instead return an iterator holding a pointer into the
  iterable), so a NAME, a one-hop element and a one-hop field iterable
  are checked for mutation inside the body at the same granularity, and
  a deeper lvalue chain keeps the located reject
  (BUGS.md#iter-borrow-place-needs-hops). A CALL or `@property`
  iterable takes its loan from the callee's borrow provenance on both
  routes; that provenance loan sits on the receiver root, so the
  depth carve-out a plain `def` body gets for a property-tail chain
  is NOT extended to a resumable body whose loop SUSPENDS -- there the
  iterators outlive every suspension and a property iterable past one
  hop keeps the located reject `res.for_iter_borrow_unplaceable`. A
  getter returning BY VALUE (`Own[...]`, or a value-typed return) is
  admitted at one hop like any other: the read is a call, so the frame
  materializes it into its own `__for_src_N` slot and the iterators
  walk storage the frame owns. A loop
  with no `yield` or `await` in its body lowers on the sync route
  even inside a frame, and takes the sync verdicts. A call returning an OWNED iterator compiles
  (`async for v in Countdown(3)`); what still rejects at lowering is a
  BORROW-returning call or property iterable --
  `call.ret_type.record_borrow` for a free function,
  `method.ret_type` for a method or a property getter. A property whose
  getter returns a USER-ITERATOR type rejects on the sync route too, at
  the call's own result gate (`method.fi_kind`); a container-returning
  property iterable compiles there.
- **Working (v1.5 M7)**: generic async free functions and methods.
  `async def f[T](x: T) -> T:` and `async def m[T](self, x: T) -> T:`
  on a non-generic class both work with T inferred from arguments at
  the await site. Every await on a direct call to an async def --
  free function or method -- now carries the substituted return type
  and inferred type-args mapping of the callee on the await node
  (rather than re-deriving from the callee's registry FunctionInfo).
  The sub-coro frame field is qualified with `<T_substituted>` and
  the awaited-value slot uses the substituted type. Generic
  TypeParamRef params use `::tpy::param_val_or_ref_t<T>` (ctor) and
  `::tpy::val_or_ref_t<T>` (field) so a value-typed T stores by
  value and an object-typed T stores by reference -- same
  trait-based pattern non-async generic codegen uses. Unblocks
  generic asyncio helpers (`wait_for`, `gather`, ...). Async methods
  on generic *classes* are also supported (F4 of the resumable-frame
  migration): the same template-header machinery folds in the
  enclosing record's `[T, ...]`, the inline factory qualifies the
  receiver as `Box<T>::take`, and `__self` is captured as `Box<T>&`.
  Protocol-bounded class type params (`class Box[T: Iterable[int32]]:
  async def total(self) -> int32: ...`) are supported: out-of-class
  member-def template headers spell the matching constraint
  (`template<Iterable<int32_t> T>`, not bare `typename T`).
- **Working (v1.5 M8)**: `asyncio.wait_for(coro, timeout)` -- race a
  coroutine against a steady-clock deadline. Returns the coroutine's
  value on success; on deadline expiry cancels the inner, pumps any
  `finally`-with-await cleanup, then raises `TimeoutError` (a
  built-in re-export of `tpy::TimeoutError`). Non-positive timeout
  triggers expiry on the first poll (matches CPython). Outer-cancel
  of a `wait_for` task propagates through to the inner coroutine
  (the resume-case cancel-check calls `cancel()` on the in-flight
  sub-coro before polling, so the inner observes `CancelledError`
  at its suspension point and can run `finally`-with-await cleanup).
  Implementation: hand-written `_WaitForFuture[T]` awaitable holds
  the inner as `Box[Cancellable[T]]`.
- **Working (v1.5 M8)**: `Own[Awaitable[T]]`-shaped (static-protocol)
  params on `async def` generic free functions and methods. The
  param's concrete type is deduced as an extra template arg
  `T_<pname>` with the protocol concept constraint (mirroring the
  non-async `gen_params_with_protocols` pattern); the frame field
  stores `T_<pname>` (which deduces to `U&` for an lvalue arg -- the
  coroutine borrows the operand -- or `U` for an rvalue), the ctor
  takes `T_<pname>&&` and the ctor/factory `std::forward` it in
  (perfect-forwarding, so an lvalue binds without a copy). Caller-side
  sub-coro field declarations spell the same forwarding-deduced type
  via the `T&&` model over `decltype((arg))`, rendered in the
  resumable-body context (so `self`->`__self` and `frame_slot` deref
  apply) so the await site matches the factory exactly. Awaiting such a
  coroutine works for a named iterable, a hoisted local, or a `self`
  field (all borrow); a collection-literal / comprehension argument
  passed directly at the await site is rejected cleanly (no concrete
  type / no frame storage there) -- bind it to a local first, then
  await. Coro
  structs are emitted in inline-await dependency order (each awaited
  callee before its awaiter), so awaiting a free coroutine from any
  method (concrete or templated) -- or a coroutine defined later in the
  module -- works; only a genuine cycle (mutually recursive inline
  `await`) is rejected, since a by-value sub-future can't be cyclic.
  Remains the path for user-defined awaitable protocols; asyncio's
  own cancellable APIs (`run` / `create_task` / `wait_for`) have
  since moved to `Own[Cancellable[T]]` (@dynamic) via the
  `OWNED_VALUE` `_CoroParamKind` -- different codegen path, same
  user surface.
- **Working**: Pointer-form `T | None` (pointer-repr Optional) async
  coroutine parameters. Factory signature and frame slot emit as
  `T*` / `const T*` (mirroring sync's `const T*` parameter shape);
  the caller-side `&`-lift is applied at the sub-future
  `emplace(...)` call site by the same coercion sync uses. Same
  `nullptr`-doubles-as-uninitialized-and-None convention as the
  matching hoisted-locals row in the generator section above.
- **Working**: the async return convention mirrors sync's, per the
  declared return (the async borrow-return ABI):
  - `-> C` (bare reference type) is a BORROW contract: the Poll payload
    is a pointer (`Poll<C*>`), so a direct `await` binds an ALIAS of the
    source -- `r = await s.get()` then `r.n = 9` is visible through `s`
    (CPython aliasing). Sources are gated exactly like sync borrow
    returns (params / `self` / globals / field chains; a local,
    temporary, or awaited owned result is rejected with the `Own[C]`
    fix-hint). `async def __aenter__(self) -> "Server": return self`
    works, and `async with server as s:` binds `s` as an alias of the
    manager. A borrow return under a SUSPENDING finally stores the
    pointer in the pending-return slot, so the finally's mutations are
    visible through the result (unlike the storage-form eager-copy
    divergence tracked in BUGS.md).
  - `-> Own[C]` is the ownership contract: the value moves out of the
    frame (the sync last-use discipline; `@nocopy` results work), and
    the coro flows through every owned consumer -- `asyncio.run`,
    `create_task` + `await task`, `wait_for` (Own is normalized out of
    the `Cancellable[T]` type arg, so `Own[Cancellable[T]]` slots
    unify at `T=C`). Under a non-suspending `finally` the
    deferred-capture path keeps the finally's reads/mutations visible.
  - Generic `-> T` splits per instantiation via `val_or_ptr_t<T>`
    (value -> by value, object -> pointer borrow), mirroring sync's
    `val_or_ref_t`: `await identity(node)` aliases, `await
    identity("s")` copies the value.
  - Borrow-returning coroutines are DIRECT-AWAIT-ONLY: passing one to
    `asyncio.run` / `create_task` / `wait_for` / any
    `Own[Cancellable[T]]` or `Awaitable`-protocol slot is a compile
    error (task results live in owned slots that outlive the borrow's
    source) naming the escape hatch -- declare `-> Own[C]` and return
    an owned value (`tpy.copy(...)` a borrowed source such as `self`).
  - Pointer-variant Union and recursive-union-wrapper returns still
    keep storage form (consumer-side work pending -- see BUGS.md).
- **Working (v1.5 M9)**: `asyncio.gather(*tasks)` (variadic-positional)
  and `asyncio.gather_list(tasks)` (list-shaped) -- two homogeneous
  entrypoints over one `_GatherFuture[T]` engine. Both run N already-
  spawned tasks concurrently and harvest their results in input order;
  all tasks must share return type `T`. `gather(*tasks)` is a sync
  factory returning the `_GatherFuture[T]` awaitable directly (same
  pattern as `await create_task(coro)`); `gather_list(tasks)` is a thin
  `async def` wrapper for callers who already have a list. Each Task is
  Rc-cloned into the future's owned list so the awaitable is self-
  contained across the await point. On the first sub-task exception
  (or outer cancel) the future enters cleanup mode, calls `cancel()`
  on the still-pending siblings, waits for them to settle, then re-
  raises the first exception observed. Both shapes are TPy-only --
  CPython's `gather` is heterogeneous-tuple-shaped (`gather(c1, c2, c3)
  -> tuple[T1, T2, T3]`); that form needs variadic generics plus async-def
  `*args` support (currently rejected at sema, see BUGS.md) and remains
  deferred (TODO.md). Implementation:
  `lib/tpy/asyncio/__init__.py:_GatherFuture` (Rc-clones tasks,
  parallel completion-order lists, O(n^2) reorder at end);
  `lib/tpy/asyncio/_executor.py` grows `Task[T].clone()`. Outer-
  cancel observation in still-running sub-tasks is prompt (since the
  cancel-runnable-mark hook landed): `create_task` stamps a `Waker`
  for the slot onto the Task at spawn time, and `Task.cancel()`
  fires `waker.wake()` so the slot is scheduled for an immediate
  poll. The in-flight frame observes its `__cancel_pending` on the
  next executor cycle instead of waiting on a timer / IO wake.
- **Working**: `asyncio.gather_list_settled(tasks) -> list[Settled[T]]`
  -- the return-exceptions variant of `gather` (CPython's
  `gather(*coros, return_exceptions=True)`). A sub-task failure does
  NOT cancel its siblings; each runs to completion and contributes a
  `Settled[T]` entry with either `value: Box[T] | None` or
  `exception: Box[Throwable] | None` populated (input order). A
  sub-task cancelled independently (via its own handle) is collected
  as a `Settled` entry with its `CancelledError`, like any other
  failure. Cancelling the gather caller itself is different: it
  propagates cancel into the sub-tasks (cleanup) and then re-raises
  `CancelledError` to the caller -- it is NOT swallowed into the
  result list (matching CPython: cancelling `gather()` cancels it).
  Diverges from CPython's `list[T | BaseException]` because TPy lowers
  container-element unions to a value-variant and the exception root
  is a polymorphic owner (slicing risk); the `Settled[T]` record
  gives clean field-based discrimination instead. Recover the
  concrete exception type by re-raising:
  ```python
  for entry in results:
      if entry.exception is not None:
          try:
              raise entry.exception
          except ValueError as v:
              handle(v)
      elif entry.value is not None:
          handle_ok(entry.value.get())
  ```
  Implementation: `lib/tpy/asyncio/__init__.py:_GatherSettledFuture`
  (arrival-order parallel result/exc arrays, pop-based input-order
  assembly).
- **Open (v1.5+)**: multi-item / nested `async with`, await inside a
  `for`/`while` `else:` clause, `async for ... else:`, executor slot
  reuse, reporting when bounded cancellation drain leaves tasks
  pending, and full `async with` with `__aexit__` exc_val inspection
  (E9 / Phase 20).

---

## Generators

- **Working**: Generator expressions `(expr for x in iterable if cond)` → `tpy::make_generator<T>(lambda)` wrapper satisfying `Iterable[T]`. Sources: `range()` (a counter lambda), any lvalue container (name, field, borrow-returning call -- aliased by reference), and any rvalue iterable -- a container literal, a dict view (`d.values()` / `.keys()` / `.items()`), a native combinator (`zip` / `enumerate` / `reversed` / `filter` / `map`), a generator call, an `Own[container]` call -- which the genexpr owns (evaluated once at construction, like CPython's `iter()`, built in place inside the closure and pulled lazily). A movable closure may be moved by an owning consumer -- another lazy combinator, `enumerate(x * 2 for x in xs)` -- before its first pull (the seed is lazy, so that is safe); after the first pull nothing moves it. A NON-movable closure (its source is an owning combinator -- `zip` / `enumerate` / `filter` / `map` / `reversed` over a non-lvalue argument such as a generator call -- whose move ctor is deleted) works at every eager consumer (`sum`, `list`, a for-head, a protocol slot) but is rejected when moved into another lazy combinator (`genexpr.nonmovable_into_owning`, e.g. `enumerate(a + b for a, b in zip(gen(), gen()))`), until producers become movable while unstarted (TODO.md). Same source classification as the comprehensions. Not yet: a nested genexpr as the source (BUGS.md#genexpr-nested-source), an `Iterator[Own[T]]` source (rejected: the yielded borrow would point into the consumed slot), a source call whose argument needs a hoisted temp (BUGS.md#genexpr-source-needs-arg-temp), a local bound to a genexpr. Filter clauses, tuple unpacking, outer local capture (frame members inside a generator/async body included). See [COMPREHENSION_DESIGN.md](COMPREHENSION_DESIGN.md#generator-expressions).
- **Working**: Generator functions with `yield` -> `Iterator[T]`. Every generator compiles to a stack-allocated state-machine struct (`__gen_<name>`) exposing `__next__() -> std::expected<T, StopIteration>`, emitted by the shared **resumable-frame** `while/switch` emitter that also powers `async def` (see [GENERATOR_RESUMABLE_MIGRATION_PLAN.md](GENERATOR_RESUMABLE_MIGRATION_PLAN.md)) -- there is no other generator emitter: the legacy goto-dispatch path and the single-yield `make_generator` lambda peephole are both gone. The resumable path covers `if`/`while`/`for` (including `for`/`while`-`else` and the range/begin_end for-loop peepholes), `try`/`except`/`finally` -- including `yield` inside `finally`, and `return` inside `finally` correctly suppressing a pending exception (Python semantics) -- and `with` (the `__exit__` on the exception edge receives the live exception, not the normal-exit `nullptr`). Generic multi-yield generators with explicit `[T]` type params are supported -- the templated struct + `__next__` + factory emit inline in the header (mirroring generic `async def`). Tuple-unpack for-loops (`for a, b in ...`) are supported, including across a suspension: reference (non-value) elements alias the live container element via pointer-form (`T*`), so mutating an unpacked record propagates to the source -- matching CPython; value elements copy. Nested suspending `finally` works (the inner `AsyncFinallyExit` forwards any pending exception or pending return into the outer's parking slots). A `yield` inside a `match` works (the CFG decomposes the `match`: arm bodies become states while the type-aware dispatch is reused, and pattern bindings are frame fields). Protocol-typed params (`def gen(it: Iterable[T])`) are supported: the for-loop iterator frame field is typed against the deduced template arg `T_<pname>` (the captured param) rather than the un-instantiable concept, and the generator borrows the iterable; aliasing the protocol param into a local once (`xs = it`) and iterating that across a suspension is also supported -- the alias forwards to the captured param (no frame field), reusing its `T_<pname>` deduction. Reassigning the alias is rejected cleanly. An **owned** static-protocol param (`def gen(items: Own[Iterable[T]])`, and the `async def` twin) is supported on the same footing: the frame field is the deduced `T_items items;` and the call site renders `std::move(...)`, so the frame OWNS the conformer instead of borrowing it. A **`@dynamic`** protocol param is supported on a generator frame too, captured as the erased base reference (`Src&`, or `const Src&` for `readonly[Src]`) so the caller sees mutations through it; the `async def` twin is not yet supported (the frame would borrow the call site's `RefAdapter` temp, which a task outlives) and is refused with a construct-not-supported diagnostic. What is not yet supported is forwarding an argument into a sub-future at an `Own[<static protocol>]` slot of an `await` -- the sub-future's field is deduced from the argument expression, which borrows where the slot says it owns, so that position is refused with a construct-not-supported diagnostic (see BUGS.md). For-loops with yields are lowered to while-loops with iterator state as struct fields (counter for range, begin/end for containers, `std::expected` for `__next__()` protocol, plus a `__for_src` field holding a temporary source -- a delegated sub-generator struct or a temporary container/iterable -- so it lives across suspensions). All generators are stack-allocated (zero heap allocation, `@noalloc`-compatible). Lifetime tracking: generators with non-value or explicit-view (`StrView`/`BytesView`/`Span`) params set `return_borrows_from` at registration time (signature-derived, so callers analyzed before the generator's body still see the facts), so that auto-move gating, mutation-during-iteration, and transitive borrow propagation work correctly. `str`/`bytes` params are captured **owned** in the frame (a `std::string` / `tpy::bytes` copy at construction -- `is_owned_in_coro_frame`), so they do NOT borrow the argument and passing a temporary is safe across iteration (previously stored as a dangling `string_view`/`BytesView` -- a silent UAF). An explicit **view** param -- `Span[T]`, `Span[readonly[T]]`, `readonly[Span[T]]`, `StrView`, `BytesView`, a `*args` pack -- is the opposite: it stays borrow form, the frame field being the same view the sync signature spells (ctor-moved, no copy of the buffer), so a write through it lands in the caller's storage and a source mutated between two pulls is seen on the next one. The `async def` twin captures identically. **A frame never receives a temporary.** Whatever a frame does with an argument -- keep a `T&`, keep a view whose value is a borrow, copy it into an owned field -- it does it after the full expression that built the argument has ended, so every argument to a generator/coroutine factory that materializes a temporary (a container literal, a call or method-call rvalue, a binop, an f-string, a `str`/`bytes` literal) is hoisted into a named local of the calling scope, and the hoisted local is then passed through the same per-param coercion the argument already went through, so an owning sink still moves, a borrowing sink still views and a value sink still copies. The rule needs no taxonomy of which C++ shapes alias their source; three slots are excluded because nothing can be owed at them: a PRIMITIVE param (register-sized, trivially copyable, nothing to borrow), a `Ptr[T]` param (the frame copies a pointer VALUE, and naming it pins nothing its pointee did not already outlive) and an `Own[T]` param (the frame takes ownership -- the temporary moves in, which is what an unnamed rvalue is for). Inside a RESUMABLE body the calling scope is a `case` block of the enclosing frame, which every handle built there outlives, so the named local is a FIELD of that enclosing frame instead -- for an inline `await`, a `for` head, a handle bound to a local and for the escaping `asyncio.create_task(f(...))` form alike. The RECEIVER of a factory METHOD rides the same rule -- a frame keeps it as `<Class>&` for the handle's whole life -- so an rvalue receiver inside a resumable body (`for v in Summer(200).pair(xs)`, `asyncio.create_task(Obj(1).run(...))`) takes a field of the enclosing frame too, for the constructor-rvalue slice the sync receiver lift covers and, at a coroutine factory, for any rvalue; an rvalue receiver at a local bind or an inline `await` stays a loud reject asking for a named receiver, and so does a receiver that is neither a stable lvalue nor a seatable temporary -- a FIELD of a temporary (`create_task(make_pair(a, b).left.add(1))`), whose owner dies at the end of the statement while the frame keeps a reference into it. The frame field is one per call SITE, so a site inside a LOOP shares it across iterations. That is sound exactly where no handle built there is still alive when the site re-executes: a `for` head (the loop statement drains and destroys its iterator), an inline `await`, a bare discard, and a handle bound to a name that is only ever awaited or iterated (one name is one slot, so the next iteration's rebind destroys the previous handle). A site inside a loop whose handle is handed on instead -- `asyncio.create_task(f(<temp>))`, a bound handle appended to a list -- is a loud reject naming the temporary, since every such handle would borrow the one field and see the last iteration's value; binding the temporary to a name inside the loop does not help (a local of a resumable body is a frame field too), so the remedy the message gives is storage that outlives the loop, or an `Own[...]` parameter so the callee owns its copy. Outside a loop the site runs once and the seat stands. Positions that hoist does not admit (a ternary arm, a comprehension body) keep the calling block's local, which is enough there because the handle cannot outlive the full expression either. In a position with no statement to hoist into, the call is rejected with a located message rather than emitted over a temporary. A view LOCAL inside a frame body is still rejected (it would need owning frame-local storage the view has no form for), and `SpanIter[T]` / the dict views (`d.keys()`, `.values()`, `.items()`) pass the frame param gate but are refused at the CALL SITE by the argument shape, so neither is reachable as a frame param yet. A generator method's receiver borrow is not yet represented (see BUGS.md).
- **Working**: Generator methods -- `yield` inside class methods (e.g. `__iter__`, or any method returning `Iterator[T]`). The method becomes a factory for a generator struct holding a `__self` reference field. `self.field` accesses in the generator body resolve through the reference. Rejected in `__init__`, `__del__`, and `@staticmethod`.
- **Working**: Reference preservation through generic generators. **A generator lends what it yields when every yield source outlives a suspension; otherwise it hands out a value.** At a GENERIC `Iterator[T]` that rule picks the slot per DEFINITION and the form per INSTANTIATION: the frame spells `::tpy::yield_slot_t<T>` (a `val_or_ref<T>` -- a value `T` by value, a reference `T` by pointer, and idempotent so a call site that already substituted `val_or_ref<Point>` is not wrapped twice), so the generic generator lends exactly where its monomorphic twin does and a consumer's `p.x += 100` reaches the source container. Lending sources: a parameter name, field or subscript, a `self` field or property read, a loop variable over a parameter container or over a frame-resident local container, a frame-resident local, and a `readonly[T]` element (the const rides inside the slot, `val_or_ref<const T>`); the block the yield sits in does not matter (a `with` body, a `try`/`finally` body and a `match` arm all lend). The verdict is ONE per generator -- one slot type serves the whole frame -- so a single non-lending source demotes every yield in the body (BUGS.md#generic-yield-mixed-source-demotes-frame), and it is settled after every body in the module, so it does not depend on whether a callee is defined above or below the generator. A generator with any non-lending source keeps the VALUE slot and copies the element out -- a temporary, a loop variable over an `Iterator[T]` / genexpr source (a borrow the next step overwrites), or a call whose signature does not say its return borrows from an operand, which includes every erased `Fn` result (`yield fn(x)`, BUGS.md#generic-yield-fn-result-copies; a NAMED callee whose `U` sema infers as `Ref[X]` does lend). Enables user-defined `my_map`, `my_enumerate` with zero-copy reference semantics, and `itertools.islice` / `takewhile` / `dropwhile` / `filterfalse` / `repeat` lend their elements (`cycle` lends on the FIRST pass only -- it buffers the elements it has seen, and the buffer holds copies: BUGS.md#itertools-cycle-later-passes-copy). Composition between user generators and builtins (`enumerate(my_map(...))`, `my_enumerate(my_map(...))`) works correctly.
- **Working**: Pointer-form frame slots for reference-type locals/params that survive a yield. Three cases:
  - `T | None` (pointer-repr Optional) locals emit as `T* = nullptr` in the generator struct. `nullptr` doubles as "uninitialized" and "None"; reads and field access go through the same path as a sync pointer-local. Avoids a `std::optional<std::optional<T>>` double-wrap that breaks for `@nocopy T` and would otherwise be a hidden value-copy across the yield boundary.
  - `T | None` (pointer-repr Optional) parameters emit as `T*` / `const T*` in the generator factory signature and frame slot, mirroring sync's `const T*` parameter shape. The caller-side `&`-lift is applied automatically by the call-site coercion machinery.
  - `for x in items` where `items` is a `NativeIterable` container (list, dict, set, span, array, str) of non-value elements emits the loop variable as `T*` aliasing the container element (assigned via `&(*iter)++`). Preserves CPython aliasing semantics: `for it in items: prev = it; yield ...` keeps `prev` referencing the actual container element across iterations, not a copy that gets overwritten. Readonly element types fall back to value-copy storage (until `const T*` slot support lands).
  - Non-value union (`A | B`) parameters emit as the pointer-variant borrow form (`::tpy::Union<A*, B*>`, `::tpy::Union<const A*, const B*>` when the body does not mutate the param) in the generator/`async def` factory signature and frame slot -- the same shape ordinary functions and plain locals use. The call site and the async await/emplace path build the matching variant automatically. (Recursive-union-alias members use the value-wrapper form instead, via `needs_wrapper()`.)
- **Working**: `tpy::frame_slot<T>` storage for remaining non-value, non-pointer-form generator/coroutine frame locals (`list[T]`, `dict[K,V]`, `set[T]`, `str`, bare unions, bare records). A union member + runtime alive flag; T is not constructed until source-level first write. Codegen emits `.emplace(value)` for first-init and rebind; reads go through `operator*` / `operator->`. Replaces the historical outer `std::optional<T>` wrap which leaked `(*name)` deref boilerplate to every consumer site and produced silent memory corruption on `name = {}` brace-init (e.g. `history: list[T] = []` setting outer to nullopt instead of engaged-with-empty-container). Future evolution: state-aware destruction (codegen-driven liveness analysis per suspension point) would let the alive bool drop out; not currently planned.
- **Working**: yielding a borrowed **reference** element from a resumable frame -- a record and a container (`list`, `dict`, `set`, `bytearray`) alike. The slot is the borrow form `val_or_ref<T>` and the frame returns the element's lvalue (`return (*s);`), so the consumer's loop variable aliases the source and mutation through it reaches the source, matching CPython. One admission covers the whole reference axis, so the accepted source shapes are the same on both halves: a frame slot, a pointer-form loop variable (`for s in xs: yield s`, including over an `*args` pack or an `Iterable[T]` param), an alias local (`s = xs[0]; yield s`), a parameter name, `self.<field>`, and a ternary of two frame slots -- in a free generator, a generator method, and inside a `with`, `try` or `finally` body. A `readonly[T]` element yields a CONST borrow: the slot is `val_or_ref<const T>` and the consumer's loop variable binds `const T&`, so a pull hands out a pointer here too (it used to be a value slot, copying a whole `std::vector` out of the source per pull and hiding a mutation made between pulls). On the consuming side, a `for` over an `Iterator[T]` PARAM inside a resumable frame spells its `__next__`-result frame field from that same slot, so a reference element works there as it does over a temporary iterator source. **Not yet supported**: a SUBSCRIPT source (`yield xs[0]`) or a borrow-returning CALL source at a CONCRETE reference yield slot, on either half; both are refused with a construct-not-supported diagnostic (see BUGS.md#subscript-elem-record). The open-`T` slot is not affected -- `def ends[T](items: list[T]): yield items[0]` lends the element like the parameter name does.
- **Working**: yielding an OWNED value from a resumable frame at `Iterator[Own[T]]` -- a record and a container alike. Three source shapes: a constructor call (`yield Node(i)`), an explicit `copy(x)` of a frame-resident name (a pointer-form loop variable included, where the copy carries the deref), and any call whose callee DECLARES the transfer (`def mk(v) -> Own[Node]`, then `yield mk(v)`). Admission keys on the callee's declared return, not on the value category: a call returning a BORROW is an lvalue into someone else's storage, so it is refused here rather than silently copied (the `Own`-slot warning above describes the copy; at this slot the reject is what keeps it unobservable). A method call returning `Own[T]` is not yet admitted.
- **Restriction**: the non-copyable rejection (`@nocopy`, a record with `__del__`, or one transitively containing either) applies only to a **value-ABI** yield -- a value-type element copied into the slot. Under `Iterator[T]` for a non-value `T` the slot is the `val_or_ref<T>` borrow form (no copy) and under `Iterator[Own[T]]` it moves, so a non-copyable reference element is accepted. A bare reference yield must root in frame-held storage; a fresh `yield Node(1)` under `Iterator[T]` is rejected and pointed at `Iterator[Own[T]]`. The same rooting check applies per tuple member: a fresh non-value member (`yield (i, Box(i))` under `Iterator[tuple[int, Box]]`) is rejected and pointed at `Iterator[tuple[int, Own[Box]]]`, including the bound form (`t = (i, Box(i)); yield t`). A *durable* member shares: `t = (i, b); yield t` for a param `b` yields a pointer borrow of `b` (the bound local is `std::tuple<int, Box*>`), so consumer mutation reaches `b`.
- **Working**: generator delegation via `for x in sub_gen(...): yield x` (the `yield from` substitute). A temporary for-loop source is evaluated exactly once -- the consumer stores it in a `__for_src` frame field typed as the callee's generator struct (struct emission is topologically ordered so an embedded callee's definition precedes its consumer's). Same applies to temporary container/user-iterable sources (`for x in make():` -- the begin_end and universal `__iter__` strategies store the temp before borrowing it), while stable lvalue sources (names, field paths like `self.items`, container subscripts) are *borrowed*, so element mutations through the loop var reach the source (CPython aliasing). Non-value elements of a delegated `Iterator[T]` source alias the producer's live yield slot (pointer-form loop var). A temporary source is evaluated *lazily* on the first pull (matching CPython's deferred body execution), not at generator construction. **Restriction -- no recursion**: recursive delegation is rejected at compile time because the embedded frame type would be infinite-size; this includes *bounded* recursion that CPython supports, e.g. a tree walk `for x in walk(child): yield x` inside `walk` -- materialize with `list(...)` to break the recursion (this covers a cycle that spans modules too -- two modules delegating to each other get the same diagnostic, as do two single-yield generators delegating to each other). The callee must be a direct *call* to a generator -- a free function (`from m import g`, `import m; m.g()`), a method, a generic callee via its inferred type args, or a method on a generic owner -- and it may live in any module: the field is spelled through the callee's own C++ namespace, the same naming the `await` path uses for a sub-coroutine. An `Iterator`-typed local or a call through a variable is not a direct call and is rejected; so is a callee with protocol-typed parameters. An imported single-yield callee delegates like any other: every generator is a named frame struct, spelled through its module's namespace.
- **Open**: `yield from`, `send()`, `throw()`, `close()`

---

## Interactive REPL

- **Working**: bare `tpy` (or `tpyc -i` / `tpy -i`) launches an interactive session
- Supports function and class definitions that persist across inputs
- Expressions are evaluated and printed automatically
- Multi-line input with automatic continuation detection (`if`/`else`/`elif`/`except`/`finally`)
- Paste mode (`.paste` or `.p`) for multi-line blocks
- Configurable C++ compiler via `--cxx`:
  - `clang-repl` -- incremental JIT, fastest for iteration (~20-80ms per expression); auto-restarts after JIT crashes. Explicit opt-in only; modules needing runtime impls or managed third-party libs (`os`, `re`, `ssl`, `datetime`, ...) are unsupported in this backend (see BUGS.md)
  - `clang` -- compile-and-run via clang++ with PCH caching
  - `gcc` -- compile-and-run via g++ with PCH caching
  - `zig` -- compile-and-run via zig c++ (from system or bundled ziglang package)
  - `auto` (default) -- picks the best available compile-and-run backend (g++ > clang++ > zig); never auto-selects clang-repl

---

## Lambda / Closures

- **Working**: `Fn[[A, B], R]` type -- zero-cost callable parameter (C++ template + `requires` constraint). Valid in function/method parameter position only.
  ```python
  from tpy import Fn, int32
  def apply(f: Fn[[int32], int32], x: int32) -> int32:
      return f(x)
  apply(lambda x: x + 1, 42)  # lambda inlined, zero overhead
  ```
- **Working**: Lambda expressions `lambda x: expr` -- parameter types inferred from `Fn` or `Callable` context via bidirectional inference. Non-capturing lambdas generate `[]`, capturing lambdas generate explicit capture lists (`[&var]` for `Fn`, `[var]` by value for `Callable`).
- **Working**: Callable container bindings supply lambda context through consuming
  `Own[...]` parameters: `callbacks.append(lambda x: x + 1)`, `insert`, and
  `commands.setdefault(key, lambda x: x + 1)`. Callable elements can also be
  replaced in lists, arrays and dictionaries. Optional callable slots accept
  callbacks and `None`; tuple elements and list/dict comprehensions propagate
  their annotated callable context. These use the existing `Callable` capture
  and conversion rules. Generic context can supply an open parameter type, but
  lambdas with open type parameters still encounter the existing THIR gate.
  Captured loop variables retain the existing by-value snapshot behavior;
  see BUGS.md's "Loop-variable capture into an escaping closure" entry for
  the known difference from CPython's shared closure cell.
- **Working**: Capturing lambdas -- `Fn` captures by reference (non-escaping, template-based), `Callable` captures by value (safe for escaping via `std::function`). Exception: a captured `self` is always the `this` pointer (alias, never a copy) -- an escaping self-capturing `Callable` is not tied to the receiver's lifetime (see BUGS.md), and its frame classifies non-Send. Inside a generator / `async def`, a captured name the resumable frame holds as a MEMBER has no variable form to name, so the entry NAMES it in a C++ init-capture -- but the mode is the sync one, so the same source gets the same binding whether or not the enclosing body suspends: `Callable` snapshots the member (`[n = n]`, which is what lets such a lambda be handed to a callee that stores it) and `Fn` binds a reference to it (`[&xs = xs]`, so a mutation through the closure reaches the caller's object exactly as in the sync twin). The receiver is a handle either way (`[&__self = __self]`, the frame's own `self` reference). Two members have no entry: one whose read spelling is not the bare name (a reference-type frame LOCAL lives in a `tpy::frame_slot<T>` and reads `(*ys)`), and, at the by-value mode only, one C++ cannot copy (`Own[T]` peeled to its payload first) -- both reject rather than render.
- **Working**: `Fn` in method parameters -- generates per-method template with `requires` constraint.
- **Working**: `Callable[[A, B], R]` type -- type-erased callable (`std::function`). Valid in all positions: the callable itself binds as `const std::function<...>&` (params), or by value in fields/returns/containers/locals. The `std::function`'s own param types (`A`, `B`) spell mutable by default for non-value types -- see the callable-value-call bullet above for the mutability contract. `Callable | None` maps to `std::optional<std::function<...>>` with `is not None` narrowing for both fields and parameters; a lambda literal, a function by name, or `None` may be passed directly to a `Callable[...] | None` parameter (the optional wrapper is unwrapped to recover the callable shape for arg inference, then the value coerces back into the optional slot).
  ```python
  from typing import Callable
  from tpy import int32
  def make_adder(n: int32) -> Callable[[int32], int32]:
      return lambda x: x + n  # captures n by value
  class Button:
      on_click: Callable[[int32], None]
  def maybe_apply(f: Callable[[int32], int32] | None, x: int32) -> int32:
      if f is not None:
          return f(x)  # narrowed to Callable, emits .value()()
      return x
  ```
- **Working**: `Callable` -> `Fn` implicit coercion -- `Callable`-typed variables can be passed where `Fn` parameters are expected. In C++, `std::function` satisfies template `requires` clauses. Works with user-defined functions, builtins (`map`, `filter`), and overload resolution. Signature compatibility uses standard function-type variance: params are contravariant (a callback accepting `int32 | None` satisfies `Fn[[int32], R]`; one accepting only `int32` does NOT satisfy `Fn[[int32 | None], R]`), returns covariant; a void contract accepts any return. A class with multiple `__call__` overloads satisfies an `Fn`/`Callable` contract when any overload's signature does (first declared match wins); no match is a located error listing the candidates.
- **Working**: Calls through callable VALUES (Fn/Callable params, locals, fields) run the same arg pipeline as direct calls -- coercions are applied (a plain `int` local narrows into an `int32` callback param), `Own[T]` consumption and readonly checks run, and the callee is treated as an opaque, potentially-mutating function: reference args bound to non-readonly callable params are conservatively marked mutated (the borrowed-container warning fires like a direct call; a method calling a mutating `Callable` field is inferred non-const). Non-value callable params spell mutable in C++ (`std::function<void(std::vector<int32_t>&)>`), so callbacks that mutate compile and the mutation is caller-visible (CPython semantics). The explicit non-mutating contract is `readonly[...]` inside the param list -- `Callable[[readonly[list[int32]]], None]` keeps the const spelling, accepts borrowed containers without warnings, and rejects mutating callbacks at the assignment boundary. Exception: bare generic slots (`Fn[[T], R]`) are not conservatively marked (a value-typed instantiation cannot mutate; the generic combinator corpus keeps const params).
- **Working**: Indexed callable fields such as `app.commands[name](arg)` and
  `app.callbacks[i](arg)` use ordinary indexing and callable argument checks.
  Chained fields, generic record fields, readonly receivers and void callbacks
  are supported. The indexed receiver is evaluated once. Temporary arguments
  retain the existing ordering limitation in
  `BUGS.md#subexpression-right-to-left-eval`; lambda bodies also retain the
  temporary-storage limitation in `BUGS.md#lambda-body-reference-argument-temp`.
  Invalid indexing on a plain callable is diagnosed instead of being ignored; explicit
  generic method calls keep their existing meaning. Indexed callbacks, direct
  callable fields and ordinary methods invalidate length-based bounds proofs
  for reference arguments consistently with free calls. A record-returning call
  used directly as the field receiver still encounters the shared container
  lowering limitation in `BUGS.md#call-rooted-container-field-index`. Generic
  methods passing an open-type reference argument retain the existing constness
  limitation in `BUGS.md#generic-method-callable-param-forced-const`.
- **Working**: Named function references as callable values -- pass functions by name to `Fn`/`Callable` params or assign to `Callable` locals/fields. Overload resolution selects the matching signature. Cross-module functions use qualified C++ names. Generic functions are supported -- type parameters are inferred from the hint signature (e.g. `identity[T]` with `Fn[[int32], int32]` infers `T=int32`); bounded type params are validated. **Limitation**: generic function refs with `str` type args are rejected because `str` uses `string_view` for params while generic functions use `const string&` via `param_val_or_ref_t<T>` -- use a lambda instead.
  ```python
  def double(x: int32) -> int32:
      return x * 2
  apply(double, 42)  # pass by name, no lambda wrapper needed
  f: Callable[[int32], int32] = double  # assign to Callable local
  ```
- **Working**: Nested `def` with captures and `nonlocal` keyword. Nested functions compile to
  C++ lambdas with auto-inferred captures. Non-escaping closures capture by reference;
  escaping closures (returned as `Callable`, passed to `Callable` params, stored in fields
  or containers) use per-capture mode: non-value parameters capture by reference (caller's
  object outlives the closure, Python-like semantics), `Own[T]` parameters and last-use locals
  are moved, remaining non-value locals are copied (with a warning suggesting `copy()` or a
  class). `nonlocal` enables mutable captures (non-escaping only). In a method, a nested def
  (or lambda) can capture `self`: the capture is `this` (alias semantics -- mutations of
  `self` fields from the closure are visible on the caller's object, matching CPython), and
  closure mutations propagate to the method's const/mutation inference (a self-mutating
  closure de-consts the method, including transitively via `self.helper()` calls; a read-only
  closure keeps the method `const`). A self-capturing closure's frame classifies non-Send
  (the capture is an alias), and `nonlocal self` is rejected -- the receiver cannot be
  rebound. An escaping closure capturing `self` holds the raw `this` -- receiver lifetime
  is not yet tied to the closure (see BUGS.md). Restrictions: no decorators, no type
  parameters, no nested-in-nested, no recursive nested defs, no nested async defs, no
  nested generators (a nested def containing `yield` is rejected; a generator containing
  a nested def works). A nested `def` binds its name for the WHOLE enclosing scope, the
  way Python does, even when the name also exists at module level: a read before the `def`
  is an error naming the shadowing (CPython raises `UnboundLocalError` there) rather than
  a silent call of the module function, a read inside the nested body is the recursion
  error, and a sibling nested def declared ABOVE the shadowing one cannot call it (a C++
  lambda names only what is declared before it). A binding of the nested def's own -- a
  param, a body local -- owns the name inside it as usual; a sub-scope binding (lambda
  param, comprehension variable, live `except ... as`) owns it only within that sub-scope.
  A `def` nested inside an `if`/`for`/`while`/`try`/`with`/`match` body is READABLE ONLY
  INSIDE THAT BLOCK: its callable is declared in the block, so a read after the block is a
  located error naming the block, whatever the paths through it bound -- both arms of an
  `if`, a `with` body included -- and never a silent call of the module name. Reads inside
  the block, and a `def` written above the block, compile as usual. A scope-level `def` of
  the name SUPERSEDES a block entry from either side, so a read it covers is legal: written
  above the block it still answers reads after it, and written below a block-bound twin it
  takes over from there -- only a read whose nearest binding is the block one is the error
  (`nested_def/name_collides_with_function` pins both directions). This is a shipped
  limitation, not Python's rule: lifting it means giving the callable a slot at function
  scope (`BUGS.md#nested-def-block-scoped-lambda-read-after-block`). The block rule does
  NOT reach the module-level twin: a `def` written inside a module-level `if` is a module
  function rather than a nested one, so the guard never sees it -- tpyc accepts the program
  and the C++ build fails naming a symbol the user never wrote
  (`BUGS.md#module-level-block-scoped-def-not-declared`, the same defect as
  `BUGS.md#conditional-def-lambda-block-scoped`).
  An escaping closure MOVES a captured local only when nothing uses it
  afterwards; a read inside a sibling nested `def` (written after it, or written
  before it and referenced after), a `nonlocal` declaration of the name in such a
  sibling, and a read inside a lambda built after it all count as a later use, so
  the capture copies and warns instead. The rule is deliberately conservative: a
  sibling that merely mentions the local and is never called counts too.
  Escaping closures that
  capture `str` parameters are rejected (string_view would dangle). Returning a
  view of a CAPTURED enclosing name is rejected too -- `def inner() -> StrView:
  return o`, where `o` is the enclosing body's view local (or its `str`
  parameter) -- although the enclosing body's own `return o` is accepted and
  sound. That position dependence is not the one-storage rule: the
  dangling-view check asks whether the name is a parameter or a global of the
  function it is analyzing, and inside a nested def that is the nested def, so
  every captured name fails closed (`BUGS.md#nested-def-captured-view-return-rejected`). Inside an `async def` or
  a generator, a nested def is emitted as a member function of the resumable frame: captures
  reach the frame-field locals directly (mutation via `nonlocal` matches CPython) and the
  function stays callable across `await`/`yield`; a generator containing a nested def always
  takes the resumable path. Such a nested def cannot escape or be passed as a value (it lives
  on the coroutine frame) -- returning it or passing it to a `Callable`/`Fn` param is a
  compile error; call it directly. Because an escaping closure
  freezes its captures by value (CPython late-binds via a cell), the compiler warns when a
  captured local is reassigned after the closure is created -- the divergent case; bind a fresh
  `snap = x` (or `copy()`) to acknowledge the snapshot. In-place mutation of a captured object
  is not yet warned (tracked in `BUGS.md`).
  ```python
  def make_adder(n: int32) -> Callable[[int32], int32]:
      def add(x: int32) -> int32:
          return x + n  # captures 'n' by value (escaping)
      return add

  def process(items: list[int32]) -> int32:
      total: int32 = 0
      def accumulate(x: int32) -> None:
          nonlocal total
          total += x  # mutable capture by reference
      for item in items:
          accumulate(item)
      return total
  ```
- **Working**: Callable classes (`__call__`) -- classes with `__call__` method compile to C++ structs with `operator()`. Instances can be called with `obj(args)` syntax and passed to `Fn`/`Callable` parameters (signature validated at compile time). Supports `@readonly`, mutable state, and recursive `self(args)`.
  ```python
  class Adder:
      offset: int32
      def __init__(self, offset: int32):
          self.offset = offset
      def __call__(self, x: int32) -> int32:
          return x + self.offset
  def apply(f: Fn[[int32], int32], x: int32) -> int32:
      return f(x)
  a = Adder(10)
  print(a(5))         # 15 -- direct call
  print(apply(a, 5))  # 15 -- passed to Fn param
  ```
- **Working**: Expression callees -- call results of arbitrary expressions, not just named functions. Supports chained calls (`make_adder(10)(5)`), subscript calls (`callbacks[0](x)`), and any expression evaluating to `Callable`, `Fn`, or a type with `__call__`. Generates `(callee_expr)(args)` in C++.
  ```python
  from typing import Callable
  from tpy import int32
  fns: list[Callable[[int32], int32]] = [make_adder(1), make_negator()]
  print(fns[0](100))       # subscript call: 101
  print(make_adder(10)(5))  # chained call: 15
  ```
- **Working**: Generator functions (`yield`) -- resumable-frame state machine (see Generators section above)
- See `docs/CLOSURES_CALLABLE_DESIGN.md` for full design and phasing.

---

## Metaprogramming / Macros

- **Open**: Compile-time code execution via decorators/annotations
  ```python
  @derive(Eq, Hash)  # generates __eq__ and __hash__ at compile time
  class Point:
      x: int32
      y: int32
  ```
- **Open**: Annotations as compile-time hooks
  ```python
  @serialize("json")  # generates serialization code
  class Config:
      name: str
      value: int32
  ```
- **Phase 1 done**: Class macro system running Python during compilation
  - `# tpy: macro_module` directive marks modules as compile-time-only
  - `@class_macro` functions receive `ClassInfo` and add/modify methods
  - `@dataclass` reimplemented as a class macro (`lib/tpy/dataclasses.py`)
  - `tpyc/macro_api.py` provides public API: metadata (`ClassInfo`, `FieldInfo`, `TypeInfo`), AST builder (`ast`), type builder (`types`), type aliases (`Expr`, `Stmt`, `Function`, `Type`). Macro modules import only from `macro_api`. AST builder includes `if_expr()`, `set_comprehension()`, `clone()`. Type builder includes `set()`. `TypeInfo` has `is_set`, `unwrap_optional()`.
  - `tpyc/macro_loader.py` loads macro modules via CPython `importlib`
- **Phase 2 done**: Call-site macros (`@call_macro`) expand at compile time
  - Macros receive `MacroArg` (AST + resolved type), return replacement `TpyExpr`
  - `dataclasses.asdict()` and `dataclasses.astuple()` as first use cases
  - Recursion into nested dataclasses, `list[DC]`, `dict[K, DC]`, `tuple[DC, ...]`, `Optional[DC]`
  - Qualified form (`dataclasses.asdict`), mixed-type dicts via `dict[str, A|B]`
  - CPython compatibility: `lib/cpy/tpyc/macro_api.py` backend -- same macro source works under both tpyc and CPython
  - Source-based authoring: `ast.quote()`, `ast.quote_expr()`, `ast.quote_fun()`, `cls.add_method_from_source()` -- write macro output as TPy source strings instead of AST builder calls
  - `macro_deps("module")` binds module name in macro namespace for qualified calls (`module.func()`) -- individual names do not spill into user code
  - Remaining: AST splicing in quote (embed computed AST nodes), hygiene
- **Phase 3 done**: FStr f-string decomposition for zero-copy logging. See `docs/FSTR_DESIGN.md` for full design, limitations, and future direction (`FStr[wrap_fn]`).
  - `FStr` compile-time-only type, `@inline` decorator for call-site body inlining
  - `MacroArg.as_fstring()` for call macro decomposition, `MacroFStringPart.is_static_str` for detecting static-storage expressions (literals, ternaries of literals, `Final[str]` name references)
  - Tuple-based dispatch to native generic functions via `std::apply`
  - `CallMacroContext` introspection: `first_param`, `get_field_type`, `get_method_return_type`, `qualified_name` -- macros discover fields/methods by name on first param (self for methods) with qualified type checking
  - `CallMacroContext.expected_type` -- type of the slot the call result lowers into (assignment/field-init LHS, declared call-arg, declared return), or `None` where sema has no expected type; enables type-directed literal rewriting in one macro
- **Phase 7 done**: Builder-trace macros (`@builder_macro` on a class describes a state machine the compiler walks at compile time)
  - `@builder_macro` / `@builder_method` / `@builder_returns(child)` / `@builder_terminal` decorators -- the terminal closes the trace and synthesizes module-level declarations
  - `BuilderContext` API surface: diagnostics, macro-time literal evaluators, typed positional / kwarg extractors, and code emission (`fresh_module_name`, `emit_record`, `emit_function`, `replace_call`)
  - `BuilderTraceExpander` runs in pass 5.5 (`SemanticAnalyzer._expand_builder_traces`) over every record-method body and free-function body; top-level statements are expanded inside `_analyze_top_level` (pass 4). Trace symbols have no runtime existence (each ctor + method statement is dropped, the terminal call is rewritten to call a synthesized free function)
  - Full v1 trace rules: tracked symbols may only appear as the receiver of registered builder method calls, may not be reassigned, escape, or be referenced inside control-flow blocks / lambdas / nested defs; exactly one terminal must be reached
  - First user: `lib/tpy/argparse.py` (see stdlib table above for the slice). v2 work tracked in `docs/MACRO_DESIGN.md`
- **Phase 8 spike (mechanism + mutation work; motivating use case not yet built)**: Function macros (`@function_macro` on a free function or record method -- a macro that rewrites the whole function body at compile time)
  - `FunctionMacroContext` -- read-only introspection (`function_name`, `module_qname`, `params` as resolved `(name, TypeInfo | None)`, `return_type`, `body`); `resolve_type(name)` to mint a primitive/builtin type (`bool`, `int32`, `float64`, ...) by name; registry-backed type introspection shared with call macros via `_MacroContextBase` (`get_field_type` / `get_method_return_type` on a record `TypeInfo` -- including inherited members -- `lookup_imported_name(name)` resolving any module-visible record/enum to a `TypeInfo` by its local name, `lookup_function_signatures(name)` returning a module-visible free function's overloads as a list of `Signature` (each with the overload's `(name, TypeInfo | None)` param list and `return_type`; `None` if the name is not a known function) so a value flowing into a typed call slot -- or returned from a typed function -- can be typed by it, the caller choosing the overload policy, and `qualified_name`; enum `TypeInfo`s expose `enum_members`); plus in-place mutation: `annotate_local(name, type)` (set a local's declared type at its introducing statement) and `replace_expr(old, new)` (identity-based replacement anywhere in the body, descending into list/dict/tuple fields)
  - Runs in pass 5.5 (`SemanticAnalyzer._expand_function_macros`), before builder-trace expansion and before body type-checking, so mutations are seen by sema. The decorator has no runtime/codegen existence
  - Applies to both source and *plugin-emitted* free functions: a source decorator is collected by the parser; a frontend plugin sets `Function.decorators=(Decorator(name="mod.fn", kwargs=...), ...)` and registers `"mod.fn"` as `MACRO` in its `decorator_manifest`, and lowering routes it into `pending_macros` -- both feed the same pass-5.5 phase. Arbitrary (non-`Expr`) per-module data the macro needs rides `FrontendModule.macro_data` (exposed as `ctx.module_data`), not decorator kwargs. See `docs/FRONTEND_PLUGIN_DESIGN.md` "Decorator registry"
  - An unrecognized *resolved* (imported) decorator on a free function or record method is treated as a function-macro reference (errors at sema if unregistered); genuinely unresolved names still error at parse
  - Methods: pass 5.5 walks record methods before free functions (mirroring builder-trace expansion); the context adds `is_method` (True for any record method) and `self_type` (enclosing record's `TypeInfo` for non-static methods; `None` for staticmethods and free functions). Plugin-emitted method decorators opt in via the `"method"` target kind in `decorator_manifest`
  - Node aliases (`Assign`, `VarDecl`, `Name`, `StrLiteral`, `BoolLiteral`) re-exported from `tpyc.macro_api` so body-walking macros can recognize statement kinds within the macro import sandbox
  - Partial: `resolve_type` covers primitives/builtins only (user types go through `lookup_imported_name`, which resolves with the module's own annotation visibility; generic records resolve to the unparameterized nominal). Still unbuilt from the body-resolver wishlist: `is_subtype_of`. Motivating use case + remaining work: local-variable type deduction (`docs/MACRO_DESIGN.md`)
  - Deferred post-sema phase: `ctx.defer_until_sema_complete(callback)` schedules a callback that runs after pass 7 (bodies type-checked), drained by `SemanticAnalyzer._drain_deferred_sema_macros` for both free functions and record methods. The callback gets a `PostSemaFunctionMacroContext` with `type_of(expr)` (inferred type, unavailable at pass 5.5), `set_expr_type(node, type)` (required for every emitted node -- sema does not re-type post-sema insertions), and `note_param_mutated(param_index)` (record that an emitted mutating call mutates a host param, so Phase-2 emits it `&` not `const&` -- pass 7 already collected mutation edges before this callback ran). `note_param_mutated` is currently free-function-host only -- it rejects a method host (method-aware `direct_mutated_params` resolution is a parked follow-up, see TODO.md). `annotate_local` is disabled post-inference; re-deferring is an error

### Compile-Time Hooks (Extensible Metaprogramming -- Future Design)

Phase 1 uses `@class_macro` decorators on standalone functions (see above). The
inheritance-based `@compile_time` / `__generate__` design below is a future alternative
for when the macro system supports class-based macro definitions:

```python
# tpy/model.py - library code, not compiler magic
class Model:
    @compile_time  # this method runs during C++ generation
    def __generate__(cls, fields: list[FieldInfo]) -> list[Method]:
        methods = []
        methods.append(generate_to_json(fields))
        methods.append(generate_from_json(fields))
        methods.append(generate_eq(fields))
        return methods
```

User code just inherits:
```python
from tpy.model import Model

class Order(Model):  # compiler sees __generate__ hook, calls it
    symbol: FixStr[8]
    price: float64
    quantity: int32
```

The compiler's role is minimal:
1. Detect that base class has `@compile_time` hooks
2. Call hooks with class metadata (fields, types, annotations)
3. Incorporate returned code into generation

This makes the system extensible without compiler changes:
```python
class Serializable:
    @compile_time
    def __generate__(cls, fields):
        return [generate_protobuf(fields)]

class Entity:
    @compile_time
    def __generate__(cls, fields):
        return [generate_orm_methods(fields)]

class Message:
    @compile_time
    def __generate__(cls, fields):
        return [generate_flatbuffer(fields), generate_validate(fields)]
```

Similar to: Python metaclasses (but compile-time), Rust proc_macro, Zig comptime, Lisp macros.

### CPython Compatibility

The same code must run in both CPython (dev/testing) and compiled C++. The `@compile_time` hooks would use `__init_subclass__` or metaclasses in CPython:

```python
class Model:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # In CPython: generate methods at class definition time
        fields = get_fields(cls)
        cls.to_json = make_to_json(fields)
        cls.from_json = classmethod(make_from_json(fields))
        cls.__eq__ = make_eq(fields)

    @compile_time  # in CPython: no-op decorator, just marks for compiler
    def __generate__(cls, fields):
        # In tpyc: this runs during C++ generation
        ...
```

Same source file, two execution paths:
- **CPython**: `__init_subclass__` creates methods dynamically at runtime
- **tpyc**: `@compile_time` hooks generate C++ code

This keeps the "write once, run in CPython, compile to C++" promise.

### Holy Grail: Pydantic-like Semantics

The dream is Pydantic's ergonomics with zero-overhead C++:

```python
class Order(Model):
    symbol: FixStr[8]
    price: float64
    quantity: int32
    side: Side  # enum
    timestamp: int64 = Field(default_factory=now)

# Automatically generates:
# - Validation (compile-time where possible, runtime checks where needed)
# - JSON/binary serialization & deserialization
# - Schema export (JSON Schema, protobuf, FlatBuffers, etc.)
# - Default values and factories
# - Field aliases, validators, constraints
# - __eq__, __hash__, __repr__
```

Key difference from Python Pydantic:
- Validation logic compiled to efficient C++ (no runtime reflection)
- Serialization is type-driven codegen, not dynamic
- Zero allocation for fixed-size types
- Optional: generate matching schemas for other languages/systems

---

## Design Principles

1. **Allocation is controllable, not forbidden**
   - Use `@noalloc` where needed, allow allocation elsewhere
   - Different applications have different needs

2. **Value vs Object semantics**
   - Small immutable types: pass by value
   - Larger/mutable types: pass by reference (implicitly)

3. **Progressive complexity**
   - Simple code should be simple to write
   - Advanced features available when needed
   - Explicit > implicit for complex cases

4. **C++ interop**
   - Generated code should be readable and idiomatic
   - Easy to call C++ from TurboPython and vice versa
   - Interop is intentionally constrained to declared, supported native shapes

5. **CPython semantic clarity**
   - Keep CPython behavior when practical
   - Emit warnings when TurboPython semantics diverge

6. **Regular Python compatibility**
   - Prefer behavior that keeps normal Python code runnable
   - If unsupported, or if semantics differ from CPython, emit clear diagnostics

## Implementation Notes

### Method Overload Resolution

Builtin method calls go through overload resolution in sema, which attaches `resolved_function_info` to the AST node. Codegen then uses this resolved info.

**Note**: `__getitem__` and binary operators (`__add__`, `__sub__`, ... and their reflected forms) support multi-overload dispatch via per-stub operator generation: each `@overload` stub / `@dispatch` variant emits its own concrete C++ `operator[]` / `operatorOP`, and C++ overload resolution selects by operand type. The `@overload` *implementation* signature (whose parameter is the operand union) is deliberately not emitted as an operator -- its body is specialized into the per-stub operators. `__setitem__` codegen still falls back to registry lookup without `resolved_function_info`, which works because it is single-overload today.

### Integer Range Tracking (Working)

The compiler tracks provable `[lo, hi]` integer value ranges and a `non_zero` flag per variable through flow analysis. This enables two optimizations:

**Bounds check elision**: When `arr[i]` is accessed and `i` is provably in `[0, len(arr))`, the compiler skips `normalize_index` (which handles negative indices and bounds checking) and emits direct `operator[]` access:

```python
for i in range(len(arr)):
    x = arr[i]  # Direct arr[i] in C++ (no bounds check)

# While-loop pattern also works via literal range tracking:
i: int32 = 0
while i < len(arr):
    x = arr[i]  # Direct arr[i] (i proven in [0, len(arr)))
    i += 1      # Invalidates range fact for soundness
```

**Division-by-zero check elision**: When `a // b` or `a % b` is executed and `b` is provably non-zero, the compiler skips the zero-check and emits `div_floor`/`mod_floor` instead of `div_check`/`mod_check`:

```python
if b != 0:
    x = a // b  # div_floor (no zero check)
    y = a % b   # mod_floor (no zero check)
```

Range facts enter the system from:
- `for i in range(len(arr))` -- `i in [0, len(arr)-1]`
- Integer literal assignment (`i = 0`, `i: int32 = 0`) -- exact value range
- `if b != 0:` / `assert b != 0` -- `non_zero=True`
- `if x > 0:` / `assert x >= 0` -- concrete lower bound
- `if x < len(arr):` / `while i < len(arr):` -- symbolic upper bound

**Safe unsigned cast elision**: When a signed-to-unsigned cast like `uint32(x)` is performed and `x` is provably non-negative (and the target is at least as wide as the source), the compiler skips the runtime range check and emits a plain `static_cast`:

```python
assert offset >= 0
u = uint32(offset)  # static_cast (no range check)
```

Range facts are invalidated on variable reassignment (including augmented assignment like `i += 1`) and merged at branch join points. Symbolic bounds referencing a container are invalidated when that container is mutated (including method calls like `.pop()`, `.clear()`).

### Parameter Mutation Inference (Working)

The compiler automatically infers which parameters each function mutates, eliminating false-positive borrow warnings when a non-mutating function is called with a borrowed container.

**Two-phase approach**: Phase 1 (during sema) collects *local* mutation facts per function -- which params are directly mutated by field writes, subscript writes, method calls, augmented assignments, and deletes -- plus call edges recording parameter flow through function calls. Phase 2 (post-sema) builds an intra-module call graph, topologically sorts it, and propagates mutation facts transitively. Cycles are handled with monotone fixpoint iteration (mutation sets only grow). Forward calls and transitive chains are fully resolved.

```python
def sum_items(items: list[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total  # mutated_params = {} (items not mutated)

def add_item(items: list[int32], val: int32) -> None:
    items.append(val)  # mutated_params = {0} (items mutated via append)

data: list[int32] = [1, 2, 3]
v = data[0]
sum_items(data)   # No warning: sum_items proven non-mutating for param 0
add_item(data, 4) # Warning: add_item mutates param 0, borrow of 'data' active
```

**Consumers**: Borrow conflict warnings (`_check_borrow_arg_conflicts`) and const-ref loop variable binding (`_check_loop_var_arg_mutation`) both use per-parameter mutation facts. If a callee is known not to mutate a specific parameter, passing a borrowed or loop-iterated container to that parameter is safe. Phase 2 also tracks `self_mutated` per method, which drives const method auto-inference (see above).

**Cross-module**: Imported functions use resolved facts from dependency-order compilation. Cross-module cycles get conservative treatment (all flowing params assumed mutated).

**Structural vs. non-structural mutations**: `mutated_params` tracks all mutations (including element-ref taking via subscript read, which requires `T&`). A separate `structural_mutated_params` tracks only mutations that *invalidate element references* -- `append`, `insert`, `extend`, `clear`, `pop`, slice assignment, `del item`, and augmented assignment on a container name. Subscript writes (`items[i] = x`) and subscript reads (`a = items[i]`) are *not* structural. Borrow conflict checks use `structural_mutated_params` when available, so `items[0] = x` does not falsely warn when an element borrow of `items` is active.

### Return-Value Borrow Contracts (Working)

The synchronous-call, async-payload and erased-callable representation readers
share a position-aware classifier. This refactor preserves current return
conventions and their existing limitations; the full callable result contract
(including permission and contained-borrow analysis) remains planned in
`CALLABLE_CONTRACT_DESIGN.md`.
The initial compatibility findings are in `CALLABLE_CONTRACT_FEASIBILITY.md`.
Analysis-only MIR first is the approved sequence; `MIR_ANALYSIS_PLAN.md` records
the implemented internal scalar CFG and borrowed-record alias/field foundation.
M2.1 adds entry-declared aliases, name reseats and direct bool/int32 fields of
borrowed plain records, including readonly access, for ordinary free functions.
M2.2 adds owned locals for plain bool/int32-field records, construction from
verified emitted THIR definitions, explicit copy, selected move-through and
acyclic replacements that preserve existing OWN versus IN_PLACE decisions.
M2.3 adds flat local tuples of bool/int32 snapshots and borrowed plain-record
identities: construct/copy/reseat, constant-index scalar reads and record-field
access. Tuple copies preserve shared mutations without following later holder
reseats. Empty tuples and readonly-source captures have internal IR tests only;
existing parser/THIR source rejections are unchanged. Nested/owned tuple elements,
tuple parameters/returns/unpacking and nested wrappers remain deferred.
M2.4 adds Optional bool/int32 snapshots and nullable borrowed plain records:
whole-value copies, supported local reseats/clears, None tests and guarded
scalar or record-field access. Copied wrappers retain their value/referent;
holder writes invalidate old presence tests, joins intersect the remaining
facts, and loops converge conservatively. Readonly payload access stays readonly.
This is internal payload-selection verification, not a new source acceptance
rule or lifetime proof. Existing nullable-record reseat source gates remain;
owned/nested wrappers, Optional calls/results and unproven runtime-checked
extraction are deferred.
M2.5 adds nonrecursive bool/int32 unions and borrowed plain-record unions,
optionally including None, with typed alternative tests and extraction aliases.
Record extraction captures the referent; scalar extraction aliases payload
storage and becomes invalid on any wrapper replacement until re-extraction.
Mixed scalar/reference and owned/recursive unions, union aggregate slots and
parameter reseats remain outside that subset (`MIR_ANALYSIS_PLAN.md`).
M2.6 adds borrowed nested plain-record paths, field-derived holders and scalar
leaf writes. A captured field keeps its subobject identity across parent-holder
reseats; readonly aliases still observe writes through mutable aliases. Existing
tuple-local and narrowed Optional/union roots compose with nested paths. Nested
owning operations and whole-field replacement remain uncovered; existing
frontend field-alias spelling restrictions are unchanged.
M2.7 adds flat tuple parameters and selected standalone borrowed-record element
captures, including normalized negative indices and per-element readonly access.
Scalar tuple members remain snapshots; captured records observe shared mutations.
Tuple wrapper constness is separate from record access. Whole/per-element owning
parameters and parameter replacement remain uncovered. Existing source gates for
local-tuple captures, reseated captures and record-tuple parameter copies into
later-reseated locals are unchanged; scalar tuple parameter copies already compile.
Readonly auto-copy metadata mismatches remain uncovered and are tracked as
`BUGS.md#readonly-auto-tuple-copy-fact`; C++ preserves the source constness.
M2.8 adds ordinary monomorphic instance-method bodies with an explicit borrowed
receiver and finalized readonly access. Scalar aliases and singleton/mixed tuple
captures retain self identity; local alias reseats do not retarget the receiver.
Existing field, owned-record and wrapper operations apply inside those bodies.
Consuming/generated method variants, properties, dunders, static/class methods,
resumables remain excluded. Direct self-to-Optional pointer
initializers stay uncovered; self-to-union capture retains its frontend gate
(`BUGS.md#self-record-union-initializer`).
M2.9 adds bounded constructor bodies: complete pure bool/int32 field
initialization precedes the existing body operations, preserving the supplied
receiver's identity. Defaults, partial initialization, bases and reference-valued
fields remain uncovered. Constructors with body effects still cannot be expanded
at MIR call sites; analyzing their bodies does not supply a call summary.
M2.10 adds qualified bool/int32 global reads and writes in supported bodies.
Globals remain shared storage across bodies, distinct from same-named locals;
short-circuit global writes remain on their guarded CFG edges. Same-module
declarations and direct module attributes are covered. From-import bindings
and reexports remain uncovered because of the existing import-rebinding defect
(`BUGS.md#imported-scalar-binding-tracks-foreign-rebind`); native globals,
aggregate/reference globals and module initialization are also outside the subset.
M2.11 preserves resolved ordinary free-function identities and typed signatures
on THIR calls and definitions, including imported aliases and reexports.
These facts describe declarations only: general calls remain MIR-uncovered,
and no call-effect, lifetime or source-admission rule changes. Overloads,
generic/callback templates, methods/constructors, native targets and resumable
factories retain separate identity gates (`MIR_CALL_CAPTURE_PLAN.md`).
M2.12 preserves complete capture inventories for selected direct lambdas and
nested defs in ordinary free functions, instance methods and constructor tails.
It distinguishes scalar binding references from readonly scalar snapshots,
borrowed plain-record parameters and receiver aliases with finalized access.
An unsupported capture or position makes the inventory unavailable; an empty
inventory proves only that there are no captures. Nested closures, record
copy/move captures, record locals, wrappers and frame-backed sources remain
outside coverage. Closure execution, escape and lifetime checks are unchanged.
M2.13 extends internal coverage to constructor-backed Optional record locals
using existing storage identities (`MIR_M2_REMAINING_PLAN.md`). Replacing or
clearing a holder preserves independent aliases; selected in-place replacement
requires a present mutable payload. Inline-slot reuse without carried storage
facts remains excluded. No source-language rule or generated C++ changes.
Owning operations in loops, arbitrary constructor effects, containers
and consuming call/return boundaries remain outside its internal coverage.
`uv run tpyc --dump-mir program.py` exposes the supported CFGs for user modules
and explicit reasons for uncovered or unavailable bodies. It uses actual
emitted THIR, writes no generated files and does not execute the program.
There is no normal compilation hook or MIR borrow checker yet. Current provenance
checks remain authoritative. Callable admission remains planned, with no new
acceptance rule or diagnostic enabled by this foundation.

M3.1 adds internal backward may-liveness for that bounded subset, visible in
`--dump-mir` as block entry/exit and statement-boundary live sets. Projected
writes use their holder; scalar union payload aliases keep their wrapper live.
Constructor initialization has a separate entry-use set. This is no move,
dead-store or lifetime-safety verdict. M3.2 adds forward reference dependencies
per holder payload, including retained copies and scalar union payload aliases.
The dump shows possible referents and active dependencies. Backing duration is
explicit, external inputs may alias, and unsupported facts remain uncovered
(`MIR_M3_LIVENESS_PLAN.md`). No new diagnostics or change of checker authority
is enabled; general storage-end events and lifetime checking remain planned.

M3.3 models bounded reuse of body-hoisted ordinary/Optional record backing
and exposes positive record-write events in `--dump-mir`
(`MIR_M3_REUSE_PLAN.md`). Only constructor-backed OWN replacement sites gain
cyclic coverage; cyclic initial construction, copy/move and in-place writes
remain uncovered. M3.4 reports internal possible
retained-object conflicts, including borrowed aggregate holders and existing
acyclic in-place replacements, also visible in `--dump-mir`. These are logical-object
retention checks, not proof of ended physical storage. Existing warnings,
source acceptance and C++ emission stay unchanged; cleanup and broader
lifetime checking remain later work.

M3.5 inventories possible lifetime-ending writes to inline scalar Optional/union
payloads, visible in `--dump-mir`. M3.6 adds internal retained-alias inspection,
also visible for covered source bodies ([plan](MIR_M3_PAYLOAD_LIFETIME_PLAN.md)).
Strict MIR validation and source behavior remain unchanged; malformed or
stale-alias witnesses are tested at the internal analysis boundary. General
cleanup and authoritative lifetime checking remain outside this proposal.

When a function returns a reference into a container parameter, the compiler records which parameters the return value borrows storage from. This allows call-site borrow tracking to register the returned reference as an *element borrow* of the source container, enabling the same conflict detection as a direct subscript borrow.

**`return_borrows_from`**: Each `FunctionInfo` carries an optional `frozenset[int]` where `-1` means `self` and `0+` means the positional parameter at that index. A non-`None` value means the return value borrows storage from those parameters (i.e. invalidated if the source container is structurally mutated).

**Inference rules** (Phase 1, during sema):

1. **Direct subscript return** -- `return items[i]` where `items` is a list/dict/set parameter: marks `items` as returned. This covers `return self.field[i]` with `self` mapped to index `-1`.

2. **Own/rvalue return** -- `return Own[T](...)` or other value types: no borrow (value-type returns never alias the source).

3. **Transitive call return** -- `return inner(items)` where `inner.return_borrows_from = {k}` and argument `k` maps to a local parameter: the outer function inherits the borrow contract for that parameter. This enables wrapper functions to propagate contracts without special-casing:

```python
def get_first(items: list[Point]) -> Point:
    return items[0]  # return_borrows_from = {0}

def get_first_wrapper(items: list[Point]) -> Point:
    return get_first(items)  # transitive: return_borrows_from = {0}
```

**Call-site borrow registration**: Wherever a call result is assigned or iterated, the compiler checks `return_borrows_from` and registers an `ELEMENT` borrow from each source container to the receiving variable or loop iterator:

- *First assignment* (`x = get_first(items)`): registers `items -> x` as ELEMENT borrow.
- *Reassignment* (`x = get_first(items)` where `x` was previously bound): registers the new borrow, replacing the old one. The borrow tracker releases the prior binding on reassignment.
- *For-loop iterable* (`for p in get_list(items)`): registers `items -> __for_iter` as ITER borrow, same as iterating a container directly.

**Conflict detection**: Once an ELEMENT borrow is active, any structural mutation of the source container (append, insert, del, etc.) triggers a warning -- because structural mutations may reallocate storage and invalidate the borrowed reference. Non-structural mutations (subscript write `items[i] = x`) do not warn, as they cannot invalidate an existing element reference.

```python
def get_first(items: list[Point]) -> Point:
    return items[0]

items = [Point(1, 2), Point(3, 4)]
x = get_first(items)
items.append(Point(5, 6))  # warning: Mutation of 'items' while borrowed
items[0] = Point(9, 9)     # ok: subscript write is not structural
```

### Field-Path Borrow Tracking (Working)

Borrow tracking extends to single-level field access paths (`self.items`, `obj.field`). This catches common patterns in methods where the receiver's container field is borrowed and then mutated:

```python
class Container:
    items: list[Point]

    def process(self) -> None:
        for p in self.items:
            self.items.append(Point(p.x, p.y))  # warning: Mutation of 'self.items' while iterating

        ptr = take_ptr(self.items[0])
        self.items.append(Point(5, 6))  # warning: Mutation of 'self.items' while borrowed

        self.items += [Point(7, 8)]     # warning: Mutation of 'self.items' while borrowed
        self.items = [Point(9, 9)]      # warning: Mutation of 'self.items' while borrowed

c = Container()
ptr = take_ptr(c.items[0])
c.items.append(Point(7, 8))  # warning: Mutation of 'c.items' while borrowed
```

**Scope**: Single-level field paths only (`obj.field`). Deeper nesting (`obj.a.b`) is conservatively not tracked (no false positives, but no warnings either). Reassigning the root variable (`c = Container()`) clears all field-path borrows for that root.

## Open Questions

1. **Allocation control ergonomics**: `@noalloc` vs `@alloc` vs module-level vs compiler flag?
2. **Container element storage**: How to spell "list of values" vs "list of pointers"?
3. **String semantics**: When does `str` allocate vs use SSO?
4. **Exceptions**: Error codes, `std::expected`, or actual exceptions?
5. **Lambda efficiency**: Resolved -- `Fn` (zero-cost template) vs `Callable` (type-erased `std::function`), explicit choice by user.
6. **Macro system scope**: How much compile-time Python execution? Safety limits?
