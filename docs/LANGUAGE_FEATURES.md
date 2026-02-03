# TurboPython Language Features

Status legend:
- **Working** - Implemented now
- **Planned** - Will add
- **Open** - Could add with right design (notes on how)

---

## Design Philosophy

**Python-first**: Idiomatic Python should work out of the box. The everyday constructs used 99% of the time (`int`, `str`, `list`, `dict`, functions, classes) should just work without special annotations or restrictions.

**Opt-in performance constraints**: Performance restrictions are applied selectively, not globally. The 80/20 rule applies - most code doesn't need ultra-low-latency guarantees, only hot paths do. Write convenient Python everywhere, then tighten constraints where it matters.

**Pluggable backends**: The mapping from TurboPython to C++ should be configurable. Different projects have different needs:
- `Span[T]` → `std::span<const T>` or a custom span type
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
    def process(self, data: Span[Int32]) -> Int32:
        ...

# Function-level (overrides module/class default)
@noalloc
def critical_loop(data: Span[Int32]) -> Int32:
    ...

@alloc  # explicitly allow allocation in otherwise restricted context
def setup() -> Config:
    ...
```

Compiler flag can set project-wide default:
```bash
tpyc --default-profile=noalloc src/
```

---

## Pluggable C++ Backends (Open)

The mapping from TurboPython types/functions to C++ should be configurable via backend modules:

```python
# tpy.backend.default - ships with tpyc
Span[T]     → std::span<const T>
str         → std::string
print(...)  → std::cout << ...
list[T]     → std::vector<T>

# tpy.backend.trading - custom for HFT
Span[T]     → firm::span<T>
str         → firm::fixed_string<256>
print(...)  → LOG_INFO(...)
list[T]     → firm::static_vector<T, N>

# tpy.backend.embedded - custom for embedded
print(...)  → uart_printf(...)
list[T]     → etl::vector<T, N>
```

Usage:
```bash
tpyc --backend=trading src/order_handler.tp.py
```

This allows the same TurboPython source to target different environments without code changes.

---

## Type Categories

TurboPython distinguishes between **value types** and **object types**:

### Value Types
Small, immutable, passed by copy:
- `int`, `float`, `Int32`, `Int64`, `Float32`, `Float64`, `Bool`
- Small immutable structs (configurable threshold)
- `FixStr[N]` (fixed-size string)

### Object Types
Larger, mutable, passed by reference:
- Classes/structs (by default)
- `str` (dynamic string)
- `list`, `dict`, `set`
- Containers like `StaticList[T, N]`

### Parameter Passing Convention
For object types, `T` in a parameter implicitly means reference:
```python
def process(data: MyClass) -> None:  # data is passed by reference
    data.value = 42  # modifies original
```

### Container Element Semantics (Open Question)
How to differentiate:
- `StaticList[Point, 100]` - list contains Point values inline
- `StaticList[Ptr[Point], 100]` - list contains pointers to Points

Possible syntax options:
- Explicit: `StaticList[Point, 100]` vs `StaticList[Ptr[Point], 100]`
- Marker: `StaticList[Value[Point], 100]` vs `StaticList[Point, 100]`
- Default by size: small types inline, large types by pointer

---

## Types

### Numeric
- **Working**: `int` (Python's int → `tpy::BigInt` arbitrary precision using GMP)
- **Working**: `float` (Python's float → `double`, 64-bit IEEE 754)
- **Working**: `Int32`, `Bool`, `Char`
- **Planned**: `Int64`, `Int8`, `Int16`, `UInt8`, `UInt16`, `UInt32`, `UInt64`
- **Planned**: `Float32`

#### Mixed Arithmetic and Type Promotion (Working)

Operations between `Int32` and `int` (BigInt) follow Python's promotion rules - the result is always the wider type:

```python
x: Int32 = 5
y = 10          # int (BigInt)
z = x + y       # Result is int (BigInt), not Int32
```

| Operation | Result Type | Rationale |
|-----------|-------------|-----------|
| `Int32 + Int32` | `Int32` | Both operands same type, checked arithmetic |
| `Int32 + int` | `int` | Promotes to BigInt to avoid overflow |
| `int + Int32` | `int` | Promotes to BigInt to avoid overflow |
| `Int32 + literal` | `Int32` | Literal coerces to target type |

**Float promotion**: Any operation involving `float` promotes to `float`:

| Operation | Result Type | Rationale |
|-----------|-------------|-----------|
| `float + float` | `float` | Both operands same type |
| `float + int` | `float` | Float is wider than int |
| `int + float` | `float` | Float is wider than int |
| `float + Int32` | `float` | Float is wider than Int32 |
| `int / int` | `float` | True division always returns float |

For augmented assignment (`+=`, `-=`, `*=`, `/=`, etc.), the target type is preserved - the right-hand side is converted to match:
```python
total: Int32 = 0
big_value = 10  # int (BigInt)
total += big_value  # big_value.to_int32(), then Int32 addition
total *= big_value  # same: converts to Int32 first
```

This ensures `Int32` variables stay in the checked arithmetic domain. If the BigInt value is too large for Int32, the conversion panics at runtime.

### Strings
- **Working**: `str` type with string literals, comparison, iteration
- **Working**: `Char` type for single characters
- **Planned**: `FixStr[N]` - fixed-capacity string, stack allocated
- **Planned**: String concatenation and formatting

#### String Literal Assignment (Open)

Plain string literals should be lightweight:
```python
s = "abc"  # → const char* or std::string_view (backend-configurable)
```

#### F-string Formatting (Open)

F-strings behave differently based on context and profile:

```python
# Unrestricted mode - allocates std::string
s = f"x={x}"
# → std::string s = std::format("x={}", x);

