# TurboPython Language Features

Status legend:
- **Working** - Implemented now
- **Planned** - Will add
- **Open** - Could add with right design (notes on how)

---

## Design Philosophy

**Performance**: Low-latency compiled output. Most code uses standard Python constructs freely, while hot paths can opt into stricter constraints (e.g. `@noalloc`). No garbage collection, no interpreter overhead.

**Idiomatic Python**: Standard Python should work out of the box. The everyday constructs (`int`, `str`, `list`, `dict`, functions, classes) should just work with minimal restrictions — type annotations on function signatures are the main requirement.

**Tooling-friendly**: Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins or language server protocols. Development uses the same tools as regular Python.

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
- `int`, `float`, `Int32`, `Int64`, `Float32`, `Float64`, `bool`
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
- **Working**: `Int32`, `bool`, `Char`
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

#### Pointer Constructors (Working)

Explicit constructors for `Ptr[T]` and `ConstPtr[T]`, as an alternative to implicit coercions:

```python
from tpy import Ptr, ConstPtr, Int32

def test() -> None:
    # Null pointers
    p: Ptr[None] = Ptr[None]()        # → nullptr
    q: Ptr[Int32] = Ptr[Int32]()      # → nullptr (typed null)

    # Address-of with explicit type
    x: Int32 = Int32(42)
    px: Ptr[Int32] = Ptr[Int32](x)    # → &x

    # Address-of with type inference
    py: Ptr[Int32] = Ptr(x)           # → &x (infers Ptr[Int32])
    cp: ConstPtr[Int32] = ConstPtr(x) # → &x (infers ConstPtr[Int32])
```

**Safety rules:**
- Argument must be an lvalue (`Ptr(Point(1,2))` rejected — temporary)
- `Ptr` requires a mutable lvalue; `ConstPtr` accepts any lvalue
- `Ptr[None](arg)` rejected — void pointer with argument makes no sense
- Dangling detection works through pointer constructors and intermediate variables:

```python
def bad() -> Ptr[Int32]:
    x: Int32 = Int32(1)
    return Ptr(x)          # ERROR: returned pointer would dangle

def also_bad() -> Ptr[Int32]:
    x: Int32 = Int32(1)
    p: Ptr[Int32] = Ptr(x)
    return p                # ERROR: returned pointer would dangle

def ok(x: Int32) -> Ptr[Int32]:
    return Ptr(x)           # OK: x is a parameter
```

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

Container storage methods (`append`, `insert`, `__setitem__`) use `Own[T]` parameters to trigger the same warning:

```python
items: list[Point] = []
p: Point = Point()
items.append(p)           # WARNING: copies Point into owned storage
items.append(copy(p))     # OK: explicit copy
items.append(Point())     # OK: rvalue, no existing owner
items[0] = p              # WARNING: copies Point into container
items.insert(0, p)        # WARNING: copies Point into owned storage
```

No warning is emitted for:
- **Value types** (Int32, bool, str, etc.) — copy-vs-share is unobservable
- **Rvalues** (constructor calls, function results) — no existing owner
- **`copy()` wrapped** — intent already explicit

#### Scope Escape Detection (Working)

The compiler detects when a pointer to a local variable might outlive its storage. This happens when a variable declared in an inner scope (e.g., loop body) is assigned to a variable in an outer scope.

For most cases, the compiler **hoists** the inner variable's storage slot to function scope and emits a warning. The generated code is safe — the hoisted slot lives as long as the function, so the outer variable never dangles:

```python
def example() -> None:
    saved: Point = Point(0, 0)
    for i in range(10):
        p: Point = Point(i, i)
        saved = p        # WARNING: hoisted to function scope (safe)
        saved = copy(p)  # OK: copy() creates independent storage
        saved = Point()  # OK: rvalue has fresh storage
    print(saved.x)       # Works: p's storage was hoisted
```

For `for-each` variables, hoisting doesn't help because the variable is a reference into a container — the reference itself would dangle. These remain **hard errors**:

```python
def bad() -> None:
    saved: Point = Point(0, 0)
    for p in make_points():
        saved = p        # ERROR: for-each var references container storage
        saved = copy(p)  # OK: copy() creates independent storage
```

Detection uses scope depth comparison — each scope has a numeric depth, and variables track the depth where they were first declared. When assigning an lvalue to a shallower-depth target, the compiler checks whether hoisting is possible (regular loop variables) or not (for-each variables).

Use `copy()` to silence warnings or fix errors — it creates an independent value that the outer variable can safely own.

