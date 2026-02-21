# Protocol System for TurboPython - Design Sketch

## Status

| Phase | Scope | Status |
|-------|-------|--------|
| **Phase 1** | Simple protocols (Sized), user-defined protocols, C++20 concept generation | Done |
| **Phase 2** | Protocol matching in sema for function params | Done |
| **Phase 3** | Generic protocols (`Sequence[T]`), type param substitution | Done |
| **Phase 4** | `NativeIterable[T]` marker protocol, `extends` declaration | Done |
| **Phase 5** | `Self` type in protocols, recursive substitution | Done |
| **Phase 6** | Compiler trait protocols (`NativeContiguous`, `MutableSequence`, `NativeRangeConstructible`) | Done |
| **Phase 7** | User-defined protocols (moved to Phase 1) | Done |
| **Phase 8** | Python-compatible `Iterable[T]`/`Iterator[T]` with `__iter__`/`__next__` | Planned |
| **Phase 9** | Dynamic protocol dispatch (`@dynamic`, vtables, zero-allocation stack dispatch) | Partial (steps 1-4) |

## Overview

Protocols enable structural subtyping (compile-time duck typing). A type matches a protocol if it has the required methods, without explicit inheritance. This is Python's `typing.Protocol` (PEP 544) and similar to Go interfaces.

## 1. User-facing syntax (Python-compatible)

```python
from typing import Protocol

class Sized(Protocol):
    def __len__(self) -> Int32: ...

class Iterable(Protocol[T]):  # Generic protocol
    def __iter__(self) -> Iterator[T]: ...

class SupportsAdd(Protocol):
    def __add__(self, other: Self) -> Self: ...

# Usage in type annotations
def process(items: Sized) -> Int32:
    return len(items)

def sum_all(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total
```

## 2. Module system definition (for builtins)

```python
# tpyc/modules/protocols.py

from tpyc.modules import BuiltinModule, ProtocolDef, MethodSig
from tpyc.typesys import INT32

module = BuiltinModule("typing")

# Simple protocol
module.protocol("Sized", methods=[
    MethodSig("__len__", params=[], returns=INT32),
])

# Generic protocol
module.protocol("Iterable", type_params=["T"], methods=[
    MethodSig("__iter__", params=[], returns=GenericRef("Iterator", "T")),
])

# Protocol with Self type
module.protocol("SupportsAdd", methods=[
    MethodSig("__add__", params=[ParamDef("other", Self)], returns=Self),
])
```

## 3. Type system additions

```python
# tpyc/typesys.py

@dataclass(frozen=True)
class NamedType(TpyType):
    """A user-defined type (record or protocol).

    During parsing, is_protocol defaults to False (unknown).
    After registration in sema, is_protocol is set correctly.
    """
    name: str
    type_args: tuple[TpyType | int, ...] = ()
    is_protocol: bool = False

    @property
    def is_record(self) -> bool:
        return not self.is_protocol

    def with_protocol_flag(self, is_protocol: bool) -> 'NamedType':
        """Return a copy with is_protocol set."""
        if self.is_protocol == is_protocol:
            return self
        return NamedType(self.name, self.type_args, is_protocol)
```

## 4. Semantic analysis

```python
# tpyc/sema.py - in type checking

def _check_type_compatible(self, arg_type: TpyType, param_type: TpyType) -> bool:
    # Direct match
    if arg_type == param_type:
        return True

    # Protocol matching (structural)
    if isinstance(param_type, NamedType) and param_type.is_protocol:
        return self.protocols.type_conforms_to_protocol(arg_type, param_type)

    # ... other rules
```

## 5. Codegen (C++ concepts or templates)

### Option A: C++20 Concepts

```cpp
// Generated for Sized protocol
template<typename T>
concept Sized = requires(T& t) {
    { t.__len__() } -> std::convertible_to<int32_t>;
};

// Function using protocol
template<Sized T>
int32_t process(T& items) {
    return items.__len__();
}
```

### Option B: SFINAE (C++17 compatible)

```cpp
// Type trait for Sized
template<typename T, typename = void>
struct is_sized : std::false_type {};

template<typename T>
struct is_sized<T, std::void_t<decltype(std::declval<T>().__len__())>>
    : std::true_type {};

// Function using enable_if
template<typename T, std::enable_if_t<is_sized<T>::value, int> = 0>
int32_t process(const T& items) {
    return items.__len__();
}
```