# Restricted mode (@noalloc) - error or warning
s = f"x={x}"  # ERROR: f-string allocates in @noalloc context

# Fixed-size string - no allocation
buf: FixStr64 = f"x={x}"
# → formats into pre-sized buffer, truncates if needed

# Format string passthrough - zero overhead
def log(fs: FormatString) -> None: ...
log(f"x={x}")
# → log("x={}", x)  # format string + args passed separately
```

The `FormatString` type enables C++ templates that accept format strings directly, avoiding intermediate string allocation.

#### String Type Semantics (Open - deciding on design)

The key question: what C++ type does `str` map to?

**Option 1: `str` = `string_view` everywhere, `String` for owned**
```python
def process(name: str) -> String:  # view in, owned out
    return f"Hello, {name}"

class Config:
    name: String  # owned field
```
- Matches Rust model (`&str` vs `String`)
- Parameters are zero-copy by default
- Downside: breaks Python compatibility - `-> str` becomes `-> String`

**Option 2: `str` = `std::string` everywhere, `StrView` for borrowed**
```python
def process(name: StrView) -> str:  # explicit view in, owned out
    return f"Hello, {name}"

class Config:
    name: str  # owned field
```
- Python code returning `str` works unchanged
- Downside: parameters copy by default unless you use `StrView`

**Option 3: Context-dependent (matches Python semantics)**
```python
def process(name: str) -> str:  # param=view, return=owned
    return f"Hello, {name}"

class Config:
    name: str  # owned field
```
- Parameter: `std::string_view` (borrowed, like Python references)
- Return/field/local: `std::string` (owned, like Python objects)
- Use `StrView` for explicit non-owning fields when needed
- Most Python-compatible, matches how Python actually works at runtime
- Only "confusing" if thinking in C++ terms, but goal is Python-first

**Leaning toward Option 3**: it matches Python's actual semantics (parameters are borrowed, returns/fields are owned), keeps code Python-compatible, and `StrView` provides an escape hatch for explicit non-owning references.

### Containers
- **Working**: `list[T]` - dynamic list → `std::vector<T>` (with context-dependent inference)
- **Working**: `StaticList[T, N]` (fixed-capacity, no allocation)
- **Working**: Array literals `[1, 2, 3]` → `std::array<T, N>` or `std::vector<T>` (context-dependent)
- **Working**: `Array[T, N]` - fixed-size array with explicit type annotation
- **Working**: `Span[T]` - non-owning read-only view into contiguous memory → `std::span<const T>`
- **Planned**: `Tuple[T1, T2, ...]`
- **Planned**: `dict` - hash map (requires allocation)
- **Planned**: `set` - hash set (requires allocation)
- **Open**: Bounded variants: `BoundedList[T, N]`, `BoundedDict[K, V, N]`

#### Array Literals and Span (Working)

Array literals compile to fixed-size stack arrays with inferred size:
```python
nums = [1, 2, 3]                    # type inferred as Array[Int32, 3]
arr: Array[Int32, 3] = [10, 20, 30] # explicit type annotation
```

#### List Literal Inference (Working)

Context-dependent inference for Python-first semantics:

| Context | Inferred Type | C++ Type | Rationale |
|---------|---------------|----------|-----------|
| Module-level (global) | `list` | `std::vector` | Cross-module safety - other modules might import and mutate |
| Function local, no mutation | `Array` | `std::array` | Stack performance, no heap allocation |
| Function local + `.append()`/`.pop()`/etc | `list` | `std::vector` | Explicit mutation requires growable container |
| Function local, passed to `list[T]` param | `list` | `std::vector` | Callee expects mutable list |
| Function local, passed to `Span[T]` param | `Array` | `std::array` | Span is read-only view, no mutation possible |
| Explicit annotation `x: Array[T, N]` | `Array` | `std::array` | User opted into fixed size |
| Explicit annotation `x: list[T]` | `list` | `std::vector` | User opted into dynamic list |

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

def reader(items: Span[int]) -> int:
    return items[0]

def caller2():
    data = [1, 2, 3]       # → std::array<BigInt, 3> (Span is read-only)
    return reader(data)
```

