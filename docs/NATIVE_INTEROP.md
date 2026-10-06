# Native Interop -- Design Document

TurboPython can import existing C/C++ functions/types and export its own functions with C linkage. This enables embedding TPy code in C/C++ projects and calling external libraries without bindings or wrappers.

## Overview

Two decorators and module-level directives form the core of the system:

```python
from tpy.extern import native, export
```

- `@native` -- import a C/C++ entity (function, class, global) into TPy
- `@export(binding="C")` -- export a TPy function with C linkage
- `# tpy: native_module` -- mark a module as declaration-only (no generated code)

## Progress

| Feature | Status |
|---------|--------|
| `@native` -- import C++ function | **Done** |
| `@native("ns::func")` -- qualified C++ name | **Done** |
| `@native(binding="C")` -- import C function | **Done** |
| `@native(binding="C")` -- import C struct | **Done** |
| `@native(cpp_return_type=T)` -- declare wider C++ return for narrowing cast | **Done** |
| `@native` class -- import C++ class (fields, stub methods) | **Done** |
| `@native("factory", function=True)` on `__init__` -- factory-style constructor | **Done** |
| `@native("MyArena", indirecting=True)` -- attest heap indirection for cycle detection | **Done** |
| `@native("MyCursor", borrowing_view=True)` -- declare a value type's values borrow handles (lifetime-checked) | **Done** |
| `@native("MyRing", elements=True)` -- declare a class owns its type arguments' values as elements | **Done** |
| `@native("put", mutates="elements")` -- declare a native method replaces elements in place and moves none | **Done** |
| `@native("clock", transient=True)` / `@cpp_template("...", transient=True)` -- declare a binding that touches only its arguments and retains nothing | **Done** |
| `@native("wait", checks_signals=True)` / `@cpp_template("...", checks_signals=True)` -- declare a binding that is a Ctrl-C check point | **Done** |
| `native_field("cpp_name")` -- per-field C++ rename on `@native` classes | **Done** |
| `@native` enum -- import C++ `enum class` | **Done** |
| `native_member("cpp_name")` -- per-member C++ rename on `@native` enums | **Done** |
| `@export(binding="C")` -- export TPy function | **Done** |
| `native_global()` -- import C/C++ global variable | **Done** |
| `# tpy: native_module` | **Done** |
| `# tpy: include("header")` | **Done** |
| `# tpy: include("header", platform="linux")` | **Done** |
| `# tpy: link("lib")` | **Done** |
| `# tpy: link("lib", platform="linux")` | **Done** |
| Cross-module native imports | **Done** |
| Package re-exports of native functions | **Done** |
| Include propagation from native modules | **Done** |
| Auto-prefix `@native` with `cpp_namespace` (bare or rename without `::`) | **Done** |
| Absolute opt-out via `@native("::name")` or `@native("ns::name")` | **Done** |
| Call-site `::` qualification for `@native` functions | **Done** |
| Duplicate symbol detection | **Done** |
| `@export(binding="C")` class -- export C struct | Planned |
| C header generation (`--emit-c-header`) | Planned |
| Callback function pointers | Open |
| Auto-bindgen from C headers | Open |
| Variadic C functions | Open |

---

## Importing: `@native`

Declares a C/C++ entity that exists elsewhere. The compiler uses it for type-checking and codegen but generates no code for the entity itself. The actual definition must be available via `# tpy: include()` or the external build system.

### Functions

```python
from tpy.extern import native

# C++ function (default binding)
@native("physics::calculate_force")
def calc_force(mass: float, accel: float) -> float: ...

# C function
@native("SDL_Init", binding="C")
def sdl_init(flags: int32) -> int32: ...

# Bare @native -- uses Python name as C++ name
@native
def global_func(x: int32) -> int32: ...
```

Functions must have a `...` (stub) body. The optional string argument specifies the C/C++ symbol name. See [Rename resolution](#rename-resolution) below for how the string interacts with `cpp_namespace`.

### Classes

Import existing C++ classes or C structs. No struct definition is generated -- the compiler trusts the external type exists.

```python
# C++ class with methods
@native("b2::Vec2")
class Vec2:
    x: float
    y: float
    def length(self) -> float: ...
    def dot(self, other: Vec2) -> float: ...
    @staticmethod
    def zero() -> Own[Vec2]: ...        # factory: returns a fresh value (see below)
    @native("mag")                      # method rename
    def magnitude(self) -> float: ...

# C struct (aggregate initialization)
@native("SDL_Rect", binding="C")
class Rect:
    x: int32
    y: int32
    w: int32
    h: int32

# Opaque handle -- no fields
@native("SDL_Window")
class Window: ...
```

All methods on a `@native` class must be stubs (`...` body). Non-native methods on a native class are an error.

**Return convention -- `-> V` vs `-> Own[V]` (reference-type returns).** For a reference-type `V` (a class / container), the TPy return annotation declares the C++ return convention, and the compiler trusts it:

- `-> V` means the C++ method returns `V&` (a reference into the receiver or other stable storage). A call result bound to a local *aliases* that storage -- `p = obj.get()` binds `V*`/`V&`, and mutations through `p` reach the original (matching `dict.setdefault`, container `__getitem__`).
- `-> Own[V]` means the C++ method returns a fresh `V` by value (a factory / a moved-out value). The result is owned; binding it copies/moves, no aliasing.

This is the same contract user-defined methods follow, so there is no native special case. The consequence is a hard requirement on the binding author: a method whose C++ returns by value (e.g. a `static Vec2 zero()` factory) **must** be declared `-> Own[V]`. Declaring it bare `-> V` makes codegen bind a reference to a destroyed temporary -- a dangling pointer (or, for a `@nocopy` `V`, a C++ build error). There is currently no compiler check that a bare `-> V` native method actually returns `V&`; it is the author's contract to honor. (Free `@native` *functions* are the exception: without a declaration they take value semantics regardless of the annotation; `borrows=` / `element_of=` (below) declare what the result borrows.)

**Construction:**
- C++ classes: constructor call syntax -- `Vec2(1.0, 2.0)` -> `b2::Vec2(1.0, 2.0)`
- C structs (`binding="C"`): aggregate init -- `Rect(0, 0, 800, 600)` -> `SDL_Rect{0, 0, 800, 600}`

The class-level `@native(name)` is enough for both `MyClass(args)` (call form) and `e = MyClass(args)` (assignment form) -- both lower to `name(args)` directly. Annotating `__init__` with its own `@native` is only required when the constructor maps to a **different** C++ symbol than the class type itself (a factory function); see below.

**Factory-style constructors (`@native("factory", function=True)` on `__init__`).** When the Python class type doesn't have a directly-callable C++ constructor and instances are produced by a free-function factory, declare each constructor stub with its own `@native(..., function=True)`. The `function=True` flag tells codegen to emit a free-function call (`factory(args)`) instead of treating the name as a class type:

```python
# a size argument has no matching buffer constructor, so it names a factory
@native("::tpy::Bytes")
class bytes:
    @native("tpy::bytes_from_size", function=True)
    def __init__(self, n: int32) -> None: ...

    @native("tpy::bytes_from_int_iterable", function=True)
    def __init__(self, x: Iterable[int32]) -> None: ...
```

Generated code: `bytes(10)` -> `tpy::bytes_from_size(10)`. Multiple `__init__` overloads each pick their own factory. Reach for this when the class's natural C++ constructor doesn't exist or doesn't match Python's call shape; otherwise the bare class-level `@native` is sufficient.

**Per-field rename (`native_field`).** When the external C/C++ field name differs from the Python name (e.g. C struct `sin_family`/`sin_port` conventions, or C++ `m_x` member-prefix conventions), use `native_field("cpp_name")` in the field's default-value slot:

```python
from tpy.extern import native, native_field

# C++ class: friendlier Python names over m_-prefixed C++ members
@native
class Vec2:
    x: int32 = native_field("m_x")
    y: int32 = native_field("m_y")

# C struct binding (e.g. under lib/tpy/_bindings/): expose POSIX field names
# under idiomatic Python names
@native("sockaddr_in", binding="C")
class SockAddrIn:
    family: uint16 = native_field("sin_family")
    port:   uint16 = native_field("sin_port")
```

Field reads and writes emit the renamed C/C++ member (`v.x` -> `v.m_x`, `a.port` -> `a.sin_port`). The rename is inherited: accessing the field through a TPy-level subclass of the `@native` class resolves it too (e.g. a user subclass of `OSError` reads `.errno` -> `error_number`); a subclass redeclaring the same field name shadows the rename and binds its own plain member (sema warns). Constructor calls are positional (aggregate init for C structs, constructor args for C++ classes) so the rename does not affect construction. `native_field` is rejected on non-`@native` classes and requires exactly one positional string literal argument.

**`@native` ValueType placeholders.** A slot declared before its first value (a name first assigned in both branches of an `if`, a generator frame local, a module global, ...) is built as `::MyHandle h{};`: value-initialized by the type's C++ default constructor (an aggregate's fields come out zero). The type must have one, and it may run where the program never constructs a value -- keep it cheap and free of observable side effects (no printing, no global state), the same contract a TPy-defined `ValueType`'s zero-argument `__init__` carries (LANGUAGE_FEATURES "Placeholders run the default constructor"). A module global's placeholder is a namespace-scope object, so that constructor runs during C++ static initialization, before `main` and before the module's other globals are bound -- it must not depend on them (BUGS.md#valuetype-global-default-ctor-static-init). Codegen emits `static_assert(std::is_default_constructible_v<::MyHandle>, "@native ValueType 'Handle' needs a default constructor: ...")` beside the type's `tpy::is_value_type` specialization, so a type without one fails the C++ build at that line rather than inside some later use. A generic `@native` `ValueType` is checked per instantiation by C++ itself.