### Chosen Implementation: Free Function Dispatch (C++20 Concepts)

The implementation uses **Option A (C++20 Concepts)** with a **free function dispatch** pattern to bridge Python's dunder methods with C++ standard library types:

```cpp
// In tpy/protocols.hpp - free functions for __len__

// Overloads for std types (which don't have __len__ method)
template<typename T>
int32_t __len__(const std::vector<T>& x) { return static_cast<int32_t>(x.size()); }

template<typename T, std::size_t N>
int32_t __len__(const std::array<T, N>& x) { return static_cast<int32_t>(x.size()); }

template<typename T>
int32_t __len__(std::span<const T> x) { return static_cast<int32_t>(x.size()); }

// Default template for user types with __len__() method
template<typename T>
    requires requires(const T& t) { { t.__len__() } -> std::convertible_to<int32_t>; }
int32_t __len__(const T& x) { return x.__len__(); }

// Sized concept uses the free function
template<typename T>
concept Sized = requires(const T& t) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
};
```

This approach:
- Keeps `std::vector` as-is (no wrapper types, no performance penalty)
- Enables both built-in types and user types to satisfy the same protocols
- Uses `tpy::__len__(x)` uniformly in generated code for `len(x)` calls
- Allows user-defined concepts to also use the free function dispatch

## 6. `len()` using protocols

```python
# tpyc/modules/builtins.py

from tpyc.typesys import NamedType

# Sized protocol type for len() parameter
SIZED = NamedType("Sized", is_protocol=True)

module.function("len", overloads=[
    MethodDef(
        params=[ParamDef("x", SIZED)],
        returns=INT32,
        cpp="tpy::__len__({0})",  # Uses the free function dispatch
    ),
])
```

The semantic analyzer checks if the argument type conforms to `Sized` by verifying it has a `__len__` method with the correct signature. The codegen simply uses the `tpy::__len__()` free function which has overloads for all standard container types.

## 7. Data structures summary

```python
@dataclass
class ProtocolInfo:
    """Protocol metadata in the TypeRegistry."""
    name: str
    methods: list[MethodSignature]
    type_params: list[str] = field(default_factory=list)
    parent_protocols: list[str] = field(default_factory=list)

@dataclass
class MethodSignature:
    name: str
    params: list[tuple[str, TpyType]]
    return_type: TpyType

@dataclass(frozen=True)
class NamedType(TpyType):
    """Unified type for records and protocols."""
    name: str
    type_args: tuple[TpyType | int, ...] = ()
    is_protocol: bool = False  # True for protocols, False for records
```

## 8. Built-in protocols

| Protocol | Methods | Used by | Status |
|----------|---------|---------|--------|
| `Sized` | `__len__` | `len()` | ✅ Working |
| `Sequence[T]` | `__len__`, `__getitem__` | indexing | ✅ Working |
| `MutableSequence[T]` | `__len__`, `__getitem__`, `__setitem__` | subscript assignment validation | ✅ Working |
| `NativeIterable[T]` | (marker) | `for` loops | ✅ Working |
| `NativeContiguous[T]` | (marker) | `Span[T]` coercion | ✅ Working |
| `NativeRangeConstructible[T]` | (marker) | range construction | ✅ Working |
| `Iterable[T]` | `__iter__` | user-extensible iteration | Phase 8 |
| `Iterator[T]` | `__next__` | iteration | Phase 8 |
| `Hashable` | `__hash__` | dict keys | Planned |
| `SupportsInt` | `__int__` | `int()` coercion | Planned |
| `SupportsIndex` | `__index__` | indexing | Planned |

## 9. Compiler Traits (Data-Driven Type Behavior)

A key goal is enabling types like `StaticList` to be fully defined in source code, without hardcoded compiler references. Protocols provide the mechanism: types declare their capabilities, and the compiler queries these declarations instead of checking `isinstance(type, SomeType)`.

**Status**: `StaticList` is now fully module-defined via `ModuleType` - no hardcoded `StaticListType` class exists in the compiler. The type's behavior (C++ codegen, methods, protocol conformance) is entirely driven by its module definition in `tpyc/modules/tpy.py`.

### Built-in Trait Protocols