Note: Passing literals (`[]`, `[1,2,3]`) or constructors (`list()`) directly to functions expecting mutable reference parameters works - the compiler generates temporary variables automatically.

`Span[T]` is a non-owning read-only view that accepts any contiguous memory:
```python
def sum_values(values: Span[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(values):
        total += values[i]
        i += 1
    return total

# All of these work:
print(sum_values([1, 2, 3, 4, 5]))  # array literal passed directly
nums = [10, 20, 30]
print(sum_values(nums))             # array variable
arr: Array[Int32, 3] = [100, 200, 300]
print(sum_values(arr))              # explicit Array type

# StaticList also converts to Span:
items: StaticList[Int32, 4] = StaticList[Int32, 4]()
items.append(1000)
items.append(2000)
print(sum_values(items))
```

Generated C++:
```cpp
int32_t sum_values(std::span<const int32_t> values) {
    int32_t total = 0;
    int32_t i = 0;
    while (i < static_cast<int32_t>(values.size())) {
        total += values[i];
        i += 1;
    }
    return total;
}

// Array literal passed directly creates temporary std::array
std::printf("%d\n", sum_values(std::array<int32_t, 5>{1, 2, 3, 4, 5}));

// Array variable
std::array<int32_t, 3> nums = {10, 20, 30};
sum_values(nums);  // implicit conversion to span

// StaticList requires explicit span construction
std::printf("%d\n", sum_values(std::span(items.data(), items.size())));
```

Key features:
- Uses `std::span<const T>` (read-only) to allow conversion from temporaries
- Requires C++23 (`-std=c++23`)
- Standard Python `len()` and `[]` indexing work for both Array and Span
- Zero-allocation passing of fixed-size arrays to functions that work with any size

### Pointers/References
- **Working**: `Ptr[T]` → `T*`
- **Working**: `ConstPtr[T]` → `const T*`
- **Working**: `Own[T]` → `T` (ownership transfer for return values)
- **Planned**: `Ref[T]` → `T&` (explicit reference)
- **Planned**: `ConstRef[T]` → `const T&`

#### Pointer Coercions (Working)

Implicit conversions between records and pointers with safety checks:

| From | To | Constraints | Generated C++ |
|------|-----|-------------|---------------|
| `T` (record) | `Ptr[T]` | Mutable lvalue, not in return | `&expr` |
| `T` (record) | `ConstPtr[T]` | Lvalue, not in return | `&expr` |
| `Ptr[T]` | `T` | Null-checked at runtime | `tpy::deref_ptr(expr)` |
| `Ptr[T]` | `ConstPtr[T]` | - | (implicit) |

**Safety rules:**
- Taking address requires an lvalue (variable, field, or subscript) - temporaries rejected
- Return statements cannot convert local records to pointers (dangling pointer prevention)
- Span/str elements cannot convert to `Ptr[T]` (read-only source)
- `Ptr[T]` → `T` includes runtime null check that panics if null

#### Owned Return Values (Working)

Object types are normally returned by reference (`T&`) to avoid hidden copies. But this creates a problem when returning newly constructed objects:

```python
def create_point() -> Point:
    p: Point = Point()
    p.x = 10
    p.y = 20
    return p  # ERROR: dangling reference to local variable
```

The compiler detects this as a dangling reference error. Use `Own[T]` to indicate the function returns a newly constructed object by value (with move semantics):

```python
from tpy import Int32, Own, copy

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
def make_point(x: Int32, y: Int32) -> Own[Point]:
    return Point(x, y)  # OK: constructor call is an rvalue
```

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
- `Own[T]` → `T` in C++ (by value, no reference)
- Relies on C++ move semantics and RVO/NRVO for efficiency
- Returning an lvalue (variable, field access) requires `copy()` to make the intent explicit
- Returning an rvalue (constructor, function call) is OK without `copy()`
- `Own[T]` coerces to `T` when receiving the value

#### Owned Parameters (Working)

`Own[T]` can also be used for parameter types to receive values by-value:

```python
def take_point(p: Own[Point]) -> Int32:
    # p is received by value (Point p in C++)
    return p.x + p.y  # Field access on Own[T] works

def main():
    # Pass Own[Point] return directly to Own[Point] param
    result: Int32 = take_point(create_point(10, 20))
    print(result)
```

**Important restriction**: You cannot pass an `Own[T]` return value directly to a function expecting `T` (passed by reference):

```python
def process(p: Point) -> None:  # p is Point& (reference)
    print(p.x)

def main():
    process(create_point(10, 20))  # ERROR: Own[Point] cannot bind to Point&

    # Solution: store in a variable first
    pt: Point = create_point(10, 20)
    process(pt)  # OK: pt is an lvalue
```

This is because functions returning `Own[T]` create temporaries (rvalues) that cannot bind to non-const references.