### Global variables

```python
from tpy.extern import native_global

# C++ global
score: int32 = native_global("engine::score")

# C global
frame_count: int32 = native_global("DG_FrameCount", binding="C")

# C global array (decays to pointer)
data: Ptr[int32] = native_global("shared_data", binding="C", array=True)

# Bare -- uses Python name as the global C++ symbol
# (native globals emit at :: regardless of the module's cpp_namespace,
# matching C ABI conventions for externally-linked variables)
tick: int32 = native_global()
```

Global imports must be at module level and require a type annotation. Generated C++:

```cpp
// C++ global
namespace engine { extern int32_t score; }

// C global
extern "C" int32_t DG_FrameCount;

// C global array
extern "C" int32_t shared_data[];
```

References to imported globals emit the C/C++ name directly: `print(frame_count)` -> `std::cout << DG_FrameCount`.

### Enums

```python
# tpy: include("native_types.hpp")
from enum import Enum, auto
from tpy.extern import native, native_member


@native("cfg::Mode")
class Mode(Enum):
    NONE_MODE = native_member("None")  # C++ side has `None` (Python keyword)
    AUTO = native_member("Auto")
    MANUAL = native_member("Manual")


@native("ns::E")
class E(Enum):
    A = auto()                          # C++ name = TPy name (`A`)
    B = auto()


@native("ns::Tag")
class Tag(Enum):
    Alpha = 100                         # explicit values are verified
    Beta = 200                          # against the C++ side via
    Gamma = 300                         # per-member static_assert
```

`@native` enums bind to an existing C++ `enum class`; no enum declaration is generated and no `operator<<` is emitted (avoids conflict with user-provided one). The user's `# tpy: include(...)` directive must make the C++ enum type visible.

**Two value-declaration modes.** The TPy-declared value is either *implicit* (via `auto()` or `native_member()`) -- in which case the C++ side is the source of truth and `e.value` reads `static_cast<underlying>(e)` directly -- or *explicit*, in which case codegen emits a per-member `static_assert` pinning the TPy-declared value to the C++ side. A mismatch fails at C++ compile time with a clear "does not match" message rather than silently miscompiling. Use explicit values when you want to document the binding's expected values in TPy source; use `auto()` when you don't want to mirror them.

**`native_member("cpp_name")` aliases the TPy-side member name** when the C++ enumerator is a Python keyword (`None`, `True`, `False`), a TPy keyword, or follows a different naming convention. Member access (`Mode.NONE_MODE`) emits the C++ enumerator (`::cfg::Mode::None`); reflection (`m.name`, `Mode["NONE_MODE"]`) keeps the TPy-side name. Only valid inside `@native` enum bodies; takes a single positional string literal.

**Iteration via reflection.** `match`, `e.name`, `e.value`, `Mode(v)`, and `Mode["MEMBER"]` all work across the binding boundary via `tpy::EnumUtil<E>` generated alongside each `@native` enum.

**Restrictions.**
- Only `@native` is allowed on enum classes; other decorators and any `@native` kwargs (`binding=`, `function=`, `cpp_return_type=`) are rejected.
- `auto()` is idiomatic; `native_member("cpp_name")` is also implicit. Explicit integer values are accepted and are verified against the C++ side via a per-member `static_assert` in the generated `.cpp`. Mixing `auto()`/`native_member()` with explicit integers in the same body is rejected.
- Methods in a `@native` enum body are rejected, as bodied methods on a `@native` class are: a `@native` type is declaration-only. Use a module-level function taking the enum.
- Nested `@native` enums (inside a class body) are not supported. Declare them at module top level using the fully-qualified C++ name (e.g. `@native("ns::Container::Kind") class Kind(Enum): ...`) -- the qname encodes the C++ nesting, so TPy structure does not need to mirror C++ structure.
- `IntEnum` with an explicit mixin (`class E(int8, Enum):`) selects the underlying integer type; it must match the C++ side's underlying type.
- Multiple TPy modules binding to the same C++ enum produce duplicate `EnumUtil<E>` definitions at link time (same constraint as duplicate `@native` records). Declare each binding in one TPy module and import from there.