#### Definite-Assignment Analysis (Working)

The compiler verifies that every local variable is definitely assigned before use — preventing undefined behavior from uninitialized C++ variables.

```python
def example(cond: bool) -> None:
    if cond:
        x: Point = Point(1, 2)
    print(x)  # ERROR: variable 'x' may be used before assignment

    y: Int32          # bare annotation, no init
    print(y)          # ERROR: variable 'y' may be used before assignment

    z: Int32
    for i in range(n):
        z = i         # assignment inside loop doesn't count (loop may not execute)
    print(z)          # ERROR
```

**Rules:**
- Function parameters and `self` are assigned at entry
- `x = expr` marks `x` as assigned
- `x: Int32` (bare annotation) does NOT mark as assigned
- **If/else merge**: intersection of both branches (a variable is assigned after `if/else` only if assigned in *both* branches)
- **Terminated branch**: if one branch returns/breaks/continues, the other branch's state is used
- **Loops**: conservative — assignments inside loop bodies don't persist after the loop (loop may execute 0 times)
- **Top-level code**: skipped (globals have separate initialization semantics)

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
- **Working**: Single class inheritance (`class Child(Parent)`)
- **Working**: Generic inheritance with forwarded type params (`class Child[T](Parent[T])`)
- **Working**: Explicit protocol implementation (`class MyList(Sequence[Int32])`)
- **Planned**: Enums → `enum class`

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

#### Working: Protocol Inheritance

Protocols can inherit from other protocols, creating combined protocols that require all methods from parent protocols:

```python
from typing import Protocol, Sized
from tpy import Int32

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

    def __len__(self) -> Int32:
        return Int32(5)

class Container[T: PrintableAndSized]:
    value: T

    def describe(self) -> None:
        print(self.value.to_str())  # From Printable
        print(len(self.value))       # From Sized
```

Generated C++:
```cpp
template<typename T>
concept Printable = requires(T& t) {
    { t.to_str() } -> std::convertible_to<std::string_view>;
};

template<typename T>
concept PrintableAndSized = requires(T& t) {
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

**Limitations:**
- Generic parent protocols are not yet supported (`class Child(Sequence[T], Protocol)` is an error)
- Parent protocols must be non-generic (inheriting from `Sized` works, but not `Sequence[T]`)

#### Working: Protocol Fields

Protocols can require fields in addition to methods:

```python
from typing import Protocol
from tpy import Int32

class HasValue(Protocol):
    value: Int32

class Point:
    value: Int32
    def __init__(self, v: Int32):
        self.value = v

def get_value[T: HasValue](item: T) -> Int32:
    return item.value  # Access protocol field

p = Point(42)
print(get_value(p))  # Output: 42
```

Generated C++ concept:
```cpp
template<typename T>
concept HasValue = requires(T& t) {
    { t.value } -> std::convertible_to<int32_t>;
};
```

Protocols can combine fields and methods:
```python
class Container(Protocol):
    count: Int32
    def is_empty(self) -> bool: ...
```

Generic protocols can use type parameters in fields:
```python
class Holder[T](Protocol):
    item: T

def get_item[T: Holder[Int32]](h: T) -> Int32:
    return h.item
```

**Conformance**: A type conforms to a protocol with fields if it has all required fields with matching types. Field type mismatches cause inference failures.

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
- **Built-in conformance**: `list[T]`, `Array[T, N]`, `Span[T]`, `StaticList[T, N]`, `Range[T]` extend `NativeIterable[T]`
- **str**: Extends `NativeIterable[Char]` (iterates over characters)
- **Zero overhead**: Compiles to C++ range-based for loops
- **Not user-extensible**: Requires C++ `begin()`/`end()` support

**Difference from `OptIterator[T]`**: `NativeIterable[T]` is for containers with `begin()`/`end()` (C++ range-for). `OptIterator[T]` is for lazy producers with `next()` (while-loop). See below.

See [docs/PROTOCOL_DESIGN.md](PROTOCOL_DESIGN.md) for the full design rationale.

#### Working: `OptIterator[T]` (lazy iteration)

`OptIterator[T]` is a **structural protocol** for types that produce values lazily via `__next_opt__()` returning `T | None`. Any type with a `__next_opt__() -> Optional[T]` method automatically conforms — no explicit `extends` declaration needed. Types with `__next__() -> T` + `raise StopIteration` also conform (the compiler synthesizes `__next_opt__` in the type registry).

```python
from tpy import Int32, OptIterator

class Counter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __next_opt__(self) -> Int32 | None:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        return None