**Note**: `Own[T]` is only valid for function parameters and return types, not for variable declarations. Use `T` for variables.

### User-Defined
- **Working**: Classes → C++ structs
- **Planned**: Enums → `enum class`
- **Open**: Inheritance → could support simple cases

### Protocols (Partial)

Protocols enable structural subtyping (compile-time duck typing). A type matches a protocol if it has the required methods, without explicit inheritance. This is Python's `typing.Protocol` (PEP 544), similar to Go interfaces or Rust traits.

#### Working: Built-in `Sized` Protocol

The built-in `Sized` protocol is available from the `typing` module:

```python
from typing import Sized
from tpy import Int32

def count(items: Sized) -> Int32:
    return len(items)

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(count(nums))  # Works! Prints 3
```

Generated C++:
```cpp
template<tpy::Sized T_items>
int32_t count(const T_items& items) {
    return tpy::__len__(items);
}
```

The `tpy::Sized` concept uses the `tpy::__len__()` free function, which has overloads for `std::vector`, `std::array`, `std::span`, `std::string_view`, and `StaticList`, plus a default template for user types with `__len__()` method.

#### Working: User-Defined Protocols

You can define your own protocols, but **prefer using existing CPython protocols** (like `Sized` from `typing`) when possible. This ensures compatibility with both CPython and TurboPython, and avoids duplicating standard definitions.

Example of a custom protocol:

```python
from typing import Protocol
from tpy import Int32

class Measurable(Protocol):
    def __len__(self) -> Int32: ...

def count(items: Measurable) -> Int32:
    return len(items)
```

Generated C++:
```cpp
template<typename T>
concept Measurable = requires(T& t) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
};

template<Measurable T_items>
int32_t count(T_items& items) {
    return tpy::__len__(items);
}
```

#### Working: Self Type in Protocols

The `Self` type can be used in protocol method signatures to refer to the implementing type. This enables patterns where the return type or parameter type should match the concrete type:

```python
from typing import Protocol, Self
from tpy import Int32

class Addable(Protocol):
    def __add__(self, other: Self) -> Self: ...

def add_values(x: Addable, y: Addable) -> None:
    result = x + y
    print(result)

a: Int32 = 21
b: Int32 = 21
add_values(a, b)  # Int32 conforms: __add__(Int32) -> Int32
```

Generated C++:
```cpp
template<typename T>
concept Addable = requires(T& t) {
    { t + std::declval<T>() } -> std::convertible_to<T>;
};

template<Addable T_x, Addable T_y>
void add_values(T_x& x, T_y& y) {
    auto result = (x + y);
    std::cout << result << "\n";
}
```

When checking protocol conformance, `Self` is substituted with the actual type being checked. For example, `Int32` conforms to `Addable` because `Int32.__add__(Int32) -> Int32` matches the protocol signature.

`Self` can also be used inside wrapper types like `Own[Self]`:

```python
from __future__ import annotations
from typing import Protocol, Self
from tpy import Int32, Own

class Addable(Protocol):
    def __add__(self, other: Self) -> Own[Self]: ...

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: Point) -> Own[Point]:
        return Point(self.x + other.x, self.y + other.y)
```

`Point` conforms to `Addable` because `Point.__add__(Point) -> Own[Point]` matches `__add__(Self) -> Own[Self]` after substituting `Self` with `Point`.

**Note**: `Self` can only be used within protocol method signatures. Using `Self` in regular functions or class methods produces an error.

#### Working: Regular Method Calls on Protocol Types

You can call any method defined in a protocol on a protocol-typed value:

```python
from __future__ import annotations
from typing import Protocol, Self
from tpy import Int32, Own

class Duplicable(Protocol):
    def duplicate(self) -> Own[Self]: ...

class Value:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def duplicate(self) -> Own[Value]:
        return Value(self.x * 2)

def double_it(d: Duplicable) -> None:
    result = d.duplicate()  # OK: calls method via protocol

double_it(Value(21))  # OK: temporaries work via auto-generated temp vars
```

**Note on temporaries**: When passing a constructor expression (like `Value(21)`) to a protocol-typed parameter, the compiler generates a temporary variable. This is necessary because protocol parameters use mutable references (`T&`) in C++, which cannot bind directly to temporaries.

#### Working: Generic Protocol `Sequence[T]`

The `Sequence[T]` generic protocol is available for types that support `len()` and indexing:

```python
from typing import Sequence
from tpy import Int32, Array

def first(items: Sequence[Int32]) -> Int32:
    return items[0]

def sum_all(items: Sequence[Int32]) -> Int32:
    total: Int32 = 0
    i: Int32 = 0
    while i < len(items):
        total += items[i]
        i += 1
    return total

def main() -> None:
    nums: list[Int32] = [1, 2, 3]
    print(sum_all(nums))  # 6

    arr: Array[Int32, 3] = [10, 20, 30]
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
def bad(items: Sequence) -> Int32:  # ERROR: Generic protocol 'Sequence' requires type arguments
    return len(items)
```