### Generic classes

```python
@native("std::vector")
class StdVector(Generic[T]):
    def push_back(self, val: T) -> None: ...
    def size(self) -> int32: ...
```

### Declaring heap indirection: `indirecting=True`

Cycle detection in recursive type aliases (`type Tree = Lit | Box[Tree]`) needs to know whether a wrapper type contains its element by value (would form an infinite-size cycle) or via pointer indirection (breaks the cycle). For TPy records the compiler infers this structurally by walking the record's fields -- a `_ptr: Ptr[T]` field is recognized as indirecting without any annotation. For `@native` records whose internal storage is opaque to TPy (no fields declared, or fields that hide the indirection in a C++ template), declare the kwarg:

```python
@native("my::Arena", indirecting=True)
class Arena[T]:  # C++ stores T behind a unique_ptr / arena handle
    ...
```

With this, `type Tree = Leaf | Arena[Tree]` compiles. Without it, the compiler treats `Arena[T]` as a by-value container and rejects the alias.

Used in the stdlib by `list`/`dict`/`set` (see `lib/tpy/tpy/_builtins/_{list,dict,set}.py`). Don't reach for it on records whose TPy field declarations already expose a `Ptr`-typed (or other indirecting) field -- the structural walk handles those.

### Declaring a borrow handle: `borrowing_view=True`

A native VALUE type whose every value points into storage it does not own --
a string view, a span, a cursor or iterator over a container -- declares it:

```python
@native("my::Cursor", borrowing_view=True)
class Cursor(ValueType):
    @native("get")
    def get(self) -> int32: ...
```