```python
from typing import Protocol

class HasLength(Protocol):
    """Types that support len()."""
    def __len__(self) -> Int32: ...

class Iterable(Protocol[T]):
    """Types that can be iterated over."""
    def __iter__(self) -> Iterator[T]: ...

class NativeContiguous(Protocol[T]):
    """Types with contiguous memory layout, coercible to Span[T]."""
    # Marker protocol - no methods, uses extends declaration

class ConstructibleFromRange(Protocol[T]):
    """Types that can be constructed from a range/iterator."""
    @classmethod
    def from_range(cls, items: Iterable[T]) -> Self: ...

class SubscriptableRead(Protocol[T]):
    """Types that support read-only indexing: x[i]."""
    def __getitem__(self, index: Int32) -> T: ...

class SubscriptableMut(SubscriptableRead[T], Protocol[T]):
    """Types that support mutable indexing: x[i] = value."""
    def __setitem__(self, index: Int32, value: T) -> None: ...
```

### How the Compiler Uses Traits

Instead of hardcoded type checks like:

```python
# OLD: hardcoded in compiler
if isinstance(arg_type, (ArrayType, ListType)):
    # generate len() call
```

The compiler queries the type's protocol conformance:

```python
# NEW: data-driven
if arg_type.conforms_to(HasLength):
    # generate len() call
```

### Type Declarations with Traits

Types declare their capabilities through two parallel mechanisms:

**1. Structural conformance** - types that have all required methods automatically conform:
```python
# StaticList has __len__ and __getitem__, so it conforms to Sequence[T]
class StaticList(Generic[T, N]):
    def __len__(self) -> Int32: ...         # → conforms to Sized
    def __getitem__(self, i: Int32) -> T: ...  # + __len__ → conforms to Sequence[T]
    def __setitem__(self, i: Int32, v: T) -> None: ...
```

**2. Explicit extends** - for marker protocols with no methods:
```python
# In module system, types declare protocol conformance
module.type("StaticList", ..., extends=["NativeIterable[T]", "NativeContiguous[T]"])
module.type("list", ..., extends=["NativeIterable[T]", "NativeContiguous[T]"])
module.register_type(STR, ..., extends=["NativeIterable[Char]"])  # str is not NativeContiguous
```

This design allows:
- **Protocols with methods** (Sized, Sequence) use structural conformance
- **Marker protocols** (NativeIterable) use explicit `extends` declaration
- Both mechanisms work together - a type can use both

### Coercion via Protocols

Implicit coercions are driven by protocol conformance:

```python
def takes_span(s: Span[Int32]) -> None: ...

arr: Array[Int32, 3] = [1, 2, 3]
takes_span(arr)  # OK: Array extends NativeContiguous[Int32]

lst: list[Int32] = [1, 2, 3]
takes_span(lst)  # OK: list extends NativeContiguous[Int32]

s: str = "hello"
takes_span(s)  # ERROR: str does not extend NativeContiguous
```

Generated C++:
```cpp
void takes_span(std::span<const int32_t> s);

// std::span accepts contiguous ranges implicitly
takes_span(arr);
takes_span(lst);
```

### Benefits

1. **Extensibility**: New container types work automatically if they implement the right protocols
2. **Maintainability**: No need to update compiler code for each new type
3. **Transparency**: Type capabilities are visible in source code
4. **User types**: Users can define types that integrate with builtins (`len()`, `for`, etc.)

### Trait Resolution at Compile Time

Protocol conformance is checked statically. The compiler:

1. Parses protocol definitions (from `typing` or user code)
2. For each type, collects its method signatures
3. When checking `conforms_to(Protocol)`, verifies all required methods exist with compatible signatures
4. Generates C++ code based on the protocol's codegen rules

No runtime vtables or dynamic dispatch—everything resolves to direct method calls.

## 10. Implementation phases

1. **Phase 1**: Simple protocols (Sized) - no generics, no Self ✅ **COMPLETE**
   - Built-in `Sized` protocol with `tpy::__len__` free function dispatch
   - User-defined protocols with `class Name(Protocol):` syntax
   - Protocol conformance checking in semantic analysis
   - C++20 concept generation for both built-in and user-defined protocols
   - Template function generation for protocol-typed parameters
2. **Phase 2**: Protocol matching in sema for function params ✅ **COMPLETE** (included in Phase 1)
3. **Phase 3**: Generic protocols (`Sequence[T]`) ✅ **COMPLETE**
   - Generic protocol definitions with type_params in ProtocolInfo
   - NamedType with type_args for instantiated generic protocols
   - Type parameter substitution in protocol conformance checking
   - Parameterized C++20 concept generation (e.g., `tpy::Sequence<int32_t>`)
   - Indexing support for protocol-typed variables
   - Validation: bare generic protocols (e.g., `Sequence` without type args) are compile errors