**Conforming types**:
- `list[T]`, `Array[T, N]`, `Span[T]`, `StaticList[T, N]` - built-in containers
- `str` conforms to `Sequence[Char]` - strings are sequences of characters
- User records with `__len__` and `__getitem__` methods (see below)

**Temporaries**: Passing temporaries (list literals, constructor calls) to protocol-typed parameters works. The compiler generates temporary variables automatically since protocol parameters use mutable references (`T&`) in C++.

**User records as Sequence**: Records with `__len__` and `__getitem__` automatically conform to `Sequence[T]`. The compiler generates `operator[]` from `__getitem__`:
```python
class IntWrapper:
    data: list[Int32]

    def __len__(self) -> Int32:
        return len(self.data)

    def __getitem__(self, index: Int32) -> Int32:
        return self.data[index]

def sum_seq(s: Sequence[Int32]) -> Int32:
    # Works with IntWrapper, list, Array, etc.
    ...

wrapper: IntWrapper = IntWrapper(nums)
sum_seq(wrapper)  # OK - IntWrapper conforms to Sequence[Int32]
```

**Mixing protocols**: Generic and non-generic protocols can be used together:
```python
from typing import Sized, Sequence

def process(items: Sequence[Int32], container: Sized) -> Int32:
    return items[0] + len(container)
```

#### Planned: `MutableSequence[T]` (read/write sequences)

`Sequence[T]` currently only requires `__len__` and `__getitem__`. For full mutable sequence support:

Planned design:
- Introduce `MutableSequence[T]` that models read/write access.
- `MutableSequence[T]` will require additional methods beyond `__len__` and `__getitem__` (likely `__setitem__`, and possibly `append`/`pop` for full Python parity).

Current limitations:
- **User records**: we generate `operator[]` from `__getitem__`, but it currently returns by value. This prevents mutating elements through `items[i].method()` for user-defined record containers. Addressing this likely requires reference-capable `__getitem__` or a dedicated mutation API in the type system.

#### Working: `NativeIterable[T]` (C++ range-for iteration)

`NativeIterable[T]` is a **marker protocol** for types that support C++ range-based for loops. It's defined in the `tpy` module (not `typing`) because it maps to C++ `begin()`/`end()` iteration rather than Python's `__iter__`/`__next__` protocol.

```python
from tpy import Int32, NativeIterable

def sum_all(items: NativeIterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

# All built-in containers work:
nums: list[Int32] = [1, 2, 3]
print(sum_all(nums))  # 6

arr: Array[Int32, 3] = [10, 20, 30]
print(sum_all(arr))   # 60
```

**Key characteristics**:
- **Marker protocol**: Types declare conformance via `extends`, no methods required
- **Built-in conformance**: `list[T]`, `Array[T, N]`, `Span[T]`, `StaticList[T, N]` extend `NativeIterable[T]`
- **str**: Extends `NativeIterable[Char]` (iterates over characters)
- **Zero overhead**: Compiles to C++ range-based for loops
- **Not user-extensible**: Requires C++ `begin()`/`end()` support

**Difference from future `Iterable[T]`**: `NativeIterable[T]` is TurboPython-specific and uses C++ iteration. The future `Iterable[T]` (in `typing` module) will use Python's `__iter__()` → `Iterator[T]` protocol, allowing user-defined iterable types.

See [docs/PROTOCOL_DESIGN.md](PROTOCOL_DESIGN.md) for the full design rationale.

#### Planned: `Iterable[T]` and `Iterator[T]`

Python-compatible iterating protocols are not yet supported:

```python
# NOT YET WORKING - requires exception support
class Iterable(Protocol[T]):
    def __iter__(self) -> Iterator[T]: ...
```

#### Working: `NativeContiguous[T]` (Span coercion)

`NativeContiguous[T]` is a **marker protocol** for types with elements laid out contiguously in memory. Types extending `NativeContiguous[T]` can be implicitly coerced to `Span[T]`.

```python
from tpy import Int32, Span, Array, StaticList

def sum_span(values: Span[Int32]) -> Int32:
    total: Int32 = 0
    for v in values:
        total += v
    return total

# All these work - Array, StaticList, list extend NativeContiguous[T]
arr: Array[Int32, 3] = [1, 2, 3]
sum_span(arr)  # OK

sl: StaticList[Int32, 8] = StaticList[Int32, 8]()
sum_span(sl)  # OK

lst: list[Int32] = [4, 5, 6]
sum_span(lst)  # OK

# str does NOT extend NativeContiguous - this is an error
# s: str = "hello"
# takes_span(s)  # ERROR: str does not extend NativeContiguous
```

