# Protocol System for TurboPython - Design Sketch

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

@dataclass
class ProtocolType(TpyType):
    """A structural protocol type."""
    name: str
    methods: dict[str, MethodSig]  # method_name -> signature
    type_params: list[str] = field(default_factory=list)

    def matches(self, concrete_type: TpyType, registry: TypeRegistry) -> bool:
        """Check if concrete_type structurally matches this protocol."""
        for method_name, sig in self.methods.items():
            # Look up method on concrete type
            method = lookup_type_method(concrete_type, method_name)
            if not method:
                return False
            if not sig.compatible_with(method):
                return False
        return True
```

## 4. Semantic analysis

```python
# tpyc/sema.py - in type checking

def _check_type_compatible(self, arg_type: TpyType, param_type: TpyType) -> bool:
    # Direct match
    if arg_type == param_type:
        return True

    # Protocol matching (structural)
    if isinstance(param_type, ProtocolType):
        return param_type.matches(arg_type, self.registry)

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
// In tpy_runtime.hpp - free functions for __len__

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

from tpyc.typesys import ProtocolType

# Sized protocol type for len() parameter
SIZED = ProtocolType("Sized")

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
class ProtocolDef:
    name: str
    methods: list[MethodSig]
    type_params: list[str] = field(default_factory=list)

@dataclass
class MethodSig:
    name: str
    params: list[ParamDef]
    returns: TpyType | str  # str for "Self" or type param refs

@dataclass
class ProtocolType(TpyType):
    protocol: ProtocolDef
    type_args: list[TpyType] = field(default_factory=list)  # for generic protocols
```

## 8. Built-in protocols

| Protocol | Methods | Used by |
|----------|---------|---------|
| `Sized` | `__len__` | `len()` |
| `Iterable[T]` | `__iter__` | `for` loops |
| `Iterator[T]` | `__next__` | iteration |
| `Hashable` | `__hash__` | dict keys |
| `SupportsInt` | `__int__` | `int()` coercion |
| `SupportsIndex` | `__index__` | indexing |

## 9. Compiler Traits (Data-Driven Type Behavior)

A key goal is enabling types like `StaticList` to be fully defined in source code, without hardcoded compiler references. Protocols provide the mechanism: types declare their capabilities, and the compiler queries these declarations instead of checking `isinstance(type, StaticListType)`.

### Built-in Trait Protocols

```python
from typing import Protocol

class HasLength(Protocol):
    """Types that support len()."""
    def __len__(self) -> Int32: ...

class Iterable(Protocol[T]):
    """Types that can be iterated over."""
    def __iter__(self) -> Iterator[T]: ...

class CoercibleToSpan(Protocol[T]):
    """Types that can implicitly convert to Span[T]."""
    def __span__(self) -> Span[T]: ...

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
if isinstance(arg_type, (StaticListType, ArrayType, ListType)):
    # generate len() call
```

The compiler queries the type's protocol conformance:

```python
# NEW: data-driven
if arg_type.conforms_to(HasLength):
    # generate len() call
```

### Type Declarations with Traits

Types declare their capabilities in their definitions:

```python
# tpy module source (or builtin module definition)
class StaticList(Generic[T, N]):
    """Fixed-capacity list with no heap allocation."""

    def __len__(self) -> Int32: ...         # → conforms to HasLength
    def __iter__(self) -> Iterator[T]: ...  # → conforms to Iterable[T]
    def __span__(self) -> Span[T]: ...      # → conforms to CoercibleToSpan[T]
    def __getitem__(self, i: Int32) -> T: ...
    def __setitem__(self, i: Int32, v: T) -> None: ...

    @classmethod
    def from_range(cls, items: Iterable[T]) -> StaticList[T, N]: ...
```

### Coercion via Protocols

Implicit coercions are driven by protocol conformance:

```python
def takes_span(s: Span[Int32]) -> None: ...

arr: Array[Int32, 3] = [1, 2, 3]
takes_span(arr)  # OK: Array conforms to CoercibleToSpan[Int32]

lst: list[Int32] = [1, 2, 3]
takes_span(lst)  # OK: list conforms to CoercibleToSpan[Int32]
```

Generated C++:
```cpp
void takes_span(std::span<const int32_t> s);

takes_span(arr.__span__());  // or implicit conversion operator
takes_span(lst.__span__());
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
   - Generic protocol definitions with type_params in ProtocolDef
   - ProtocolType with type_args for instantiated generic protocols
   - Type parameter substitution in protocol conformance checking
   - Parameterized C++20 concept generation (e.g., `tpy::Sequence<int32_t>`)
   - Indexing support for protocol-typed variables
   - Validation: bare generic protocols (e.g., `Sequence` without type args) are compile errors
4. **Phase 4**: `NativeIterable[T]` protocol ✅ **COMPLETE**
   - `NativeIterable[T]` in `tpy` module for C++ range-based for loops
   - Conformance via `is_iterable()` trait + element type matching (no method checking)
   - For-each loops work with protocol-typed parameters
   - C++20 concept `tpy::NativeIterable<ElemT>` using `std::ranges::begin/end`
   - **Note**: Python-compatible `Iterable[T]` and `Iterator[T]` deferred to Phase 8
5. **Phase 5**: `Self` type in protocols ✅ **COMPLETE**
   - `Self` type for protocol method signatures (parsed, substituted during conformance)
   - Recursive substitution for nested types like `Own[Self]`, `Ptr[Self]`
   - Validation: `Self` only allowed in protocol contexts
   - Regular (non-dunder) method calls on protocol-typed values
6. **Phase 6**: Compiler trait protocols (`CoercibleToSpan`, etc.)
7. ~~**Phase 7**: User-defined protocols~~ ✅ **COMPLETE** (moved to Phase 1)
8. **Phase 8**: Python-compatible `Iterable[T]` and `Iterator[T]` (future)
   - `Iterator[T]` with `__next__() -> T` + `raise StopIteration`
   - Compiler optimization: detect StopIteration pattern → efficient has_next codegen
   - `Iterable[T]` with `__iter__() -> Iterator[T]` for user-extensible iteration
   - Requires: either exceptions or `Optional[T]` type support

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
- Uses C++ `begin()`/`end()` under the hood
- All built-in containers (list, Array, Span, StaticList) conform
- Users **cannot** create custom NativeIterable types (requires C++ begin/end)
- Zero overhead - compiles to C++ range-based for

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