# Direct use in for-loop (structural detection)
for x in Counter(5):
    print(x)

# Pass to function taking OptIterator[Int32] (structural conformance)
def sum_iter(it: OptIterator[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

print(sum_iter(Counter(5)))        # 10
```

**Codegen**: `for i in range(...)` is optimized to a C-style counter loop:

```cpp
for (int32_t i = 0; i < 5; ++i) {
    // body
}
```

Step ±1 uses `++i`/`--i`; other literal steps use checked `tpy::int32_add(i, step)` for Int32 or `i += step` for BigInt; variable steps use a ternary condition (`step > 0 ? i < stop : i > stop`). Generic `OptIterator[T]` parameters (not `range()` calls) still use the while-loop path:

```cpp
auto& __iter_0 = it;  // reference for variable (preserves consumption)
while (auto __opt_0 = __iter_0.__next_opt__()) {
    int32_t x = *__opt_0;
    // body
}
```

**Key characteristics**:
- **Structural protocol**: Any type with `__next_opt__() -> T | None` automatically conforms
- **Lazy evaluation**: Values produced one at a time, no container allocation
- **break/continue**: Work naturally in both counter-loops and while-loops

#### Iterator Roadmap

| Phase | Status | What |
|-------|--------|------|
| 1. OptIterator | **Working** | User-defined iterators via `__next_opt__()` protocol |
| 2. Counter-loop optimization | **Working** | `for i in range(...)` → C-style `for (int32_t i = ...)` |
| 3. Range as NativeIterable | **Working** | `Range[T]` is an immutable container with `begin()`/`end()`, supports `list(range(...))` |
| 4. Structural OptIterator | **Working** | Check `__next_opt__() -> Optional[T]` method for protocol conformance |
| 5. User-defined iterators | **Working** | `__iter__`/`__next__` compiled to `__next_opt__` under the hood |
| 6. Generator functions | Open | `yield` → state-machine class implementing OptIterator |
| 7. Iterator combinators | Open | `enumerate()`, `zip()`, `filter()`, `map()`, `reversed()` |

See [docs/ITERATOR_DESIGN.md](ITERATOR_DESIGN.md) for the full iterator design document.

#### Working: User-Defined Iterators (`__iter__`/`__next__`)

Two patterns for user-defined iterators, both producing `__next_opt__() -> std::optional<T>` in C++:

**Pattern 1: Python-compatible** — `__next__(self) -> T` + `raise StopIteration` (runs in both tpyc and CPython):

```python
from __future__ import annotations
from tpy import Int32

class Counter:
    current: Int32
    limit: Int32

    def __init__(self, limit: Int32) -> None:
        self.current = 0
        self.limit = limit

    def __iter__(self) -> Counter:
        return self

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration

for x in Counter(5):
    print(x)
```

**Pattern 2: TurboPython-specific** — `__next_opt__(self) -> T | None`:

```python
class Counter:
    # ... same fields and __init__ ...
    def __next_opt__(self) -> Int32 | None:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        return None
```

**Container → Iterator separation** via `__iter__()`:

```python
class NumberRange:
    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[RangeIter]:
        return RangeIter(self.start, self.limit)
```

**Rules**:
- Direct `obj.__next__()` calls are forbidden — use a for-loop or call `obj.__next_opt__()` instead
- `raise StopIteration` is only allowed inside `__next__` methods
- The `next()` builtin is not yet supported

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
- `for` loops work on `OptIterator[T]`-typed parameters ✓ (working)
- Implicit coercion to `Span[T]` works on types extending `NativeContiguous[T]` ✓ (working)
- `for` loops work on `Iterable[T]`-typed parameters (planned - Python-compatible)

See [docs/PROTOCOL_DESIGN.md](PROTOCOL_DESIGN.md) for the full design, including implementation phases and C++ codegen strategies.

### Union/Optional
- **Working**: `T | None` for non-value types (records, lists, arrays) → nullable pointer (`T*`)
  - Locals, parameters, returns: `T*` (nullable pointer)
  - `x is None` / `x is not None` for null checks
  - Field/method/subscript access on unproven optional values emits a warning and inserts a runtime null check
  - Guarded paths (`if x is not None`) and `assert x is not None` narrow `x` to `T`
  - Functions returning `T | None` return `T*` in C++
- **Working**: `T | None` for value types (`Int32 | None`, `bool | None`, `float | None`) → `std::optional<T>`
  - Variables, parameters, returns use `std::optional<T>` directly
  - `x is None` / `x is not None` → `.has_value()` checks
  - Truthiness checks (`if x`, `assert x`, `while x`) narrow on true path, with warning about falsy non-None values
  - `print()` prints `None` for empty, value otherwise
- **Working**: Reassignment-based Optional inference for unannotated variables
  - `x = None; x = Point()` infers `x` as `Point | None`
  - `x = None; x = 123` infers `x` as `int | None`
  - `x = None; x = make_point()` works from function/method return types
  - Bare `x = None` without later type anchor is an error
- **Working**: Literal anchoring for unannotated reassignment
  - `x = 0; x = Int32(666)` infers `Int32` (if previous literals fit Int32 range)
  - `x = 0; x = True` is rejected (no implicit int/bool merge)
- **Working**: Auto inference rules (reassignment)
  - Inference is per variable within its binding scope, across all writes.
  - A later explicit annotation is authoritative and retro-validates earlier writes.
  - Conflicting explicit annotations are an error.
  - `None` seeds optional inference: `x = None; x = T(...)` infers `T | None`.
  - Bare `x = None` with no later concrete anchor is an error.
  - Literal-seeded variables default to `int` (`BigInt`) and may narrow via compatible literal anchoring (for example to `Int32`).
  - `bool` does not auto-merge with numeric families during inference.
  - Augmented assignment currently does not perform literal anchoring (`x = 0; x += Int32(5)` remains `int`/`BigInt`).
  - For `x = 0` style literal-seeded vars, `x += Int32(...)` emits a warning that augmented assignment does not narrow the variable type.
  - The warning also applies when RHS is a function returning `Int32`; explicit `int` anchors (`x: int = 0`, `x = int(0)`) do not warn.
- **Working**: Optional class members (`self.field: T | None`) → `std::optional<T>` inline storage
  - Field access through optional (`obj.field.x`) works via `std::optional::operator->()`
  - `is None` / `is not None` checks use `.has_value()`
  - Boundary conversions between `std::optional<T>` fields and `T*` pointer-locals handled automatically
- **Working**: Optional-aware operator checks for value-consuming expressions (e.g., `x + 1` where `x: Int32 | None`)
  - Unproven use emits warning and inserts runtime null checks
  - Proven non-None paths (guard/assert narrowed) emit unchecked unwraps
- **Open**: `T | U` → templates with `if constexpr`, or overloads

For details, see [docs/NONE_SAFETY.md](NONE_SAFETY.md).

---

## Operators

### Arithmetic
- **Working**: `+`, `-`, `*`, `//`, `%`, `/`, unary `-`
- **Working**: `**` (power) for `int`, `Int32`, and `float`
- **Planned**: `**` with negative integer exponent (e.g., `2 ** -3`) - use `2.0 ** -3` instead

### Comparison
- **Working**: `==`, `!=`, `<`, `<=`, `>`, `>=`
- **Working**: `is`, `is not` (identity comparison with `None` only)

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
- **Working**: `for i in range(n)`, `for i in range(start, end)`, `for i in range(start, end, step)`
- **Working**: `for item in container` (for-each over list, Array, Span, str)
- **Working**: `for x in iterator` (for-each over OptIterator types — user-defined iterators)
- **Working**: `break`, `continue`
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
- **Working**: `@readonly` (conservative contract enforcement on functions/methods)
  - Disallows field/subscript/global writes inside readonly bodies
  - Disallows calls to unknown/non-readonly functions inside readonly bodies
  - `print` remains non-readonly by design (observable I/O side effects)
  - Current form: `@readonly` (bare name). `@tpy.readonly` is not yet supported.
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

### Type Parameter Bounds

**Working**: Constrain type parameters using protocol bounds (Python 3.12+ syntax):

```python
from typing import Sized
from tpy import Int32, Comparable

# Function with bounded type parameter
def get_length[T: Sized](items: T) -> Int32:
    return len(items)  # OK: T conforms to Sized, so len() works

# Class with bounded type parameter
class SortedContainer[T: Comparable]:
    data: list[T]

    def add(self, item: T) -> None:
        # Can use comparison operators because T: Comparable
        ...

# Multiple bounds on different type parameters
def process[T: Sized, U: Comparable](items: T, key: U) -> Int32:
    return len(items)
```

**Protocol Method Calls**: You can call protocol methods on bounded type parameters:

```python
from typing import Protocol, Self
from tpy import Int32, Own

class Clonable(Protocol):
    def clone(self) -> Own[Self]: ...

def duplicate[T: Clonable](item: T) -> Own[T]:
    return item.clone()  # OK: T conforms to Clonable
```

Generated C++ (concept-constrained templates):
```cpp
template<tpy::Sized T>
int32_t get_length(const T& items) {
    return tpy::__len__(items);
}

template<tpy::Comparable T>
struct SortedContainer {
    std::vector<T> data;
    void add(const T& item) { ... }
};
```

**Available Protocol Bounds**:
- `Sized` - has `__len__()` method
- `Comparable` - has comparison operators (`<`, `<=`, `>`, `>=`, `==`, `!=`)
- `Sequence[T]` - has `__len__()` and `__getitem__()`
- `NativeIterable[T]` - supports C++ range-for iteration
- `OptIterator[T]` - lazy iteration via `next() -> Optional[T]`
- User-defined protocols (including protocols with inheritance)

**Protocol Inheritance with Bounds**: When using a child protocol as a bound (e.g., `T: PrintableAndSized`), methods from all ancestor protocols are available on `T`.

**Limitations**:
- Generic methods on classes (methods with their own type params) not yet supported

---

## Classes

### Definition
- **Working**: Typed fields
- **Working**: `__init__`
- **Working**: Instance methods
- **Working**: Generic classes (Python 3.12+ syntax)
- **Working**: `@staticmethod` → static methods
- **Working**: Single class inheritance (`class Child(Parent)`)
- **Working**: Generic inheritance (`class Child[T](Parent[T])`)
- **Working**: Explicit protocol implementation (`class MyList(Sequence[T])`)
- **Open**: `@classmethod` → if use case is clear
- **Open**: `@property` → getter/setter methods

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

### Integer Type Parameters

**Working**: Generic classes can have integer type parameters using Python 3.12+ syntax with `: int` bound:

```python
from tpy import Int32

class Container[T, N: int]:
    size: Int32

    def __init__(self) -> None:
        self.size = Int32(N)

    def get_capacity(self) -> Int32:
        return Int32(N)

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
Container<std::string_view, 10> c{};
```

**Key points:**
- Use `: int` bound to declare integer type parameters: `class Foo[T, N: int]`
- Integer type parameters become `std::size_t` template parameters in C++
- Can use `Int32(N)` to convert the integer constant to Int32 inside methods
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
- Using `N` in `__init__` (e.g., `self.size = Int32(N)`) is **TurboPython-only** - CPython cannot access type args during construction
- `Array[T, N]` with forwarded N works as type annotation, but instantiation in `__init__` is TurboPython-only
- Tests using `N` inside `__init__` cannot be validated against CPython

### Class Inheritance

**Working**: Single class inheritance with optional protocol implementations.

```python
from tpy import Int32
from typing import Protocol

# Base class
class Animal:
    name: str
    age: Int32

    def __init__(self, name: str, age: Int32) -> None:
        self.name = name
        self.age = age

    def speak(self) -> str:
        return "..."

# Child class inheriting from Animal
class Dog(Animal):
    breed: str

    def __init__(self, name: str, age: Int32, breed: str) -> None:
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
  std::string_view name;
  int32_t age;
  Animal() = default;
  explicit Animal(std::string_view name, int32_t age) : name(name), age(age) {}
  std::string_view speak() { return "..."; }
};

struct Dog : Animal {
  std::string_view breed;
  Dog() = default;
  explicit Dog(std::string_view name, int32_t age, std::string_view breed)
    : Animal(name, age), breed(breed) {}  // Base init + field init
  std::string_view speak() { return "Woof!"; }
};
```

**Key points:**
- Single class inheritance only (multiple class inheritance is an error)
- Use `super().__init__(args)` to call the parent constructor
- Method override works by simply defining a method with the same name
- Inherited fields and methods are accessible via `self.field` and `self.method()`

#### `super()` Support

**Working**: Python 3-style `super()` for calling parent class constructors and methods.

**Calling parent constructor:**
```python
class Dog(Animal):
    breed: str

    def __init__(self, name: str, age: Int32, breed: str) -> None:
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
std::string_view full_speak() {
  std::string_view parent_msg = Animal::speak();
  return parent_msg;
}
```

**Works with generic parents:**
```python
class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value

class LabeledContainer(Container[Int32]):
    label: str
    def __init__(self, label: str, value: Int32) -> None:
        super().__init__(value)  # Type-aware: calls Container<int32_t>
        self.label = label
```

**Restrictions:**
- Python 3 style only: `super()` with no arguments
- Must be inside a non-static method
- Class must have a parent class
- `super()` in `@staticmethod` is an error
- `super()` not supported for builtin type parents (`list`, `StaticList`, etc.) - use implicit default construction instead

**Working**: Inheriting from generic classes with concrete type arguments:
```python
from tpy import Int32

class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value
    def get(self) -> T:
        return self.value

class IntContainer(Container[Int32]):
    extra: Int32
    def __init__(self, value: Int32, extra: Int32) -> None:
        self.value = value   # Inherited field, type is Int32 (not T)
        self.extra = extra

c = IntContainer(42, 100)
print(c.get())   # Returns Int32, not T
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
from tpy import Int32

class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value
    def get(self) -> T:
        return self.value

# Forward type parameter to parent
class Wrapper[T](Container[T]):
    extra: Int32
    def __init__(self, value: T, extra: Int32) -> None:
        self.value = value
        self.extra = extra

w: Wrapper[str] = Wrapper[str]("hello", Int32(42))
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

# Forward T, fix U to Int32
class IntPair[T](Pair[T, Int32]):
    def __init__(self, first: T, second: Int32) -> None:
        self.first = first
        self.second = second

p: IntPair[str] = IntPair[str]("hello", Int32(42))
print(p.get_first())   # Returns str (forwarded T)
print(p.get_second())  # Returns Int32 (fixed U)
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
from tpy import StaticList, Int32

class IntStack(StaticList[Int32, 100]):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def push(self, value: Int32) -> None:
        self.append(value)  # Inherited from StaticList

def main() -> Int32:
    stack = IntStack("my_stack")

    # Use inherited methods
    stack.push(Int32(10))
    stack.push(Int32(20))

    # Use inherited __len__
    print(len(stack))  # 2

    # Use inherited __getitem__
    print(stack[0])    # 10

    # Access own field
    print(stack.name)  # "my_stack"

    return Int32(0)
```

Generated C++:
```cpp
struct IntStack : StaticList<int32_t, 100> {
  std::string_view name;

  IntStack() = default;
  explicit IntStack(std::string_view name) : name(name) {}

  void push(int32_t value) {
    (*this).push_back(value);  // Calls inherited method
  }
};

// Usage
IntStack stack("my_stack");
stack.push(10);
stack.push(20);
std::cout << tpy::__len__(stack) << "\n";  // 2
std::cout << stack[0] << "\n";              // 10
```

**Supported builtin parents:**
- `StaticList[T, N]` - fixed-capacity list
- `Array[T, N]` - fixed-size array (planned)
- `list[T]` - dynamic list (planned)

**Key points:**
- Inherited methods from builtins work automatically (e.g., `append`, `__getitem__`, `__len__`)
- Type parameters are substituted with concrete types (e.g., `T` → `Int32`)
- No `super().__init__()` needed - C++ base class default constructor is called automatically
- Can add custom fields and methods to the child class
- Subscript (`stack[i]`) and `len(stack)` work on child types

**Limitations:**
- Generic parent without type args rejected (`class Child(Parent)` where `Parent[T]` is generic)
- No `super()` calls - child must initialize parent fields directly
- Cannot inherit from builtins with forwarded type parameters (`class Child[T](StaticList[T, 100])`)

### Explicit Protocol Implementation

**Working**: Classes can declare protocol implementations explicitly.

```python
from tpy import Int32
from typing import Protocol

# Define a protocol
class Printable(Protocol):
    def __str__(self) -> str:
        ...

# Explicit protocol implementation
class Person(Printable):
    name: str
    age: Int32

    def __init__(self, name: str, age: Int32) -> None:
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
    def size(self) -> Int32: ...

# Implement multiple protocols
class Box(Printable, Describable, Measurable):
    width: Int32
    height: Int32

    def __str__(self) -> str:
        return "Box"

    def describe(self) -> str:
        return "A rectangular box"

    def size(self) -> Int32:
        return self.width * self.height
```

**Combined class inheritance and protocol implementation:**
```python
# Inherit from class AND implement protocols
class Car(Vehicle, Printable, Measurable):
    model: str

    def __init__(self, brand: str, year: Int32, model: str) -> None:
        self.brand = brand  # From Vehicle
        self.year = year    # From Vehicle
        self.model = model

    def __str__(self) -> str:
        return self.model

    def weight(self) -> Int32:
        return 1500
```

**Rules:**
- At most one class parent (single inheritance)
- Multiple protocol implementations allowed
- Class parent must come first in the base list: `class Child(Parent, Protocol1, Protocol2)`
- All declared protocol methods must be implemented (compiler validates)
- Protocols provide no implementation - they're pure interfaces

### Special Methods
- **Working**: `__init__`
- **Working**: `__eq__`, `__ne__`, `__lt__`, `__le__`, `__gt__`, `__ge__` → comparison operators
- **Working**: `__add__`, `__sub__`, `__mul__`, etc. → arithmetic operators
- **Working**: `__len__` → `__len__()` method (used by `len()`)
- **Working**: `__getitem__` → `operator[]` (for `Sequence` conformance)
- **Planned**: `__del__` (destructor)
- **Open**: `__setitem__` → mutable `operator[]`
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
- **Working**: `abs()`, `min()`, `max()` for numeric types
- **Planned**: List slicing: `items[1:3]`
- **Open**: `isinstance()` → compile-time type check / type narrowing
- **Open**: `type()` → compile-time type info
- **Working**: `list()` → empty list constructor (requires type annotation), `list(iterable)` from NativeIterable containers, `list(range(...))`, `list(iterator)` from OptIterator
- **Working**: `int(float)` → truncates toward zero, panics on NaN/infinity
- **Working**: `float(int)`, `float(Int32)` → converts to float
- **Open**: `str()`, `int(str)` → string conversion functions (see below)
- **Open**: `enumerate()` → returns OptIterator (see iterator roadmap)
- **Open**: `zip()` → returns OptIterator (see iterator roadmap)

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

# List from range
nums = list(range(5))        # → [0, 1, 2, 3, 4]
nums2 = list(range(2, 7))   # → [2, 3, 4, 5, 6]

# List from user-defined iterator (OptIterator)
result = list(Counter(5))    # → [0, 1, 2, 3, 4]
```

Generated C++:
```cpp
// list([1, 2, 3]) - from literal
std::vector<tpy::BigInt> x({1, 2, 3});

// list(container) - from NativeIterable (range, array, list, etc.)
auto y = tpy::from_range<std::vector<int32_t>>(tpy::Range<int32_t>(5));

// list(iterator) - from OptIterator (user-defined)
auto z = tpy::collect<std::vector<int32_t>>(Counter(5));
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

Used to make value copies explicit — both for returning lvalues as `Own[T]` and for acknowledging implicit copies when assigning to record fields:

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
- Silences copy warnings when assigning lvalues to inline storage (`self.field = copy(x)`, `items.append(copy(x))`)
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
  - **Note**: tpy types require explicit import - using `Int32` without `from tpy import Int32` produces an error with a helpful suggestion
- **Working**: `import time` and `from time import time`
- **Working**: Import aliases: `from time import time as get_time`, `from tpy import Int32 as I32`
  - Aliases work for both type annotations (`x: I32`) and constructor calls (`I32(42)`)
- **Working**: Module-level aliases: `import time as t`, `import tpy as t` (then use `t.time()`, `t.Int32()`)
- **Working**: `import sys` - system module with `sys.argv`
- **Working**: `import math` - mathematical functions
- **Working**: Namespace wrapping for modules (each module gets its own C++ namespace)
- **Working**: User-defined modules (multi-file projects)
- **Working**: Package support (dotted imports, `__init__.tp.py`, namespace packages)
- **Working**: Relative imports (`from . import sibling`, `from ..pkg import func`)
- **Working**: Re-exports in `__init__.py` (functions, records, protocols, variables)
- **Open**: Importing additional Python stdlib subsets that can be statically compiled

### User-Defined Modules (Working)

TurboPython supports importing from other `.tp.py` files in the same directory:

```python
# utils.tp.py
from tpy import Int32

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def add(a: Int32, b: Int32) -> Int32:
    return a + b

MAX: Int32 = Int32(100)
```

```python
# main.tp.py
from tpy import Int32
from utils import Point, add, MAX

p = Point(Int32(1), Int32(2))
result = add(p.x, p.y)
print(MAX)  # 100
```

**Supported import styles:**
- `from mod import func, Record, Protocol` - import specific items
- `import mod` then `mod.func()` - module-qualified access
- `from mod import X as Y` - import with alias

**Module resolution:**
- Looks for `mod.tp.py` first, falls back to `mod.py`
- Supports package directories with `__init__.tp.py` or `__init__.py`

**`__name__` variable:**
- Entry point module: `__name__ == "__main__"`
- Imported modules: `__name__ == "module_name"`

**Import execution order:** Imports execute at their source location, matching Python semantics. Top-level code in imported modules runs when the import statement is reached, not hoisted to the beginning:

```python
# main.tp.py
print("before import")  # runs first
from helper import func  # helper's top-level code runs now
print("after import")   # runs last
```

Each module initializes only once (double-init guard prevents diamond dependency issues).

**Circular imports:** Detected at compile time with clear error messages.

**Shadowing builtin modules:** User modules can shadow builtin modules (`math`, `time`, `sys`). If you create `math.tp.py` in your project, `from math import ...` will use your module instead of the builtin. A warning is emitted:
```
main.tp.py:1: warning: import 'math' shadows builtin module
```

**C++ mapping:** Each module gets its own namespace (`tpy_user::utils::Point`). Cross-module references use fully qualified names. Package modules use nested namespaces (`tpy_user::mypackage::submod::func`).

### Packages (Working)

TurboPython supports Python-style packages with `__init__.tp.py` files:

```
project/
├── main.tp.py
└── mypackage/
    ├── __init__.tp.py    # Package init (can export functions/variables)
    ├── utils.tp.py       # Submodule
    └── inner/
        ├── __init__.tp.py
        └── core.tp.py
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

**Namespace packages:** TurboPython supports namespace packages (no `__init__` required for simple submodule imports). If `mypackage/utils.tp.py` exists, `from mypackage.utils import X` works without requiring `mypackage/__init__.tp.py`.

**Package initialization:** Parent package `__init__` files are discovered and initialized before submodules, matching Python import semantics. When importing `from mypackage.submod import X`, the `mypackage/__init__` is executed first (side effects like `print()` run), then the submodule is initialized.

**C++ mapping:**
- `mypackage/__init__.tp.py` → `namespace tpy_user::mypackage`
- `mypackage/utils.tp.py` → `namespace tpy_user::mypackage::utils`
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

### Re-exports in `__init__.py` (Working)

Package `__init__.py` files can re-export items from submodules, making them available at the package level:

```python
# mypackage/__init__.py
from tpy import Int32
from .utils import add, Point    # Re-export from submodule

VERSION: Int32 = Int32(42)       # Package-level variable
```

```python
# main.tp.py - import from package level
from mypackage import add, Point, VERSION

result = add(1, 2)     # Uses mypackage.utils.add via re-export
p = Point(10, 20)      # Uses mypackage.utils.Point via re-export
print(VERSION)         # Package variable
```

**What can be re-exported:**
- Functions
- Records (classes)
- Protocols
- Variables

**C++ implementation:** Re-exported items use `using` declarations and reference aliases:
```cpp
// mypackage.hpp (generated)
namespace tpy_user::mypackage {
  using tpy_user::mypackage::utils::add;    // Re-exported function
  using tpy_user::mypackage::utils::Point;  // Re-exported record
  inline auto& counter = tpy_user::mypackage::utils::counter;  // Re-exported variable
  extern int32_t VERSION;                    // Package's own variable
}
```

**Aliased re-exports:** When using import aliases (`from .utils import func as f`), different C++ constructs are used:
```cpp
inline auto& f = tpy_user::mypackage::utils::func;    // Function alias
using Pt = tpy_user::mypackage::utils::Point;         // Record alias
```

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
- **Working**: `assert` (`assert cond`, `assert cond, "msg"`)
  - Emits runtime panic when condition is false
  - Contributes control-flow narrowing facts
  - Current limitation: assert message must be a string literal
- **Open**: `try`/`except` → error codes, `std::expected`, or limited exceptions
- **Open**: `raise` → if exception model chosen

---

## Concurrency

- **Open**: `async`/`await` → coroutines or state machines

---

## Generators

- **Open**: `yield` → generator as state-machine class implementing OptIterator (see iterator roadmap)
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

## Implementation Notes

### Method Overload Resolution

Builtin method calls go through overload resolution in sema, which attaches `resolved_function_info` to the AST node. Codegen then uses this resolved info.

**Limitation**: Subscript (`__getitem__`, `__setitem__`) and binary operator codegen paths currently fall back to registry lookup without `resolved_function_info`. This works because these methods are single-overload today. If multi-overload subscript or binop methods are needed in the future, those codegen paths must be updated to thread `resolved_function_info` through (similar to regular method calls).

## Open Questions

1. **Allocation control ergonomics**: `@noalloc` vs `@alloc` vs module-level vs compiler flag?
2. **Container element storage**: How to spell "list of values" vs "list of pointers"?
3. **String semantics**: When does `str` allocate vs use SSO?
4. **Exceptions**: Error codes, `std::expected`, or actual exceptions?
5. **Lambda efficiency**: Always template? Configurable? Type-erased fallback?
6. **Macro system scope**: How much compile-time Python execution? Safety limits?