**Key points:**
- **Built-in conformance**: `list[T]`, `Array[T, N]`, `Span[T]`, `StaticList[T, N]` extend `NativeContiguous[T]`
- **str excluded**: `str` iterates over `Char` but doesn't extend `NativeContiguous` (design choice)
- **Zero overhead**: Uses C++ `std::span` implicit construction from contiguous ranges
- **C++ concept**: Maps to `std::ranges::contiguous_range`

#### Compiler Traits Summary

Protocols serve as **compiler traits**—letting the compiler discover type capabilities without hardcoded type checks:

- `len(x)` works on any type conforming to `Sized` ✓ (working)
- `Sequence[T]` for types supporting `len()` and indexing ✓ (working)
- `for` loops work on `NativeIterable[T]`-typed parameters ✓ (working)
- Implicit coercion to `Span[T]` works on types extending `NativeContiguous[T]` ✓ (working)
- `for` loops work on `Iterable[T]`-typed parameters (planned - Python-compatible)

See [docs/PROTOCOL_DESIGN.md](PROTOCOL_DESIGN.md) for the full design, including implementation phases and C++ codegen strategies.

### Union/Optional
- **Open**: `T | None` → `std::optional<T>` or pointer
- **Open**: `T | U` → templates with `if constexpr`, or overloads

---

## Operators

### Arithmetic
- **Working**: `+`, `-`, `*`, `//`, `%`, `/`, unary `-`
- **Working**: `**` (power) for `int`, `Int32`, and `float`
- **Planned**: `**` with negative integer exponent (e.g., `2 ** -3`) - use `2.0 ** -3` instead

### Comparison
- **Working**: `==`, `!=`, `<`, `<=`, `>`, `>=`

### Membership
- **Working**: `in`, `not in` (for list, Array, Span, str)

### Logical
- **Working**: `and`, `or`, `not`

### Bitwise
- **Working**: `&`, `|`, `^`, `~`, `<<`, `>>`

### Assignment
- **Working**: `=`, `+=`, `-=`, `*=`, `/=`, `//=`, `%=`, `&=`, `|=`, `^=`, `<<=`, `>>=`
  - Augmented assignment works on variables, fields, and subscripts (`items[i] += 1`)

---

## Control Flow

### Conditionals
- **Working**: `if`, `elif`, `else`
- **Open**: Ternary `x if cond else y` → C++ ternary

### Loops
- **Working**: `while`
- **Working**: `for i in range(n)`, `for i in range(start, end)`
- **Working**: `for item in container` (for-each over list, Array, Span, str)
- **Working**: `break`, `continue`
- **Planned**: `for i in range(start, end, step)`
- **Open**: `for/else`, `while/else` → flag variable pattern

### Other
- **Working**: `return`, `pass`
- **Open**: `match`/`case` → switch or if/else chain

---

## Functions

### Definition
- **Working**: Typed parameters and return types
- **Planned**: Default parameter values
- **Open**: `*args` → variadic templates or fixed overloads
- **Open**: `**kwargs` → if keys known at compile time

### Decorators
- **Working**: `@noalloc` (parsed and recognized, enforcement planned)
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

**Explicit Type Arguments**: When inference isn't possible or you want explicit control:

```python
# Explicit type argument
result: Int32 = first[Int32](nums)

# Required when inference would be ambiguous
def identity[T](x: T) -> T:
    return x

# Type annotation provides hint for inference
y: Int32 = identity(42)  # T inferred as Int32 from annotation
```

Generated C++ (template functions):
```cpp
template<typename T>
tpy::return_val_or_ref_t<T> first(std::vector<T>& items) {
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

**Limitations**:
- Type parameter bounds (`def foo[T: Comparable](x: T)`) not yet supported
- Generic methods on classes (methods with their own type params) not yet supported

---

## Classes

### Definition
- **Working**: Typed fields
- **Working**: `__init__`
- **Working**: Instance methods
- **Working**: Generic classes (Python 3.12+ syntax)
- **Planned**: `@staticmethod` → free functions or static methods
- **Open**: `@classmethod` → if use case is clear
- **Open**: `@property` → getter/setter methods
- **Open**: Inheritance → composition, or actual inheritance for simple cases

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
box: Box[Int32] = Box[Int32](42)  # Explicit
box2 = Box(42)                    # Inferred as Box[int] from argument
pair = Pair(1, "hello")           # Inferred as Pair[int, str]

# Generic methods use substituted types
print(box.get())  # Returns Int32
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
Pair<std::string_view, int32_t> pair{"hello", 100};
```

**Type Inference**:
- Type arguments can be inferred from constructor arguments: `Box(42)` → `Box[int]`
- Inference works when all type parameters can be determined from arguments
- If inference fails, explicit type arguments are required
- When mixing int literals with `Int32`, inference upgrades to `Int32`: `Same(1, x: Int32)` → `Same[Int32]`
- Supports inference through wrapper types: `Ptr[T]`, `ConstPtr[T]`, `Own[T]`, `list[T]`
- `Ptr[T]` arguments match `ConstPtr[T]` parameters (follows coercion rules)