4. **Phase 4**: `NativeIterable[T]` protocol ✅ **COMPLETE**
   - `NativeIterable[T]` in `tpy` module for C++ range-based for loops
   - Marker protocol with `extends` declaration (no methods required)
   - Types declare conformance via `extends=["NativeIterable[T]"]` in module system
   - For-each loops work with protocol-typed parameters
   - C++20 concept `tpy::NativeIterable<ElemT>` using `std::ranges::begin/end`
   - **Note**: Python-compatible `Iterable[T]` and `Iterator[T]` deferred to Phase 8
5. **Phase 5**: `Self` type in protocols ✅ **COMPLETE**
   - `Self` type for protocol method signatures (parsed, substituted during conformance)
   - Recursive substitution for nested types like `Own[Self]`, `Ptr[Self]`
   - Validation: `Self` only allowed in protocol contexts
   - Regular (non-dunder) method calls on protocol-typed values
6. **Phase 6**: Compiler trait protocols ✅ **COMPLETE**
   - `NativeContiguous[T]` - types with contiguous memory layout, coercible to `Span[T]`
   - `MutableSequence[T]` - types that support `__len__`, `__getitem__`, and `__setitem__`
     - Used by sema to validate subscript assignment (Span, str don't conform → read-only)
     - C++20 concept `tpy::MutableSequence<ElemT>`
   - `NativeRangeConstructible[T]` - types that can be constructed from a range
     - Unified `tpy::from_range<Container>(range)` template using iterator-pair constructor
     - Reserves capacity if container supports `reserve()` and range has known size
     - C++20 concept `tpy::NativeRangeConstructible<ElemT>`
     - list and StaticList extend this protocol
7. ~~**Phase 7**: User-defined protocols~~ ✅ **COMPLETE** (moved to Phase 1)
8. **Phase 8**: Python-compatible `Iterable[T]` and `Iterator[T]` (future)
   - `Iterator[T]` with `__next__() -> T` + `raise StopIteration`
   - Compiler optimization: detect StopIteration pattern → efficient has_next codegen
   - `Iterable[T]` with `__iter__() -> Iterator[T]` for user-extensible iteration
   - Requires: either exceptions or `Optional[T]` type support
9. **Phase 9**: Dynamic protocol dispatch (future)
   - `@dynamic` decorator on protocol definitions
   - Object-safety validation at definition site
   - Abstract base class + adapter template generation
   - `Dyn[P]` builtin type -- non-nullable borrowed dynamic reference
   - Implicit wrapping: concrete type -> adapter when assigned to dynamic context
   - Integration with `Box[P]`, `Rc[P]`, `Ptr[P]`
   - Requires: `Box[T]` type, move semantics

## 11. Iteration Protocol Design

TurboPython has two iteration approaches, designed to balance Python compatibility with C++ performance.

### Design Rationale: Why Two Protocols?

**Problem**: Python's iteration model (`__iter__`/`__next__` with `StopIteration`) differs fundamentally from C++'s (`begin()`/`end()` iterators).

**Key insight**: For-loops don't actually need Python's `Iterator[T]` at the language level—C++ handles iteration automatically via `begin()`/`end()`. Manual iteration (`it = iter(x); val = next(it)`) is rare.

**Solution**: Two-tier approach:
1. `NativeIterable[T]` (now) - enables for-loops over protocol params with zero overhead
2. `Iterable[T]`/`Iterator[T]` (future) - Python-compatible, user-extensible

### Naming: Why NativeIterable, not Iterable?

We chose `NativeIterable[T]` instead of `Iterable[T]` because:

| Concern | Rationale |
|---------|-----------|
| **Reserves standard name** | `Iterable[T]` in `typing` should match Python semantics |
| **Sets expectations** | "Native" signals C++/compiler-level, not user-extensible |
| **Avoids confusion** | Users won't expect to implement `__iter__` on their types |
| **Optional divergence** | Like `Int32` vs `int`, users opt into TurboPython-specific types |

### NativeIterable[T] (Current - Phase 4)

```python
from tpy import NativeIterable, Int32

def sum_all(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total
```

**Characteristics**:
- Defined in `tpy` module (TurboPython-specific)
- **Marker protocol** - types declare conformance via `extends`, no methods required
- Uses C++ `begin()`/`end()` under the hood
- All built-in containers (list, Array, Span, StaticList, str) conform via `extends`
- Users **cannot** create custom NativeIterable types (requires C++ begin/end)
- Zero overhead - compiles to C++ range-based for

**Module system declaration**:
```python
# Types declare conformance explicitly
module.type("list", ..., extends=["NativeIterable[T]"])
module.type("Array", ..., extends=["NativeIterable[T]"])
module.register_type(STR, ..., extends=["NativeIterable[Char]"])
```

**Two conformance mechanisms work in parallel**:
1. **Explicit extends** - for marker protocols like `NativeIterable[T]` with no methods
2. **Structural** - for protocols with methods (like `Sequence[T]` with `__len__` + `__getitem__`)

### Iterable[T] + Iterator[T] (Future - Phase 8)

```python
from typing import Iterable, Iterator

class MyIterator(Iterator[Int32]):
    def __next__(self) -> Int32:
        if self.done:
            raise StopIteration
        return self.value

class MyContainer(Iterable[Int32]):
    def __iter__(self) -> Iterator[Int32]:
        return MyIterator(self.data)
```

**Characteristics**:
- Will be defined in `typing` module (Python-compatible)
- Uses Python's `__iter__`/`__next__` protocol
- Users **can** create custom iterable types
- Enables manual iteration: `it = iter(x); val = next(it)`

### Iterator Implementation Options (Phase 8)

Three approaches were considered for signaling iteration exhaustion:

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A** | `raise StopIteration` | Python-compatible | Requires exception support |
| **B** | `__next__() -> Optional[T]` | No exceptions needed | Different signature than Python |
| **C** | `has_next()` + `next()` | Works now, simple | Not Python-compatible, may need rework |

**Chosen approach**: Option A with compiler optimization.

Users write standard Python:
```python
def __next__(self) -> Int32:
    if self.done:
        raise StopIteration
    return self.value
```

Compiler detects the `raise StopIteration` pattern and generates efficient C++:
```cpp
struct MyIterator {
    bool __has_next__() const { return !done; }
    int32_t __next__() { return value; }  // Only called when has_next
};

// for loop compiles to:
for (auto it = container.__iter__(); it.__has_next__(); ) {
    auto x = it.__next__();
    // body
}
```

**Benefits**:
- Python-compatible user API
- Zero-overhead C++ (no actual exceptions thrown)
- Works with CPython without harness changes

### Why Phase 8 Is Blocked

Full `Iterator[T]` support requires one of:

1. **Exception support** (`try`/`except`) - for `raise StopIteration`
2. **`Optional[T]` type** - for exception-free `__next__() -> Optional[T]`

Both are currently marked "Open" in LANGUAGE_FEATURES.md.

### Summary

| Protocol | Module | User-extensible | For-loops | Manual iteration | Status |
|----------|--------|-----------------|-----------|------------------|--------|
| `NativeIterable[T]` | `tpy` | No | ✅ | No | ✅ Phase 4 |
| `Iterable[T]` | `typing` | Yes | ✅ | Yes | Phase 8 |
| `Iterator[T]` | `typing` | Yes | N/A | Yes | Phase 8 |

## 12. Dynamic Protocol Dispatch

Static protocols (C++20 concepts) are zero-cost but monomorphized -- each concrete type
produces a separate template instantiation. Dynamic protocols add runtime dispatch via
vtables, enabling polymorphism where the concrete type is erased at compile time.

### Design Principles

- **No hidden allocations** -- dynamic dispatch uses stack-allocated adapters and the
  existing pointer-local/slot mechanism. Heap allocation only happens when the user
  explicitly requests it (e.g., `Box[P]`, `list[Box[P]]`).
- **Structural conformance** -- a type conforms to a `@dynamic` protocol if it has the
  required methods. No explicit `extends` declaration needed (same as non-dynamic protocols).
- **Pythonic syntax** -- `pet: Pet = Dog()` just works. No wrapper types required.

### `@dynamic` Annotation

A protocol must be explicitly marked `@dynamic` to enable runtime dispatch:

```python
from typing import Protocol
from tpy import dynamic

@dynamic
class Speakable(Protocol):
    def speak(self) -> None: ...

@dynamic
class Drawable(Protocol):
    def draw(self, x: Int32, y: Int32) -> None: ...
```

Without `@dynamic`, a protocol is always statically dispatched (C++20 concept, template
monomorphization). This is the existing behavior, unchanged.

`@dynamic` means "this protocol supports vtable dispatch." It enables using the protocol
as a type annotation for variables and parameters with runtime polymorphism.

### Dispatch Modes

A `@dynamic` protocol supports both static and dynamic dispatch, selected by syntax:

| Usage | Dispatch | C++ |
|-------|----------|-----|
| `pet: Speakable` (param) | dynamic | `void f(__tpy_Speakable_Base& pet)` |
| `pet: Speakable` (local) | dynamic | `__tpy_Speakable_Base* pet` (pointer-local) |
| `T: Speakable` (type bound) | static | `template<Speakable T> void f(T& pet)` |

- **Bare protocol type** (`pet: Speakable`) -- dynamic dispatch via vtable. Works for
  function parameters (passed as `Base&`) and local variables (pointer-local to
  stack-allocated adapter).
- **Type bound** (`T: Speakable`) -- static dispatch via C++20 concept constraint,
  monomorphized. Same as non-dynamic protocols. Use this when you want zero-cost
  dispatch and don't need type erasure.

For non-`@dynamic` protocols, `pet: Proto` remains static (template), same as today.

### Usage Examples

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> None: ...

class Dog:
    def make_noise(self) -> None:
        print("Woof")

class Cat:
    def make_noise(self) -> None:
        print("Meow")

# --- Dynamic dispatch (type-erased, vtable) ---

def greet(pet: Pet) -> None:
    pet.make_noise()         # virtual dispatch

dog: Pet = Dog()             # stack-allocated adapter, pointer-local
dog.make_noise()             # virtual dispatch
greet(dog)                   # pass erased value

my_dog = Dog()
greet(my_dog)                # implicit wrap: temporary adapter at call site

# Reassignment to different concrete type
pet: Pet = Dog()
pet = Cat()                  # rebind pointer to new adapter slot
pet.make_noise()             # "Meow"

# --- Static dispatch (monomorphized, zero-cost) ---

def greet_fast[T: Pet](pet: T) -> None:
    pet.make_noise()         # direct call, no vtable
```

### Zero-Allocation Stack Dispatch

Dynamic protocol variables use the existing pointer-local and slot hoisting
infrastructure. No heap allocation occurs.

**Local variable:**

```python
pet: Pet = Dog()
pet.make_noise()
```

Generated C++:

```cpp
// Adapter contains Dog by value, stack-allocated
__tpy_Pet_Adapter<Dog> __pet_slot_0;
// Pointer-local (same model as reassigned records)
__tpy_Pet_Base* pet = &__pet_slot_0;
// Virtual dispatch via pointer
pet->make_noise();
```

**Reassignment** creates additional slots, hoisted to the variable's declaration scope:

```python
pet: Pet = Dog()
if condition:
    pet = Cat()
pet.make_noise()
```

Generated C++:

```cpp
// Both slots hoisted to function scope (same as existing hoisted_vars)
__tpy_Pet_Adapter<Dog> __pet_slot_0;
__tpy_Pet_Adapter<Cat> __pet_slot_1;
__tpy_Pet_Base* pet = &__pet_slot_0;
if (condition) {
    pet = &__pet_slot_1;
}
pet->make_noise();  // dispatches to Dog or Cat
```

**Function parameters:**

```python
def greet(pet: Pet) -> None:
    pet.make_noise()

greet(my_dog)     # my_dog is Dog (concrete)
greet(erased_pet) # erased_pet is Pet (already erased)
```

Generated C++:

```cpp
void greet(__tpy_Pet_Base& pet) {
    pet.make_noise();
}

// Calling with concrete type -- temporary adapter on stack
Dog my_dog;
__tpy_Pet_Adapter<Dog> __tmp(my_dog);
greet(__tmp);

// Calling with erased value -- dereference pointer-local
greet(*erased_pet);
```

### Return Types

Returning a `@dynamic` protocol type is allowed when the value provably outlives the
caller -- i.e., when it refers to a global or a parameter (not a locally-constructed
value, since the stack adapter would be destroyed):

```python
global_dog: Pet = Dog()

def get_global_pet() -> Pet:
    return global_dog          # OK: global outlives caller

def echo_pet(pet: Pet) -> Pet:
    return pet                 # OK: parameter outlives caller

def make_pet() -> Pet:
    return Dog()               # ERROR: local adapter destroyed on return
```

For returning locally-constructed dynamic values, use explicit heap allocation:

```python
def make_pet() -> Box[Pet]:
    return Box(Dog())          # OK: heap-allocated, caller owns
```

### What Requires Explicit Wrapping

Dynamic protocol types cannot be used directly in contexts that require owning storage
with unknown lifetime. These require explicit `Box[P]` (or future `Rc[P]`):

| Context | Direct `Pet` | `Box[Pet]` |
|---------|-------------|------------|
| Local variable | OK (stack adapter) | OK (heap) |
| Function param | OK (reference) | OK |
| Return value | Only if source outlives caller | OK |
| Record field | No (size unknown) | OK |
| `list[Pet]` | No (elements need ownership) | `list[Box[Pet]]` |

### C++ Code Generation

For each `@dynamic` protocol, the compiler generates three artifacts:

**1. C++20 concept** (for `T: Proto` static dispatch -- same as non-dynamic):

```cpp
template<typename T>
concept Speakable = requires(T& t) {
    { t.speak() } -> std::same_as<void>;
};
```

**2. Abstract base class** (vtable target):

```cpp
struct __tpy_Speakable_Base {
    virtual void speak() = 0;
    virtual ~__tpy_Speakable_Base() = default;
};
```

**3. Adapter template** (bridges concrete types to the abstract base):

```cpp
template<Speakable T>
struct __tpy_Speakable_Adapter : __tpy_Speakable_Base {
    T inner;

    template<typename... Args>
    __tpy_Speakable_Adapter(Args&&... args) : inner(std::forward<Args>(args)...) {}

    void speak() override { inner.speak(); }
};
```

### Object Safety

Not all protocols can be `@dynamic`. The compiler validates at definition site:

- All methods must have concrete (non-generic) signatures
- No `Self` type (deferred -- `Self` support may be added later with restrictions)
- No static methods (no receiver to dispatch on)
- Marker protocols cannot be `@dynamic` (no methods to dispatch)

A non-object-safe protocol with `@dynamic` is a compile error.

### Conformance

Dynamic protocols use the same structural conformance as static protocols. A concrete
type conforms if it has all required methods with compatible signatures. No explicit
`extends` is needed:

```python
@dynamic
class Pet(Protocol):
    def make_noise(self) -> None: ...

class Dog:
    def make_noise(self) -> None:    # structurally conforms to Pet
        print("Woof")

dog: Pet = Dog()                     # OK: Dog has make_noise() -> None
```

This is consistent with non-dynamic protocols (e.g., `Sized` checks for `__len__`
without requiring `extends`). Explicit `extends` remains for marker protocols only.

### Future Extensions

- **`Box[P]`** -- heap-owned dynamic value for fields, containers, returns. Requires
  `Box[T]` implementation (Phase 6 of move semantics).
- **`Rc[P]`** -- shared-ownership dynamic value for reference-counted sharing.
- **`Self` type in `@dynamic`** -- may be supported with restrictions (e.g., `Self` in
  return position only, behind `Box`).
- **Generic `@dynamic` protocols** -- e.g., `@dynamic class Comparable(Protocol): def __lt__(self, other: Self) -> bool: ...`
  Requires Self support first.
- **Multiple protocol conformance** -- `pet: Pet & Drawable` for intersection types.

### Phase 9 Implementation Steps

1. **`@dynamic` decorator** (done) -- parser recognizes `@dynamic` on protocol classes,
   sema stores `is_dynamic` flag on ProtocolInfo, object-safety validation at definition
   site (no marker protocols, no generic protocols, no Self type)
2. **Abstract base + adapter codegen** (done) -- generate `__tpy_Proto_Base` and
   `__tpy_Proto_Adapter<T>` for each `@dynamic` protocol in the header
3. **Protocol-typed locals** -- `pet: Pet = Dog()` generates stack adapter slot +
   pointer-local (`__tpy_Pet_Base*`), with slot hoisting for reassignment
4. **Protocol-typed function params** -- `def f(pet: Pet)` generates
   `f(__tpy_Pet_Base& pet)`, with implicit adapter wrapping at call sites for concrete
   arguments
5. **Return types** -- allow returning protocol-typed values when provably long-lived
   (globals, parameters); error on returning local adapters
6. **`Box[P]` integration** (future) -- heap-allocated dynamic values for fields,
   containers, and unrestricted returns
