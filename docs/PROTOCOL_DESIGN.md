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
concept Sized = requires(const T& t) {
    { t.__len__() } -> std::convertible_to<int32_t>;
};

// Function using protocol
template<Sized T>
int32_t process(const T& items) {
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

## 6. `len()` using protocols

```python
# tpyc/modules/builtins.py

SIZED = module.get_protocol("Sized")  # or reference by name

module.function("len", overloads=[
    MethodDef(
        params=[ParamDef("x", SIZED)],
        returns=INT32,
        cpp="{0}.__len__()",  # or dispatch marker
    ),
])
```

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

## 9. Implementation phases

1. **Phase 1**: Simple protocols (Sized) - no generics, no Self
2. **Phase 2**: Protocol matching in sema for function params
3. **Phase 3**: Generic protocols (`Iterable[T]`)
4. **Phase 4**: `Self` type in protocols
5. **Phase 5**: User-defined protocols