**Limitations**:
- Type parameter bounds (`class SortedList[T: Comparable]`) not yet supported
- Integer type parameters (`class FixedStack[T, N: int]`) not yet supported

### Special Methods
- **Working**: `__init__`
- **Planned**: `__del__` (destructor)
- **Open**: `__eq__`, `__lt__` → `operator==`, `operator<`
- **Open**: `__getitem__`, `__setitem__` → `operator[]`
- **Open**: `__str__` → if we have string type
- **Open**: `__enter__`, `__exit__` → RAII wrapper

---

## Built-in Functions

- **Working**: `print()`, `len()`, `range()`, `chr()`, `copy()`
- **Working**: List methods: `append()`, `pop()`, `insert()`, `remove()`, `clear()`, `extend()`
  - **Note**: `remove(value)` silently does nothing when value not found (Python raises `ValueError`)
- **Working**: StaticList methods: `append()`, `pop()`, `clear()`, `push_empty()`, `get_mut()`
  - Use subscript notation `items[i]` for element access (same as list)
  - Initializer list constructor: `StaticList[Int32, 8]([1, 2, 3])`
  - Fill constructor via list repetition: `StaticList[Int32, 64]([0]*64)`
  - Multi-element list repetition: `StaticList[Int32, 6]([1, 2]*3)` → `[1, 2, 1, 2, 1, 2]`
- **Working**: List repetition: `[element] * N` and `[elements...] * N`
  - Single-element: uses efficient fill constructor
  - Multi-element: uses `tpy::repeat_range` to repeat the sequence N times
- **Working**: Negative indexing for list, StaticList, Array, Span: `items[-1]` (last element)
- **Planned**: `abs()`, `min()`, `max()`
- **Planned**: List slicing: `items[1:3]`
- **Open**: `isinstance()` → compile-time type check / type narrowing
- **Open**: `type()` → compile-time type info
- **Working**: `list()` → empty list constructor (requires type annotation) and `list(iterable)` with type inference
- **Working**: `int(float)` → truncates toward zero, panics on NaN/infinity
- **Working**: `float(int)`, `float(Int32)` → converts to float
- **Open**: `str()`, `int(str)` → string conversion functions (see below)
- **Open**: `enumerate()` → compile-time transform
- **Open**: `zip()` → compile-time transform for fixed iterables

#### Type Conversion Functions

**`list()` (Working)**:

```python
# Empty list with type annotation
x: list[int] = list()        # type from annotation
x: list[int] = []            # same - empty literal infers from annotation

# Bare list() with no context → error with helpful message
x = list()                   # error: list() requires type annotation
x = []                       # error: Empty array literal requires explicit type annotation

# List from iterable - type inferred from element type
x = list([1, 2, 3])          # → list[int], infers element type from literal
y = list(some_array)         # → list[T], infers from array's element type
z = list(other_list)         # → list[T], copies the list
```

Generated C++:
```cpp
// list([1, 2, 3]) - from literal
std::vector<tpy::BigInt> x({1, 2, 3});

// list(array) - from other container
std::vector<T> y(arr.begin(), arr.end());
```

**`int()` (Working)**:
```python
x = int(3.14)     # → 3 (truncates toward zero)
y = int(-2.7)     # → -2 (truncates toward zero)
z = int(1e100)    # → large BigInt (works correctly)
# int(float("nan"))  # panics: cannot convert float NaN to integer
# int(float("inf"))  # panics: cannot convert float infinity to integer

# From string
a = int("42")     # → 42
b = int("-123")   # → -123
c = int("  99  ") # → 99 (whitespace trimmed)
# int("abc")      # panics: invalid literal for int()
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
b = bool(Int32(0))  # → False
b = bool(Int32(1))  # → True
```

**`copy()` (Working)**:

Used for explicit ownership transfer when returning lvalues (variables, field accesses) as `Own[T]`:

```python
from tpy import Int32, Own, copy

class Box:
    value: Int32

def take_value(b: Box) -> Own[Int32]:
    return copy(b.value)  # Explicit copy required for lvalue

def make_box() -> Own[Box]:
    return Box()  # No copy needed - constructor is an rvalue
```

The `copy()` function:
- Takes exactly one argument of any type `T`
- Returns `Own[T]` (owned value)
- Required when returning lvalues (variables, field accesses, subscript) as `Own[T]`
- Not required when returning rvalues (constructor calls, function calls)
- In generated C++, `copy(x)` simply evaluates to `x` (the `Own[T]` return type handles the by-value semantics)
- In CPython tests, uses `deepcopy` to match C++ by-value semantics (containers copy all elements)