The compiler then lifetime-checks the type's values wherever the stdlib views
(`StrView`, `BytesView`, `Span`, `SpanIter`, the dict views, which all
declare the same kwarg) are checked: returning or yielding one that borrows a
local or a temporary is an error (`Cannot return Cursor referencing a local or
temporary`), and a native method returning one is taken to borrow its
receiver. WHAT a value borrows is decided at the producing call (the method's
receiver, the view constructor's first argument), not by the declaration.
A USER view's own constructor is not yet recognized as borrowing its
arguments: `return Cursor(xs)` with `xs` a parameter is rejected as if it
borrowed a temporary. Return a view from a method (`buf.cursor()`) instead;
the constructor rule is tracked in TODO.md ("View constructors borrow their
arguments; a view root is durable by its emitted storage").

The kwarg describes a TYPE whose every value borrows. A handle that borrows
only for some producing calls must not declare it: `copy_iter()`'s `CopyIter`
borrows a source passed as an lvalue but moves in and owns any temporary
source -- a container, a view, a record or an iterator (`copy_iter(make())`,
`copy_iter(map(f, xs))`) -- so as a borrowing view it would reject or warn
about the owning form. That borrow depends on the argument's
value category and belongs to the producing call -- the callable-level borrow
annotation below (`borrows=` / `element_of=`), which `copy_iter()` does not use yet
(TODO.md, "Callable-level borrow annotation for native stubs").

The obligation is the stub author's: an unannotated native value type is
treated as OWNING its data, so forgetting the kwarg on a handle type means its
values are copied and returned with no lifetime check at all. The kwarg is
rejected on a reference type (a class without the `ValueType` marker, which is
borrow-checked as a reference already) and on a function-level `@native`.

A TPy record that merely HOLDS a view field declares nothing and is not
checked (`BUGS.md#record-view-field-escapes-local-buffer`).

The fact also decides how a generic handle spells a `readonly[T]` type
argument: a `borrowing_view` template gets `const T` (a handle over readonly
elements is the const handle -- `Window[readonly[Node]]` renders
`my::Window<const Node>`, as the runtime's dict views specialize on it), while
every other native template keeps `T` (`Bag[readonly[Node]]` renders
`my::Bag<Node>`): a storage template owns its elements, and C++ containers
and allocators reject a const element type. The readonly-ness of a storage
template's element is enforced by sema alone.

### Declaring what a result borrows: `borrows=` / `element_of=`

A binding without a body has no `return` for the compiler to read, so it
cannot tell on its own whether the C++ hands back one of its arguments. A
free binding that does says so by naming the parameters its result may come
from (read as a union -- the result is one of them), with keywords of its
own `@native` or `@cpp_template`:

```python
from tpy.extern import native, cpp_template

@native(borrows=("a", "b"))
def pick(a: Node, b: Node) -> Node: ...      # C++: Node& pick(Node&, Node&)

@native(element_of=("items",))
def first(items: Iterable[Node]) -> Node: ...  # C++: a reference to an element

@cpp_template("::lib::pick({0}, {1})", borrows=("a", "b"))
def pick2(a: Node, b: Node) -> Node: ...
```

`borrows=` names parameters the result IS (or lives inside the storage of);
`element_of=` names parameters the result is handed out by ITERATING. A
given keyword must name at least one parameter, and a name may appear in
only one. (These keywords replaced a separate decorator --
the facts belong to the binding they describe. `@cpp_template` is to fold
into `@native` the same way: TODO.md, "`@cpp_template` folds into
`@native`".)

The call then behaves as the bodied twin `def pick(a, b): return a` does
wherever every named argument LENDS: `n = pick(x, y)` binds `Node& n =
pick(x, y);`, a write through `n` reaches the argument, and a readonly
argument gives a readonly result. The operands become mutable whenever the
result is, as a generic returning its parameter does (even `m = pick(a, b);
return m.v` takes `Node& a, Node& b`); declare them readonly to keep them
const. A `borrows=` argument lends when its form
proves it outlives the call -- a name, a field or subscript of one, a call
that itself lends, a view a call hands back (`d.values()`); a temporary
(`pick(x, Node())`) or a method read off a temporary does not, and a
conditional operand is refused
(`BUGS.md#borrow-call-walrus-conditional-operand-rejects`). An `element_of=` argument lends only when it is
a CONTAINER (a list, a dict's values, a `Span`): an iterator never lends --
its step is valid only until the next one, a generator may rebind the object
it yielded -- and neither does a class whose `__iter__` builds an iterator.

Where an argument does not lend, the result is a fresh value: read in place
(`pick(x, Node()).v`, an argument) it copies nothing -- except where the
C++ hands back a copy (an `element_of=` parameter declared `Iterable`,
given an iterator or a generator: the callee walks it with an iterator of its
own, gone at the return). Passed to a parameter the callee writes, that copy
is what the callee writes, and it says so (`copies Node into argument 'p'
of 'f(...)'; use copy() to make this explicit`); a parameter only read takes
it silently. A binding, an
owning slot or a generator / coroutine frame that holds it copies, which
that sink reports -- `copies Node into local 'n'; use copy() to make this
explicit` at a local, `... into the frame of 'f(...)'` for a frame, the
field / `Own` slot's own words elsewhere (`copy(...)` makes the copy
explicit; a non-copyable type is an error). When every named argument is a
fresh owner nothing can see the copy, and nothing is said. Returning such a
value through a borrowing `-> Node` is the dangling-return error. A
value-type result (`int`, `str`, a tuple) is a value at every call. An
owned element slot -- a tuple / list / set / dict literal element, a list /
set comprehension element, a dict-comprehension value, an item store, an
empty container's first store / `append` / `add` (its element type is the
owned payload, never a reference) --
holds a COPY of a reference-type result (a class or a `list` / `dict` /
`set` / `bytearray`) whatever its form, borrow included, when the slot's
type is the result's own, or an `Optional` of it at a literal element
(`[pick(x, y) for _ in r]` -> `push_back(Node(::tpy::assert_lent(pick(x,
y))))`), warned where the copy is observable (`copies Node into owned
storage`, `... (tuple element 0)` for a tuple local). Not admitted yet: a
union element slot holding the result as one member, an `Optional`
comprehension element, an owned element in a generic body over an open
`T`, and a tuple local assigned more than once (it holds pointers to its
class elements, so the copied element is refused in words). Holding the
alias needs a name, `n = pick(x, y)` then `(n, 1)`.

Two refusals key on the declaration, so they hold for EVERY binding that
declares `borrows=` / `element_of=`, not only the builtin `min` / `max` / `next`: an augmented
assignment whose target holds the call (`pick(x, y).v += 1`) is refused --
the render would run the call twice -- and where the C++ hands back a copy,
a write through it in place is the lost-write error.

The C++ must match: a reference into a `borrows=` argument (with a const
overload for const arguments), and for `element_of=` a reference into a
container's element; for a source walked through `__next__`, a value when
the parameter is an `Iterable` (the builtin `min` / `max` helpers) and a
reference valid until the caller's next step when it is the caller's
`Iterator` (`next`), which only an in-place use reads. A call the compiler
binds as a borrow renders wrapped in `::tpy::assert_lent(...)`, so a
declared binding whose C++ returns by value stops the C++ build with "a call
the compiler bound as a borrow returns a value; return a reference, or
declare the stub's result Own[...]". A free function without the
declaration keeps a by-value result, even when its C++ returns a reference
(`BUGS.md`, "An UNDECLARED free `@native` function").

A bodyless METHOD stub declares the same way, `self` naming the receiver:

```python
@native
class Node:
    @native(borrows=("other",))
    @readonly
    def pick(self, other: Node) -> Node: ...   # C++: Node& pick(Node&) const

@native("::Bag", elements=True)
class Bag[T](NativeIterable[T]):
    @native("first", element_of=("self",))
    @readonly
    def first(self) -> T: ...                  # a reference to an element
```

The call takes the same borrow-or-value decision a free binding's call does,
the receiver lending as an argument would. The declaration REPLACES the
receiver borrow an undeclared native method infers from its signature, so a
method that names only `other` says its result does not borrow the
receiver: `ro.pick(x)` on a `readonly` receiver with a mutable `x` is a
mutable `Node&`. The builtin `dict.get(key, default)` is declared
`borrows=("default",), element_of=("self",)`: over a reference-type value it
hands back the stored object or the default itself, as CPython does
(`m = d.get("a", fb)` binds `P& m`; a temporary default is read in place, and
a local holding it copies, warned). `self` is refused on a `@staticmethod` /
`@classmethod` (no receiver) and on a method taking `self: Own[Self]` (the
receiver is moved into the call); the keywords are refused on an operator
method (`__x__`) and a `@property`, which are reached through syntax of their
own that does not take the declaration yet. A native method WITHOUT the
keywords still infers that its result borrows the receiver; requiring a
declaration where the result could borrow several sources (the ambiguity rule)
is deferred (TODO.md, "Borrow-declaration elision and the ambiguity rule for
native stubs"). A declared result that is a value type HOLDING a class
instance (an Optional, union or tuple of one) is refused: the by-value C++
result would copy the instance. In a generic body, a call whose result is
an open `T` -- bare, or held in an Optional, union or tuple (`V | None`) --
is decided before instantiation as a copy: the C++ hands it back by value,
and a holder warns in the generic hedge (*may copy V | None into local 'm'
if not a value type*). A concrete class beside an open `T`
(`tuple[T, Box]`, `list[V] | None`) is refused as its concrete twin is:
that copy is certain whatever `T` turns out to be.

Each name must be a parameter of the binding whose argument can hold storage
the result borrows (a reference type, an open `T`, a view, a `str`, a callable
whose environment may hold references); an unknown name, a duplicate, a
scalar parameter, or the decorator on a function with a body is an error. The
builtin `min` / `max` (`borrows=` for two or three operands, `element_of=` for
one iterable) and `next(it, default)` (`borrows=("default",)`,
`element_of=("it",)`) are declared this way. The declaration says what the
result borrows, not that the borrow is sound: as with every native
signature, the stub author answers for the C++ actually returning one of the
named arguments.

### Declaring element storage: `elements=True` and `mutates="elements"`

A native container -- a class whose values own the values of its type
arguments in storage of their own -- declares it on the class, and each
method that replaces elements without moving any declares that on the
method:

```python
from typing import Iterator
from tpy import int32, Own, NativeIterable, readonly, pure
from tpy.extern import native

@native("my::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...      # the element member: T

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, i: int32) -> T: ...   # a positional subscript yields the element

    @native("put", mutates="elements")
    def put(self, i: int32, v: Own[T]) -> None: ...   # replaces an element in place

    @native("push")
    def push(self, v: Own[T]) -> None: ...      # undeclared: may move or free every element
```

`elements=True` says a borrow can point into the type's elements -- a loop
variable, a reference to a record element -- so the borrow analysis models
them as storage of their own, apart from the container's structure.
`mutates="elements"` says the method writes elements in place and moves
none, so iterators and references to the other elements stay valid (the
element it overwrites is replaced under any reference to it): sema does not warn
when it is called on a container being iterated, where `push` above warns
(`Mutation of 'r' while iterating over it ('push' invalidates the
iterator)`). A mutating method (neither `@readonly` nor `@pure`) that
declares nothing may replace elements and also move or free every one of
them; that is what an undeclared method means, so `mutates="structure"` is
not a spelling -- `"elements"` is the only value. The declaration is trusted
on a method of any `@native` class, whether or not the class declares
`elements=True`: a container whose element is no type parameter (a
`Bag(NativeIterable[int32])`) cannot declare its elements, and this is its
only way to say a write keeps iterators valid. Each use is an audit of the
binding: the stdlib declares
`elements=True` on `list`, `dict`, `set` and `Array`, and
`mutates="elements"` on `dict.__setitem__` and `list`'s integer-index
`__setitem__` (its slice overloads stay undeclared).

**`element_effect="insert"` / `"lookup"` (stdlib-only).** The builtin
`list`, `dict` and `set` stubs also declare, per method, what it does with an
argument naming the container's element, key or value (or a container of
them): holds it from then on (`insert`: `append`, `add`, `__setitem__`,
`update`, `setdefault`, ...) or only compares it with what it holds or hands
it back (`lookup`: `get`, `pop`'s key and default, `discard`,
`__contains__`, ...). A container literal whose numbers are not decided yet
(`xs = [1, 2]`) is widened by an inserted value and requires a looked-up one
to fit; a method without the keyword decides its numbers first. The keyword
is read only on those three stubs and ignored on any other class.

**Members.** Which type arguments are elements is read off the stub, never
off the type's name:

- The ELEMENT is the type parameter the class's readonly `__iter__` yields
  (`-> Iterator[T]`). A consuming `__iter__` (not `@readonly`) does not count.
- A `__getitem__` returning the element is a positional subscript (`list`,
  `Array`, `Span`).
- A `__getitem__` returning ANOTHER type parameter declares the VALUE member
  and must be keyed by the element: `dict[K, V]` iterates `K` and declares
  `def __getitem__(self, key: readonly[K]) -> V`.
- Members are positions among the type parameters, so `dict[str, str]` has
  two members of one type.

A `borrowing_view=True` type declares the members it views the same way,
without `elements=True` (a view owns nothing): `dict_values[K, V]` yields
`V`, and `dict_items[K, V]`, whose `__iter__` yields `tuple[K, V]`, views
both members and binds no single-element cursor. `SpanIter`, whose
`__iter__` returns itself, declares none.

**Rejections.** At parse time: `mutates=` on a class or a free function;
`elements=` on a method or a free function; a `mutates` value other than
`"elements"`; `mutates="elements"` beside `@readonly`, `@pure` or
`@auto_readonly` (in any decorator order), or on a `@staticmethod` or
`@classmethod` (no receiver); `mutates=` on `@cpp_template`, whose mutating
methods stay undeclared. When the class is registered, `elements=True` is
rejected when no readonly `__iter__` yields one of the class's own type
parameters, together with `borrowing_view=True`, when two readonly `__iter__`
overloads yield different parameters, when `__getitem__` overloads return
different parameters, when a positional `__getitem__` returns a parameter
other than the element, when a `__getitem__` is keyed by a parameter
`__iter__` does not yield, and when a `__getitem__` sits beside an
`__iter__` that yields a tuple of two members. A `borrowing_view=True`
class is not obliged to declare members: one whose declarations disagree
in any of these ways is accepted and simply declares none, so the analysis
does not model it.

**Current limit.** On a user `@native` type, an element read into a local or
a return value (`p = r[0]`, `return r[0]`) and a subscript write
(`r[0] = v`) are rejected by C++ code generation before any analysis reads
the facts (THIR reject sites `decl.slot_type`, `subscript.recv_type`,
`setitem.family`). Today the declarations serve loops over the type, field
reads through a subscript (`r[0].x`) and method calls. A subscript renders
through the runtime's `tpy::__getitem__`, which calls a `__getitem__`
member of the C++ type whatever name the stub declares
(`BUGS.md#native-getitem-ignores-declared-name`), so the C++ class must
define one.

### Declaring a transient binding: `transient=True`

A function or method binding whose C++ touches nothing but its arguments
declares it on the binding itself, `@native` or `@cpp_template`:

```python
@native("tpy::time_time", transient=True)
def time() -> float: ...
```

The `transient` binding fact promises that the bound C++ reads or writes
only its arguments as their declared mutability says, retains nothing after
return or raise, reaches no other TPy storage and runs no user code. `pure`
implies it. It is published as a stub callee's `TRANSIENT` contract for
MIR's stub call contract; sema does not read it. Each use is an audit of
the binding. The kwarg is a bool literal; it is rejected on a class-level
`@native` (it describes a call, not a type's values) and on a binding
that carries a TPy body.

### Declaring a check point: `checks_signals=True`

A binding whose C++ may raise a pending Ctrl-C -- it calls
`::tpy::check_signals()` or throws `KeyboardInterrupt` itself -- declares
it on the binding, `@native` or `@cpp_template`:

```python
@native("tpy::time_sleep", checks_signals=True)
def sleep(seconds: float) -> None: ...
```

The compiler reads it when it decides whether a cleanup body run under
C++ `noexcept` (`__del__`, `__move__`, `__hash__`, an abandoned frame's
cleanup) is inert: a call to a marked stub makes the body open a
`::tpy::DeferSignals` scope, so the check point defers instead of
terminating the process, while an unmarked stub over inert arguments
leaves the body scope-free. Every runtime function that calls
`check_signals()` on its success path must therefore be bound by a marked
stub; an unmarked one reached from an inert-looking `__del__` terminates on
a Ctrl-C. The `EINTR` check in `raise_mapped_os_error` makes every
errno-raising `os` stub a check point on that path; only the retry loops
(`read_fd`, `write_fd`, `waitpid`) are marked today
(`BUGS.md#eintr-check-point-in-inert-cleanup-body`). The kwarg is a bool literal,
rejected on a class-level `@native` and on a binding that carries a TPy
body, like `transient`.

### Narrowing C++ returns: `cpp_return_type=T`

`@native` declares an exact-match binding to a C++ symbol -- the TPy signature must match the C++ side. When the C++ side returns a wider type than the TPy declared return (e.g. `std::vector::capacity()` returns `size_t`, but the user wants an `int32` view), use `cpp_return_type=T` to tell codegen the underlying type:

```python
@native("std::vector")
class Vec[T]:
    @property
    @native("capacity", cpp_return_type=uint64)  # capacity() returns size_t
    def cap(self) -> int32: ...

# Codegen emits:  static_cast<int32_t>(v.capacity())
```

Without `cpp_return_type`, the implicit narrowing would trip `-Wconversion` / `-Wsign-conversion` at the use site. With it, codegen wraps the call in `static_cast<DECLARED_TPY_RETURN>(...)` so the conversion is explicit. Works on both methods and free functions. The annotated value is a TPy type name (e.g. `uint64`); the cast target is always the declared TPy return type.

For more involved transformations (computed expressions, multi-step conversions), use `@cpp_template` instead -- it gives full control over the emitted call expression.

### Inline C++ templates: `@cpp_template`

`@cpp_template("...")` (from `tpy.extern`) replaces a call with an inline C++ expression. The template body substitutes placeholders:

- `{self}` -- the receiver (methods only; an error in a free function)
- `{0}`, `{1}`, ... -- positional arguments
- `{cpp}` -- the C++ spelling of the (substituted) return type
- `{T}` / type-param names -- substituted with the inferred type argument

```python
@cpp_template("std::rotl<uint32_t>({0}, {1})")
def rotl32(x: uint32, n: int32) -> uint32: ...
```

To emit a **literal** C++ brace (aggregate-init, a lambda body, a scope, a GCC statement-expression), double it -- `{{` -> `{` and `}}` -> `}`, matching Python's `str.format` convention:

```python
# Emits: []() { return a + b; }()
@cpp_template("[]() {{ return {0} + {1}; }}()")
def lambda_sum(a: int32, b: int32) -> int32: ...
```

A lone unescaped brace (or an out-of-range `{N}`) is a compile-time diagnostic, not an internal error.

**A runtime-value placeholder (`{self}` or `{N}`) may appear at most once.** Substitution is a textual paste with no evaluate-once binding, so a repeated value placeholder would evaluate its argument twice -- side effects run twice, and a repeated side-effecting receiver makes `std::stable_sort({self}.begin(), {self}.end())` undefined (the two evaluations can view different containers). The compiler rejects this at parse time. Type placeholders (`{cpp}`, `{T}`) are inert and may repeat freely. When you need an evaluated argument more than once, bind a typed C++ helper with `@native` -- it evaluates each argument once by construction.

---

## Exporting: `@export`

Compiles a TPy function and emits it with C linkage so it can be called from C/C++ code.

```python
from tpy.extern import export

# Export with C linkage
@export("app_init", binding="C")
def init() -> None:
    print("initialized")

# Bare -- uses Python name as C symbol
@export(binding="C")
def app_tick(dt: float) -> None:
    update_state(dt)
```

The function must have a body (not `...`). The optional string argument specifies the C symbol name. `binding="C"` is required.

Every type in the signature must be C-representable -- see "C-linkage signatures" under [Supported types](#supported-types). In particular `str`, `bytes`, containers and TPy classes are rejected; strings and buffers cross as `Ptr[readonly[uint8]]` and are converted explicitly.

Generated C++:

```cpp
extern "C" void app_init() {
    std::cout << "initialized" << "\n";
}
```

### Classes (planned)

Export a TPy class with C-compatible layout:

```python
@export(binding="C")
class GameState:
    score: int32
    level: int32

    def reset(self) -> None:
        self.score = int32(0)
        self.level = int32(1)
```

Would generate:

```cpp
extern "C" {
    struct GameState { int32_t score; int32_t level; };
    void GameState_reset(GameState* self);
}
```

### Ctrl-C in a host program (`--no-main`)

A standalone TPy program's generated `main()` sets process-wide dispositions
(`tpy::process_startup()`): the uncaught-exception terminate handler, SIGPIPE
ignored, and the SIGINT layer that turns Ctrl-C into `KeyboardInterrupt` (see
the "Ctrl-C (SIGINT) -> `KeyboardInterrupt`" entry under
[Error Handling](LANGUAGE_FEATURES.md#error-handling)). A `--no-main` build
(TPy code linked into a C/C++ host, or a CPython extension module) installs none
of them: the host owns its signals. Ctrl-C then does whatever the host's
disposition says, and `time.sleep` / `input()` / blocking sockets /
`subprocess` waits and pipes in TPy code are not interruptible. (`asyncio.run` still installs a SIGINT handler for the
duration of the run and restores the host's afterwards.)

A host that wants Ctrl-C delivered into TPy code opts in with one of two calls
from `<tpy/core.hpp>`:

```cpp
// Arm Ctrl-C delivery; the calling thread becomes the one that receives
// KeyboardInterrupt. Installs TPy's SIGINT handler unless the argument is
// false. Returns false on failure.
bool tpy::install_interrupt_handler(bool install_sigint_handler = true);

// For a host that keeps its own SIGINT handler (armed above with false): call
// this from that handler -- it is async-signal-safe -- to request delivery.
void tpy::request_interrupt() noexcept;
```

Arming is once per process: a repeated `install_interrupt_handler()` keeps the
first call's target thread. It can add TPy's SIGINT handler (a `true` call
after a `false` one installs it then, replacing the host's), but never removes
one: a `false` call after a `true` one leaves TPy's handler in place.

The interrupt then surfaces as a `tpy::KeyboardInterrupt` C++ exception (a
`std::exception`) thrown out of whichever TPy call was running on that thread,
at its next interruptible operation. Threads spawned through `tpy.thread` block
SIGINT so the kernel delivers it to the target thread; host-created threads
should do the same if they may run while TPy code waits.

A host context that must not throw (a C callback, a destructor) can call TPy
code inside a deferral scope from `<tpy/core.hpp>`:

```cpp
{
    tpy::DeferSignals defer;  // RAII; nests, not copyable or movable
    tpy_callback();           // no KeyboardInterrupt in here
}
```

While a scope is open on the target thread, check points do not raise,
`time.sleep` / `input()` / blocking sockets / `subprocess` waits and pipes run
to completion without waking for Ctrl-C, and an `asyncio.run` started inside handles no SIGINT. The Ctrl-C
stays pending and is raised at the first check point after the scope closes,
in whatever TPy call next reaches one. Generated code opens the same scope in
a body that runs under `noexcept` (`__del__`, `__move__`, `__hash__`, a
dropped generator's cleanup) unless the compiler proved the body cannot reach
a check point. A second Ctrl-C while the first is still pending
terminates the process, as it does outside a scope. Nothing raises a Ctrl-C
that is still pending when no further check point runs: a standalone program
exits normally with it unraised, and in a `--no-main` host it waits for the
next TPy call that reaches a check point (only the disarm that ends an
`asyncio.run`-armed layer clears it).

#### Compiling the layer out (`--no-signals`)

A host that wants none of this builds with `tpyc --no-signals`, which adds the
C++ define `TPY_NO_SIGNALS` to every compile. The generated code is the same;
the runtime changes under the define:

- `tpy::check_signals()`, the manipulator that ends every print chain, and
  `tpy::DeferSignals` are empty, so `print`, `sys.stdout` / file I/O and
  cleanup bodies carry no check and no thread-local access.
- `time.sleep` and `input()` are the plain calls, a socket wait polls the
  socket alone, `JoinHandle.join()` is a plain join, `subprocess.Popen.wait()`
  is a plain `waitpid` and its pipes read and write without a wait in front,
  and worker threads are spawned without touching the signal mask.
- Nothing installs a SIGINT handler: a generated `main()` leaves SIGINT at its
  inherited disposition (the terminate handler and `SIGPIPE` ignore stay), and
  `asyncio.run` in a host installs none for the run. No signal becomes a
  `KeyboardInterrupt`; `signal.raise_signal(SIGINT)` takes the process's
  disposition. A `KeyboardInterrupt` the program raises itself is unaffected.
- An `asyncio.run` whose tasks all wait with no timer and no I/O registered
  raises `RuntimeError` (`asyncio.run: no progress possible`) instead of
  blocking: without a SIGINT to wait for, nothing could ever wake it. Runs
  that handle no SIGINT for another reason (an inherited ignore, a run off
  the interrupt target thread) already behave this way.
- `tpy::install_interrupt_handler()` and `tpy::request_interrupt()` are not
  declared.

The define is an ABI switch for the runtime headers, so it goes on every
translation unit that includes them: the generated sources, the runtime's own
`.cpp` files and the host's. `tpyc -b` does that for its own build; a build
from `sources.cmake` applies `TPYC_COMPILE_DEFINITIONS` to the whole target
(`target_compile_definitions(myapp PRIVATE ${TPYC_COMPILE_DEFINITIONS})`).
A translation unit compiled with the define does not link against
`signal_impl.cpp` compiled without it (undefined `tpy_signals_compiled_out`):
that runtime could still be armed and raise `KeyboardInterrupt` through
cleanup bodies whose deferral scope was compiled empty. The option does not
apply to the REPL.

What stays in an opted-out build, next to the syscalls it sits beside: a
blocking socket operation still tries without waiting and then polls, and
`asyncio.run` still asks once whether to watch for SIGINT and is told no.

---

## Module directives

### `# tpy: native_module`

Marks a module as declaration-only. No `.hpp` or `.cpp` is generated. All `@native` entities are for type-checking only. `# tpy: include()` directives are propagated to importing modules.

Each module that should be declaration-only must have its own `# tpy: native_module` directive -- it does not propagate from package `__init__.py` to child modules.

See [Rename resolution](#rename-resolution) below for how `@native` renames interact with `cpp_namespace`.

`@export` is not allowed in `native_module` modules (they are declaration-only).

### `# tpy: cpp_namespace("ns")`

Overrides the C++ namespace for the module's generated code (default: `tpyapp::module_name`). Also drives the [rename resolution](#rename-resolution) rule for `@native` entities in the module.

### `# tpy: include("header")`

Adds an `#include` directive. Quoted paths use `#include "..."`, angle-bracket paths use `#include <...>`. Optional platform filter.

```python
# tpy: include("mylib/types.h")
# tpy: include(<SDL2/SDL.h>)
# tpy: include(<sys/time.h>, platform="linux")
```

In `native_module` modules, includes propagate transitively into any consumer whose generated C++ reaches a type defined in this module -- whether named explicitly in the consumer's imports or surfaced only through field/method chains on imported types. Native-to-native chains follow the same rule: a hand-written native header that forward-declares a type from another native module does not need to be edited; the consumer header pulls in both. In regular modules, includes go into the module's own generated header.

### `# tpy: link("library")`

Passes `-l<name>` to the linker. Optional platform filter.

```python
# tpy: link("SDL2")
# tpy: link("ws2_32", platform="windows")
# tpy: link("pthread", platform="linux")
```

Link directives are collected from all modules in the dependency graph.

---

## Cross-module imports

Native entities imported in one module can be used in another via normal Python imports:

```python
# types.py (native_module)
# tpy: native_module
# tpy: cpp_namespace("game")
# tpy: include(<game/types.hpp>)

@native
class Player:
    name: str
    score: int32
```

```python
# main.py (regular module)
from types import Player

def greet(p: Player) -> None:
    print(p.name)
```

The compiler propagates `<game/types.hpp>` to `main.hpp` so the C++ type is available.

The other direction -- hand-written C++ (an `@native` companion `.cpp`, or an
external caller) that iterates a TPy generator -- must include the module's
`<mod>_inl.hpp` after its `<mod>.hpp`. A small generator's `__next__` is
defined only there, `inline`, so a TU that includes just the header compiles
but fails to link.

---

## Symbol naming

All decorators accept an optional string argument for the C/C++ symbol name:

```python
@native("SDL_Init", binding="C")      # Python: sdl_init, C: SDL_Init
def sdl_init(flags: int32) -> int32: ...

@export("Helper_Add", binding="C")     # Python: helper_add, C: Helper_Add
def helper_add(x: int32) -> int32:
    return x + int32(1)
```

### Rename resolution

The rename string interacts with the module's `cpp_namespace` directive (if any). The rule covers both the no-rename case (`@native`) and the explicit-rename case (`@native("name")`), and applies to module-level `@native` functions, records, and protocols. C-linkage (`binding="C"`) and `@builtin_type` records are exempt -- their symbol names are unchanged by this rule.

| Form | Interpretation | Emitted C++ (in `cpp_namespace("mylib")`) |
|------|----------------|-------------------------------------------|
| `@native` (no rename) | relative to `cpp_namespace` | `::mylib::PythonName` |
| `@native("foo")` (no `::` in string) | relative to `cpp_namespace` | `::mylib::foo` |
| `@native("a::b")` (contains `::`) | absolute -- `cpp_namespace` ignored | `::a::b` |
| `@native("::foo")` (leading `::`) | absolute to global scope | `::foo` |

**Rule of thumb:** any `::` in the rename string opts out of the namespace prefix. Use `@native("::libc_name")` to bind to libc or other global-scope symbols from inside a namespaced module (e.g. `@native("::socket")` in a `cpp_namespace("tpystd::_bindings::posix_socket")` module).

```python
# tpy: cpp_namespace("engine::core")
# tpy: include("<engine/core/types.hpp>")

from tpy.extern import native

@native                          # -> ::engine::core::Session
class Session: ...

@native                          # -> ::engine::core::init_scope
def init_scope() -> None: ...

@native("init_scope_v2")         # -> ::engine::core::init_scope_v2 (relative)
def init_scope2() -> None: ...

@native("other::Thing")          # -> ::other::Thing (absolute, contains ::)
class Thing: ...

@native("::global_helper")       # -> ::global_helper (absolute, leading ::)
def global_helper() -> None: ...
```

### Call-site qualification

All `@native` function calls (C++ linkage) emit their symbol absolute-qualified -- codegen prepends `::` at every call site. This prevents C++ unqualified lookup from binding to a namespace member, class method, or ADL hit before finding the intended external symbol. C-linkage `@native(binding="C")` and `@export(binding="C")` are exempt: their declarations are namespace-scoped `extern "C"`, so the prefix would force global-scope lookup and miss the declaration.

## Errors

| Condition | Error |
|-----------|-------|
| `@native` with real body | `must have '...' body` |
| `@export` with `...` body | `must have a body` |
| Both `@native` and `@export` on same function | `cannot have both @native and @export` |
| Same native symbol declared twice | `duplicate extern symbol 'name'` |
| Non-stub method on `@native` class | `methods on @native class must be stubs` |
| `native_global()` inside a function | `can only be used at module level` |
| `native_global()` without type annotation | `requires a type annotation` |
| `@export` in `native_module` | `@export not allowed in native_module` |
| Non-C-representable type in a `binding="C"` signature | `is not representable in the C ABI` (plus a per-family remedy) |
| `*args` on a `binding="C"` function | `variadic parameters are not supported on a C-linkage function` |
| Non-C-representable type on a `binding="C"` `native_global` | `is not representable in the C ABI` |

## Supported types

Native functions can use any type with a direct C++ mapping:

| TPy type | C/C++ type |
|----------|-----------|
| `int8/16/32/64` | `int8_t/16/32/64_t` |
| `uint8/16/32/64` | `uint8_t/16/32/64_t` |
| `float` / `float64` | `double` |
| `float32` | `float` |
| `bool` | `bool` |
| `str` | `std::string` (param: `std::string_view`) |
| `None` (return) | `void` |
| `Ptr[T]` | `T*` |
| `Ptr[readonly[T]]` | `const T*` |
| `Span[T]` | `std::span<T>` |

**Fixed-width arguments from unbounded values:** converting a BigInt (or any
wider value) into a fixed-width native argument (`int64(x)`, `int32(x)`, ...)
panics uncatchably when out of range. Stdlib code calling `@native` functions
must range-check the value and raise the appropriate catchable exception
BEFORE the conversion -- the same validate-in-BigInt-first discipline used
for record field stores applies to native-call arguments (see the timestamp
guard in `lib/tpy/datetime.py::_from_epoch_us` for the pattern; the missing
guard there was a review-caught uncatchable-panic bug).

### C-linkage signatures

The table above is the C++-linkage surface. A `binding="C"` signature -- `@native(binding="C")`, `@export(binding="C")`, `native_global(..., binding="C")` -- is emitted verbatim into an `extern "C"` declaration, so it is restricted further: sema rejects any type a C caller cannot spell.

**Permitted:** fixed-width integers (`int8`..`uint64`), `float32`, `float`, `bool`, `char`, `Ptr[T]` for any `T` (including `Ptr[None]` -> `void*` and a pointer to a `@native(binding="C")` struct), `T | None` where `T` is a reference type (pointer repr -- `-> Widget | None` is the nullable-handle idiom and emits the same `Widget*` as `Ptr[Widget]`, at a parameter as at a return), `@native` enums (the bound C/C++ header owns the spelling), and `None` in return position (`void`). `readonly[...]` and `Own[...]` around any of these is permitted -- neither changes the C spelling of a value type.

**Rejected:** `int` (`BigInt`), `str` / `StrView` / `String`, `bytes` / `bytearray`, `list` / `Span` / `Array`, `tuple`, `T | None` over a value type (`int32 | None` is `std::optional`), unions (a variant, even where its alternatives are pointers), TPy classes, a plain (non-`@native`) enum, a `@native(binding="C")` struct passed **by value** (it emits `S&`, a C++ reference), `Own[S]` over a reference type (`S&&`), and `*args`.

The `array=True` form of `native_global` checks its **element** type instead -- `extern "C" <element> <name>[];` names the element rather than passing it, so a `@native(binding="C")` struct is permitted there (`pts: Ptr[Point] = native_global("g_pts", binding="C", array=True)` emits `extern "C" Point g_pts[];`) although the same struct by value in a signature is not.

Without the gate these emitted C++ types no C header can express (`const ::tpy::BigInt&`, `std::vector<int32_t>&`, `::SDL_Rect&`) or -- worse -- a signature that compiled and then silently misbehaved: a `str` param respells to `const char*` at the signature while the body still renders against `std::string_view`, so `s == "hello"` compared the incoming pointer against a literal's address and was always false.

**`@native(binding="C")` class fields and stub methods are deliberately not gated.** tpyc emits nothing for them -- they mirror a declaration the author's own C header owns -- so they stay under the ordinary `@native` "the author asserts the C side" contract. The gate covers only what tpyc itself writes into an `extern "C"` declaration.

#### Strings and buffers by hand

There is no marshaling layer, so the conversion is explicit and visible in the signature. A C string crosses as `Ptr[readonly[uint8]]`, converted through `tpy.unsafe`:

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

`unsafe_cstr` takes `String` (owned), not `str`, because only the owned form carries the null terminator `std::string` guarantees; `unsafe_ptr`'s `str` overload points into view storage that has none. The returned pointer is valid only while the `String` lives, so bind the string to a name before the call rather than building it inline.

A byte buffer or a sequence takes the ordinary C shape, written out: `Ptr[readonly[uint8]]` / `Ptr[T]` plus an explicit length parameter. Generating that shape (and the return direction, which needs an ownership policy) is the deferred marshaling work tracked in `TODO.md`.

---

## Other decorators

These are orthogonal to the import/export system and remain unchanged:

| Decorator | Purpose |
|-----------|---------|
| `cpp_template("...")` | Inline C++ template expansion; takes `transient=True` and `checks_signals=True` like `@native` (see "Declaring a transient binding", "Declaring a check point") |
| `pure` (from `tpy`) | No non-local mutation, no I/O, nothing retained after return or raise; implies `readonly`. Read by sema's borrow-argument check, mutation call edges, the with-exit and loop-hold write checks (`sema/loop_frames.py`) and `sema/receiver_calls.call_mutates_receiver`, and published as a stub callee's `PURE` contract for MIR's stub call contract, which admits it only at inert or owned-leaf arguments of builtin TypeDefs (a protocol or callable parameter bound to user code refuses: `@pure` was never audited for "runs no user code") |
| `native(..., mutates="elements")` | Marks a native method as replacing elements in place and moving none, so it invalidates no iterator (see "Declaring element storage") |
| `value_ptr_coercion` | Type coercion annotation |
| `virtual_raise` | Class marker: its hand-written C++ `__raise__` dispatches (is not `throw *this`), so `raise X(args)` routes through it instead of the fresh-throw peephole. Not inherited. Used by `OSError`'s errno -> subclass mapping |

---

## Future ideas

### C header generation

Generate a standalone C header from `@export(binding="C")` declarations:

```bash
tpyc --emit-c-header src/game.py -o game_api.h
```

### Auto-bindgen from C headers

Parse C headers and generate `@native` declarations automatically:

```bash
tpyc --bindgen /usr/include/SDL2/SDL.h -o sdl.py
```

### Callback function pointers

Pass TPy functions as C callbacks:

```python
@native("qsort", binding="C")
def qsort(base: Ptr[None], count: int32, size: int32,
           cmp: CCallback[[Ptr[readonly[None]], Ptr[readonly[None]]], int32]) -> None: ...
```

### Variadic C functions

Support for `printf`-style variadic calls (design TBD).

### String and buffer marshaling

Automatic conversion between TPy types and C types at the FFI boundary, in both the argument and the return direction (`str` <-> `const char*`, `bytes` / `list[T]` <-> `T*, size_t`).

Half of this once existed by accident and has been withdrawn: a `str` param on a `binding="C"` function respelled to `const char*` in the signature while the body kept rendering against `std::string_view`, which miscompiled string reads silently. A signature respell alone is therefore not the feature -- a real conversion has to run on both sides of the call. Those types are rejected outright today (see "C-linkage signatures"), and the designed replacement is a generated wrapper function rather than an entry prologue, since only a wrapper can change arity (`bytes` -> `(const uint8_t*, size_t)`) or marshal a return value. Tracked in `TODO.md`.