**`str()` (Partial - with caveats)**:
```python
s = str()         # → "" (empty string)
s = str("hello")  # → "hello" (identity)
s = str(True)     # → "True"
s = str(False)    # → "False"
s = str(c)        # → single-char string from Char

# Numeric conversions (inline use only!)
print(str(42))    # → "42" - safe inline
print(str(3.14))  # → "3.14" - safe inline
```

**⚠️ Known UAF risk**: `str(numeric)` returns `std::string` but `str` maps to `std::string_view`. Storing in a variable creates a dangling reference:
```python
s: str = str(42)  # UNSAFE - s points to destroyed temporary
print(s)          # undefined behavior
```
Safe for inline use only (e.g., `print(str(42))`). Proper fix requires ownership tracking.

---

## Modules & Imports

- **Working**: `from tpy import ...` (built-in types like `Int32`, `Span`, `StaticList`)
- **Working**: `import time` and `from time import time`
- **Working**: Import aliases: `from time import time as get_time`, `from tpy import Int32 as I32`
  - **Note**: Aliases work for function calls but not yet for type annotations (`x: I32` won't resolve)
- **Working**: `import sys` - system module with `sys.argv`
- **Working**: `import math` - mathematical functions
- **Working**: Namespace wrapping for modules (each module gets its own C++ namespace)
- **Planned**: Module-level aliases: `import time as _time`
- **Planned**: Multi-file projects (user-defined modules)
- **Open**: Importing additional Python stdlib subsets that can be statically compiled

### Standard Library Modules

#### `time` module (Working)

```python
import time
# or
from time import time, sleep

# time.time() - returns seconds since epoch as float
t = time.time()  # → double

# time.sleep(seconds) - suspend execution
time.sleep(0.5)  # sleep for 500ms
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

## Variables & Scope

- **Working**: Local variables (inferred and annotated)
- **Working**: Global variables (typed)
- **Planned**: Type inference from function returns
- **Open**: `global` → explicit global access
- **Open**: `:=` walrus → if useful pattern emerges

---

## Expressions

- **Working**: Binary/unary ops, calls, field access
- **Open**: List comprehensions → unrolled loops for fixed size
- **Open**: Dict comprehensions → if dict type exists
- **Open**: Lambda → anonymous struct with `operator()` or inline
- **Open**: Slice `[start:end]` → view type

---

## Error Handling

- **Working**: Runtime panics (bounds checks → abort)
- **Open**: `assert` → conditional panic or compile-time check
- **Open**: `try`/`except` → error codes, `std::expected`, or limited exceptions
- **Open**: `raise` → if exception model chosen

---

## Concurrency

- **Open**: `async`/`await` → coroutines or state machines

---

## Generators

- **Open**: `yield` → generator as state machine class
- **Open**: Generator expressions → lazy iterators with known bounds
- Could be zero-alloc if state machine is stack-allocated

---

## Interactive REPL

- **Working**: `tpyc --repl` launches an interactive session
- Supports function and class definitions that persist across inputs
- Expressions are evaluated and printed automatically
- Multi-line input with automatic continuation detection

---

## Lambda / Closures

- **Open**: `lambda x: x + 1` → inline or functor class
- **Open**: Functions accepting lambdas → templates for efficiency
  ```python
  def map_values(items: List[T], fn: Callable[[T], T]) -> None:
      # fn could be a template parameter, inlined at compile time
  ```
- **Open**: Closures capturing variables → struct with captured state

---

## Metaprogramming / Macros

- **Open**: Compile-time code execution via decorators/annotations
  ```python
  @derive(Eq, Hash)  # generates __eq__ and __hash__ at compile time
  class Point:
      x: Int32
      y: Int32
  ```
- **Open**: Annotations as compile-time hooks
  ```python
  @serialize("json")  # generates serialization code
  class Config:
      name: str
      value: Int32
  ```
- **Open**: Macro system running Python during C++ generation
  - Inspect types, generate methods, transform AST
  - Similar to Rust's proc_macro or C++ template metaprogramming

### Compile-Time Hooks (Extensible Metaprogramming)

The compiler shouldn't hardcode special classes like `Model`. Instead, classes can define compile-time hooks that the compiler calls during generation:

```python
# tpy/model.tp.py - library code, not compiler magic
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
    price: Float64
    quantity: Int32
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
    price: Float64
    quantity: Int32
    side: Side  # enum
    timestamp: Int64 = Field(default_factory=now)

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

## Open Questions

1. **Allocation control ergonomics**: `@noalloc` vs `@alloc` vs module-level vs compiler flag?
2. **Container element storage**: How to spell "list of values" vs "list of pointers"?
3. **String semantics**: When does `str` allocate vs use SSO?
4. **Inheritance**: Support simple cases or always composition?
5. **Exceptions**: Error codes, `std::expected`, or actual exceptions?
6. **Lambda efficiency**: Always template? Configurable? Type-erased fallback?
7. **Macro system scope**: How much compile-time Python execution? Safety limits?
