# TurboPython Language Features

Status legend:
- **Working** - Implemented now
- **Planned** - Will add
- **Open** - Could add with right design (notes on how)

---

## Design Philosophy

**Performance**: Low-latency compiled output. Most code uses standard Python constructs freely, while hot paths can opt into stricter constraints (e.g. `@noalloc`). No garbage collection, no interpreter overhead.

**Regular Python compatibility**: TurboPython aims to compile and run regular Python code wherever practical. When a feature is unsupported, or semantics differ from CPython (for example, inline field storage vs Python object references), diagnostics should explain the gap clearly.

**Constrained C++ interop**: Easy integration with existing C/C++ code, but only through explicit interop rules. TurboPython does not aim to accept arbitrary native C++ types or signatures without declared compatibility.

**Familiar syntax**: Keep the language approachable for non-programmers and close to regular Python. The everyday constructs (`int`, `str`, `list`, `dict`, functions, classes) should feel familiar, with minimal required annotations.

**Semantic transparency**: When TurboPython behavior intentionally differs from CPython, the compiler should warn so differences are visible during development and testing.

**Tooling-friendly**: Source files are valid Python, so existing IDEs, linters, type checkers, and LLMs work without special plugins or language server protocols. Development uses the same tools as regular Python.

**Thread safety**: Unlike CPython (which relies on the GIL), TurboPython targets multi-threaded, high-performance environments. The compiler should produce thread-safe code by default where possible without sacrificing performance, and give the user explicit control where trade-offs exist. Compiler analyses (e.g. narrowing, aliasing) must be sound in the presence of concurrent access.

**Pluggable backends**: The mapping from TurboPython to C++ should be configurable. Different projects have different needs:
- `Span[T]` → `std::span<T>`, `ReadOnlySpan[T]` → `std::span<const T>`, or custom span types
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
Span[T]     → std::span<T>
ReadOnlySpan[T] → std::span<const T>
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
tpyc --backend=trading src/order_handler.py
```

This allows the same TurboPython source to target different environments without code changes.

---

## Type Categories

TurboPython distinguishes between **value types** and **object types**:

### Value Types
Small, immutable, passed by copy:
- `int`, `float`, `Int32`, `Int64`, `bool`
- Small immutable structs (configurable threshold)
- `FixStr[N]` (fixed-size string)
- User records that extend `ValueType` (see [ValueType Protocol](#working-valuetype-marker-protocol))

### Object Types
Larger, mutable, passed by reference:
- Classes/structs (by default)
- `str` (dynamic string)
- `list`, `dict`, `set`

### Parameter Passing Convention
For object types, `T` in a parameter implicitly means reference:
```python
def process(data: MyClass) -> None:  # data is passed by reference
    data.value = 42  # modifies original
```

---

## Types

### Numeric
- **Working**: `int` (Python's int -> `tpy::BigInt` arbitrary precision, custom runtime implementation)
- **Working**: `float` (Python's float -> `double`, 64-bit IEEE 754)
- **Working**: `Float32` (32-bit single precision -> `float`), `Float64` (alias for `float`)
- **Working**: `Int8`, `Int16`, `Int32`, `Int64`, `UInt8`, `UInt16`, `UInt32`, `UInt64`, `bool`, `Char`

#### Default Integer Type for Unannotated Literals (Working)

Unannotated integer literals (`x = 42`) use the configured default integer type, controlled by `--default-int` (default: `Int32`). Explicit `int` annotations always mean `BigInt`:

```python
x = 42          # Int32 (default), or Int64/BigInt with --default-int
y: int = 42     # always BigInt (explicit annotation)
```

The compiler performs range-safe fallback: if a literal's value exceeds the configured type's range, it automatically falls back to `BigInt` with a warning:

```python
a = 2147483647   # Int32 (fits)
b = 2147483648   # BigInt with warning (exceeds Int32 range)
c = -2147483648  # Int32 (exactly Int32 min)
d = -2147483649  # BigInt with warning (below Int32 min)
```

Constant-folded expressions (`1 << 100`, `2 ** 40`) are evaluated at compile time and use the same range check on the result.

See `docs/INTEGER_INFERENCE_DESIGN.md` for the full design rationale.

#### Mixed Arithmetic and Type Promotion (Working)

All fixed-width integer types use checked arithmetic (panics on overflow). Operations between fixed-width types and `int` (BigInt) follow Python's promotion rules - the result is always the wider type:

```python
x: Int32 = 5
y: int = 10     # explicit int (BigInt)
z = x + y       # Result is int (BigInt), not Int32
```

| Operation | Result Type | Rationale |
|-----------|-------------|-----------|
| `Int32 + Int32` | `Int32` | Both operands same type, checked arithmetic |
| `Int32 + int` | `int` | Promotes to BigInt to avoid overflow |
| `int + Int32` | `int` | Promotes to BigInt to avoid overflow |
| `Int32 + literal` | `Int32` | Literal coerces to target type |

**Implicit widening**: Smaller fixed-width integers widen to larger ones automatically:
- Signed: `Int8` → `Int16` → `Int32` → `Int64`
- Unsigned: `UInt8` → `UInt16` → `UInt32` → `UInt64`
- Cross-sign: `UInt8` → `Int16`, `UInt16` → `Int32`, `UInt32` → `Int64`

Negation (`-x`) is only available on signed types — unsigned types produce a compile error.

**Explicit cross-type casts**: Any fixed-width integer type can be explicitly converted to any other using the constructor. Out-of-range values panic at runtime:

```python
from tpy import Int8, Int32, UInt8

a: UInt8 = UInt8(42)
b: Int32 = Int32(a)      # OK: widening, always safe
c: UInt8 = UInt8(b)      # OK: narrowing, panics if b > 255

d: UInt8 = UInt8(Int32(300))  # Runtime panic: UInt8 overflow
e: UInt8 = UInt8(-3)          # Compile error: out of range literal
```

**Truncating conversion** (`trunc`): For wrapping/modular conversion without panicking, use the `trunc()` static method:

```python
from tpy import Int8, Int32, UInt8

x: Int32 = Int32(300)
print(UInt8.trunc(x))       # 44 (300 % 256)
print(UInt8.trunc(Int8(-1))) # 255

# Also accepts BigInt (int)
print(UInt8.trunc(2**100 + 42))  # 42 (low 8 bits)
```

**Float promotion**: Any operation involving `float` promotes to `float`:

| Operation | Result Type | Rationale |
|-----------|-------------|-----------|
| `float + float` | `float` | Both operands same type |
| `float + int` | `float` | Float is wider than int |
| `int + float` | `float` | Float is wider than int |
| `float + Int32` | `float` | Float is wider than Int32 |
| `int / int` | `float` | True division always returns float (panics on zero divisor) |
| `Int32 / Int32` | `float` | Fixed-width true division: operands cast to double (panics on zero divisor) |

For augmented assignment (`+=`, `-=`, `*=`, `/=`, etc.), the target type is preserved - the right-hand side is converted to match:
```python
total: Int32 = 0
big_value: int = 10  # int (BigInt)
total += big_value  # big_value converted to Int32, then Int32 addition
total *= big_value  # same: converts to Int32 first
```

This ensures fixed-width variables stay in the checked arithmetic domain. If the BigInt value is too large for the target type, the conversion panics at runtime.

### Strings
- **Working**: `str` type -- context-dependent: `std::string` by default, `std::string_view` for parameters
- **Working**: `String` (`tpy.String`) -- explicit owned `std::string` (parameters use `const std::string&`)
- **Working**: `StrView` (`tpy.StrView`) -- explicit `std::string_view`
- **Working**: `Char` type for single characters
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

`str` is context-dependent, matching Python's actual semantics where parameters are borrowed and returns/fields are owned. Locals are inferred: `std::string_view` when safe (literal or param source), `std::string` when ownership is needed:

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
```

For explicit control, use `String` or `StrView` from the `tpy` module:

```python
from tpy import String, StrView

def process(name: String) -> String:   # const std::string& in, std::string out
    return name

def peek(data: StrView) -> StrView:    # string_view in, string_view out
    return data
```

`StrView` is a non-owning view, so the compiler enforces safety restrictions:
- Cannot be used as a record field (dangling risk -- use `str` or `String`)
- Cannot use `+=` (would dangle -- use `str` or `String` for mutable strings)
- Returning a `StrView` referencing a local or temporary is an error

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

### Containers
- **Working**: `list[T]` - dynamic list → `std::vector<T>` (with context-dependent inference)
- **Working**: `list[T] + list[T]` concatenation → new list, `list[T] += list[T]` extend in-place, `del lst[i]` element removal
- **Working**: Array literals `[1, 2, 3]` → `std::array<T, N>` or `std::vector<T>` (context-dependent)
- **Working**: `Array[T, N]` - fixed-size array with explicit type annotation
- **Working**: `Span[T]` - non-owning mutable view into contiguous memory → `std::span<T>`
- **Working**: `ReadOnlySpan[T]` - non-owning read-only view into contiguous memory → `std::span<const T>`
- **Working**: `SpanIter[T]` - lightweight iterator over a contiguous span → `tpy::SpanIter<T>`. Constructed from `Span[T]` or `ReadOnlySpan[T]`. Implements `NativeIterable[T]`, `OptIterator[T]`, `Iterable[T]`, `Iterator[T]`. Used as the return type of `__iter__()` on span-backed user types (e.g. `ArrayList`).
- **Working**: `tuple[T1, T2, ...]` - fixed-length typed tuple -> `std::tuple<T1, T2, ...>`
- **Working**: `dict[K, V]` - ordered hash map → `tpy::ordered_map<K, V>` (insertion-order preserving)
  - Literals `{k: v, ...}`, subscript `d[k]`/`d[k] = v`, `del d[k]`, `len(d)`, `k in d`, `for k in d`
  - Constructor: `dict(iterable)` from any iterable of `tuple[K, V]` (list of tuples, `.items()` view, etc.)
  - Methods: `get(k)`, `get(k, default)`, `pop(k)`, `pop(k, default)`, `clear()`, `update(other)`, `setdefault(k, default)`, `keys()`, `values()`, `items()`
  - Views: `d.keys()`, `d.values()`, `d.items()` return zero-allocation views with `for`-loop, `len()`, `in`
  - `Iterable[T]` conformance: `dict[K,V]` and views conform to `Iterable` (`d` is `Iterable[K]`, `d.keys()` is `Iterable[K]`, `d.values()` is `Iterable[V]`, `d.items()` is `Iterable[tuple[K, V]]`) and can be passed to generic functions accepting `Iterable[T]`
  - Keys: `str`, `int`, fixed-width ints, `float`, `bool`, `Char`
  - Return by value requires `Own[dict[K, V]]`
- **Working**: `set[T]` - ordered hash set -> `tpy::ordered_set<T>` (insertion-order preserving)
  - Literals `{a, b, ...}`, `len(s)`, `x in s`, `for x in s`
  - Constructor: `set(iterable)` from any iterable
  - Methods: `add(v)`, `discard(v)`, `remove(v)`, `pop()`, `clear()`, `copy()`
  - Algebra: `union(other)`, `intersection(other)`, `difference(other)`, `symmetric_difference(other)`
  - Predicates: `issubset(other)`, `issuperset(other)`, `isdisjoint(other)`
  - In-place: `update(other)`, `intersection_update(other)`, `difference_update(other)`, `symmetric_difference_update(other)`
  - Operators: `|` (union), `&` (intersection), `-` (difference), `^` (symmetric difference)
  - Comparison: `<=` (subset), `<` (strict subset), `>=` (superset), `>` (strict superset)
  - Augmented: `|=`, `&=`, `-=`, `^=`
  - Elements must be hashable (same constraint as dict keys)
  - Return by value requires `Own[set[T]]`
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
| Function local, passed to `Span[T]` param | `Array` | `std::array` | Span is a view, array stays on stack |
| Function local, different-size reassignment | `list` | `std::vector` | `x = [1,2,3]; x = [4,5]` -- sizes differ, must be dynamic |
| Function local, returned as `list[T]` | `list` | `std::vector` | Return type context propagates to local variable |
| Function local, alias mutated | `list` | `std::vector` | `b = a; b.append(4)` -- both `a` and `b` become list |
| Explicit annotation `x: Array[T, N]` | `Array` | `std::array` | User opted into fixed size |
| Explicit annotation `x: list[T]` | `list` | `std::vector` | User opted into dynamic list |
| Return type `-> Own[list[T]]` | `list` | `std::vector` | Return type context propagates to literal |

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

def reader(items: Span[int]) -> int:
    return items[0]

def caller2():
    data = [1, 2, 3]       # → std::array<BigInt, 3> (Span is a view)
    return reader(data)
```

**Contextual element-type widening**: When the annotation's element type is wider than the literal's inferred element type, the literal adopts the annotation's type. This enables mixed-type literals:
```python
# Union element types: literal elements are checked against the annotation
items: list[Int32 | None] = [Int32(1), None, Int32(3)]
empty: list[Int32 | None] = []
```

Note: Passing literals (`[]`, `[1,2,3]`) or constructors (`list()`) directly to functions expecting mutable reference parameters works - the compiler generates temporary variables automatically.

`Span[T]` is a non-owning mutable view into contiguous memory. `ReadOnlySpan[T]` is the read-only variant. This follows the same pattern as `Ptr[T]`/`ReadOnlyPtr[T]`:

```python
from tpy import Int32, Span, ReadOnlySpan, Array

# Mutable span -- can read and write elements
def zero_first(values: Span[Int32]) -> None:
    values[0] = 0

# Read-only span -- can only read elements
def sum_values(values: ReadOnlySpan[Int32]) -> Int32:
    total: Int32 = 0
    for v in values:
        total += v
    return total

# Any contiguous container coerces to Span or ReadOnlySpan:
arr: Array[Int32, 3] = [10, 20, 30]
zero_first(arr)                     # Array -> Span[Int32]
print(sum_values(arr))              # Array -> ReadOnlySpan[Int32]
print(sum_values([1, 2, 3, 4, 5])) # array literal -> ReadOnlySpan[Int32]

# Span[T] auto-coerces to ReadOnlySpan[T] (like Ptr -> ReadOnlyPtr):
s: Span[Int32] = arr
print(sum_values(s))                # Span -> ReadOnlySpan

# Through @readonly refs, Span[T] becomes ReadOnlySpan[T] automatically
```

Key features:
- `Span[T]` maps to `std::span<T>` (mutable), `ReadOnlySpan[T]` maps to `std::span<const T>`
- Requires C++23 (`-std=c++23`)
- Standard Python `len()` and `[]` indexing work for both (with bounds checking and negative index support)
- `unchecked_get(index)` for raw unchecked access (no bounds check, no negative index normalization)
- `sort()` for in-place stable sort via `std::stable_sort` (matches Python's stable sort guarantee)
- Zero-allocation passing of fixed-size arrays to functions that work with any size
- Constructors: `Span(Ptr[T], Int32)` and `ReadOnlySpan(ReadOnlyPtr[T], Int32)` for low-level span creation
- Explicit construction from containers: `Span[T](arr)`, `ReadOnlySpan[T](lst)` for any `ReadOnlySpanLike[T]` source (list, Array)
- Containers coerce to `Optional[Span[T]]` / `Optional[ReadOnlySpan[T]]` at call sites
- `Ptr[T].span(length)` returns `Span[T]`, `ReadOnlyPtr[T].span(length)` returns `ReadOnlySpan[T]`

#### Tuples (Working)

Fixed-length typed tuples with compile-time element access:

```python
from tpy import Int32

# Type is inferred -- no annotation needed
t = (Int32(1), "hello")
print(t)           # (1, 'hello')

# Explicit annotation also works
t2: tuple[Int32, bool, str] = (Int32(42), True, "world")

# Element access with compile-time integer index
x = t[0]           # -> std::get<0>(t)
s = t[1]           # -> std::get<1>(t)
last = t[-1]       # negative indexing supported

# Single-element tuple (trailing comma required, like Python)
single = (Int32(42),)
print(single)       # (42,)

# Nested tuples
nested = (Int32(10), ("inner", False))

# Tuple as function parameter and return type
def swap[A, B](p: tuple[A, B]) -> tuple[B, A]:
    return (p[1], p[0])

# Tuple unpacking (destructuring assignment)
a, b = (Int32(1), "hello")  # fresh variables
_, second = swap(t)          # _ discards a value

# For-loop unpacking
items: list[tuple[Int32, str]] = [(Int32(1), "one"), (Int32(2), "two")]
for n, s in items:
    print(n, s)

# Comparison (== and != only)
a2 = (Int32(1), "hello")
b2 = (Int32(1), "hello")
print(a2 == b2)       # True
```

Reference types in tuples follow context-dependent semantics (same rules as standalone `T`):

```python
from tpy import Int32, Own

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

# Return context: Point is returned by reference (like -> Point)
def find(p: Point) -> tuple[Point, bool]:
    return (p, True)  # -> std::tuple<Point&, bool>

# Own[T] forces value/copy semantics in return context
def make(x: Int32) -> tuple[Own[Point], bool]:
    return (Point(x, x), True)  # -> std::tuple<Point, bool>

# Local tuple: lvalue elements captured by reference
p = Point(Int32(1), Int32(2))
t = (p, True)     # std::tuple<Point&, bool>, p.x mutation visible through t

# Tuple unpacking: reference elements bind as T&
pt, found = find(p)  # pt is Point&, found is bool
```

| Context | `tuple[Int32, Point]` C++ | Rationale |
|---------|--------------------------|-----------|
| Return | `std::tuple<int32_t, Point&>` | Same as `-> Point` = `Point&` |
| Return (`@readonly`) | `std::tuple<int32_t, const Point&>` | Same as readonly `-> Point` |
| Field | `std::tuple<int32_t, Point>` | Same as `field: Point` = owned |
| Container (`list[...]`) | `std::tuple<int32_t, Point>` | Same as `list[Point]` = owned |
| Local variable | `auto` (deduced from RHS) | Same as `p2 = p` = reference |

Restrictions:
- Index must be a compile-time integer literal (variable indexing is rejected)
- Bare `tuple` without type arguments is rejected (must use `tuple[T1, T2, ...]`)
- Returning a reference to a local or temporary as a tuple element is rejected (dangling reference check). Use `Own[T]` to return by value.
- Tuple element assignment (`t[0] = x`) is rejected (tuples are immutable)
- Nested unpacking (`a, (b, c) = ...`) is not yet supported
- `Optional[T]` / `Union` elements in tuples are not yet supported (codegen and printing need work)
- Comparison (`==`, `!=`) requires element-wise type compatibility; `<`, `>`, `<=`, `>=` are not supported

### Pointers/References
- **Working**: `Ptr[T]` -> `T*`
- **Working**: `ReadOnlyPtr[T]` -> `const T*` (also available as `Ptr[readonly[T]]`)
- **Working**: `Own[T]` -> `T` (ownership transfer for return values)
- **Working**: `tpy.unsafe` -- unsafe pointer operations (`unsafe_ptr`, `unsafe_load`, `unsafe_store`, `unsafe_copy_n`, `unsafe_ptr_add`, `unsafe_ptr_diff`, `unsafe_cast`, `unsafe_const_cast`, `unsafe_str_view`, `unsafe_alloc`, `unsafe_alloc_n`, `unsafe_free`, `unsafe_init`, `unsafe_drop`, `unsafe_move_out`)
- **Working**: `tpy.mem` -- uninitialized storage primitives (`UninitArrayStorage[T, N]`, `UninitHeapStorage[T]`)
- **Planned**: `Ref[T]` -> `T&` (explicit reference)
- **Planned**: `ConstRef[T]` -> `const T&`

#### Pointer Coercions (Working)

Implicit conversions between records and pointers with safety checks:

| From | To | Constraints | Generated C++ |
|------|-----|-------------|---------------|
| `T` (record) | `Ptr[T]` | Mutable lvalue, not in return | `&expr` |
| `T` (record) | `ReadOnlyPtr[T]` | Lvalue, not in return | `&expr` |
| `Ptr[T]` | `T` | Null-checked at runtime | `tpy::deref_check(expr)` |
| `Deref[T]` type | `T` | Via `__deref__()` | `expr.__deref__()` |
| `Ptr[T]` | `ReadOnlyPtr[T]` | - | (implicit) |

**Safety rules:**
- Taking address requires an lvalue (variable, field, or subscript) - temporaries rejected
- Return statements cannot convert local records to pointers (dangling pointer prevention)
- `ReadOnlySpan`/str elements cannot convert to `Ptr[T]` (read-only source)
- `Ptr[T]` → `T` includes runtime null check that panics if null

#### Pointer Constructors (Working)

Explicit constructors for `Ptr[T]` and `ReadOnlyPtr[T]`, as an alternative to implicit coercions:

```python
from tpy import Ptr, ReadOnlyPtr, Int32

def test() -> None:
    # Null pointers
    p: Ptr[None] = Ptr[None]()        # → nullptr
    q: Ptr[Int32] = Ptr[Int32]()      # → nullptr (typed null)

    # Address-of with explicit type
    x: Int32 = Int32(42)
    px: Ptr[Int32] = Ptr[Int32](x)    # → &x

    # Address-of with type inference
    py: Ptr[Int32] = Ptr(x)           # → &x (infers Ptr[Int32])
    cp: ReadOnlyPtr[Int32] = ReadOnlyPtr(x) # → &x (infers ReadOnlyPtr[Int32])
```

**`Ptr[readonly[T]]` normalization:** `Ptr[readonly[T]]` is equivalent to `ReadOnlyPtr[T]` and normalizes to it at parse time. Both syntaxes produce the same type:

```python
from tpy import Ptr, readonly, Int32

x: Int32 = Int32(42)
p: Ptr[readonly[Int32]] = Ptr(x)   # same as ReadOnlyPtr[Int32]
```

**Safety rules:**
- Argument must be an lvalue (`Ptr(Point(1,2))` rejected — temporary)
- `Ptr` requires a mutable lvalue; `ReadOnlyPtr` accepts any lvalue
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

#### Auto-Deref via `Deref[T]` Protocol (Working)

Types that implement `__deref__() -> T` conform to the `Deref[T]` protocol and support **auto-deref**: the compiler automatically resolves field access and method calls through `__deref__` chains.

`Ptr[T]` and `ReadOnlyPtr[T]` conform to `Deref[T]`. User-defined types can also implement `__deref__`:

```python
from tpy import Int32, copy

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> Int32:
        return self.x + self.y

class Ref:
    _target: Point
    def __init__(self, target: Point) -> None:
        self._target = copy(target)
    def __deref__(self) -> Point:
        return self._target

def main() -> None:
    r: Ref = Ref(Point(10, 20))
    print(r.x)     # auto-deref: r.__deref__().x → 10
    print(r.sum())  # auto-deref: r.__deref__().sum() → 30
```

Multi-hop chains are supported — if `Box.__deref__() -> Ref` and `Ref.__deref__() -> Point`, then `box.x` resolves through both (max depth: 8). Auto-deref also works through `Optional` receivers (`Ref | None`).

**Deref coercion:** Types with `__deref__() -> T` also coerce to `T` in assignment, argument, and return contexts. For example, a user `Ref` with `__deref__() -> Point` can be passed where `Point` is expected — the compiler inserts `ref.__deref__()` automatically. `Ptr[T]` uses `tpy::deref_check()` for null-checked coercion.

**Pointer None semantics:**

- `None` can be assigned to `Ptr[T]` and `ReadOnlyPtr[T]` (represents `nullptr`)
- `p is None` / `p is not None` work for `Ptr[T]` and `ReadOnlyPtr[T]`
- `p == None` / `p != None` are rejected; use identity checks (`is` / `is not`)

**Null-safety:** Auto-deref through `Ptr[T]`/`ReadOnlyPtr[T]` is null-checked at runtime via `tpy::deref_check()`. A null pointer access panics with "null pointer dereference" instead of causing undefined behavior. Pointers with known non-null provenance skip the null check and use direct `->` access. Non-null provenance is established by:

- Constructor: `p = Ptr(x)` (pointer to a local variable)
- Condition narrowing: `if p is not None:` / `if p is None: return` / `assert p is not None` / `while p is not None:`
- Assignment from a known non-null variable

Narrowing supports negation and `and`/`or` composition. Branch merging uses intersection (non-null only if all paths agree). Reassignment from an unknown source (e.g., function return) clears non-null provenance.

**C++ interop:** User-defined types with `__deref__()` get `operator*()` generated in C++, enabling `*box` syntax from C++ code.

#### Unsafe Pointer Operations (Working)

Low-level pointer arithmetic for C interop and performance-critical code. These bypass bounds checking. The API uses free functions from `tpy.unsafe` (not method calls on pointers):

```python
from tpy import Ptr, ReadOnlyPtr, Int32, UInt32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

# Get raw pointer to array data
arr: Array[Int32, 4] = [Int32(10), Int32(20), Int32(30), Int32(40)]
p: Ptr[Int32] = unsafe_ptr(arr)

# Indexed read/write (no bounds check)
val: Int32 = unsafe_load(p, UInt32(2))       # -> 30
unsafe_store(p, UInt32(0), Int32(99))        # arr[0] = 99

# ReadOnlyPtr has unsafe_load only (no store)
cp: ReadOnlyPtr[Char] = unsafe_ptr("hello")
```

Generated C++: `unsafe_ptr(x)` -> `x.data()`, `unsafe_load(p, i)` -> `p[i]`, `unsafe_store(p, i, v)` -> `p[i] = v`.

See the full `tpy.unsafe` API [below](#unsafe-memory-operations----tpyunsafe-working) for `unsafe_copy_n`, `unsafe_ptr_add`, `unsafe_ptr_diff`, `unsafe_const_cast`, and `unsafe_cast`.

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
- **Value types** (Int32, bool, str, etc.) -- copy-vs-share is unobservable
- **`T: ValueType` bounded type params** -- the bound guarantees value semantics
- **Rvalues** (constructor calls, function results) -- no existing owner
- **`copy()` wrapped** -- intent already explicit

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

#### Auto-Move at Last Use (Working)

When a local variable or `Own[T]` parameter is passed to an `Own[T]` parameter and it is the **last use** of that variable (not read again on any subsequent execution path), the compiler automatically emits `std::move()` instead of requiring `copy()`:

```python
def consume(p: Own[Point]) -> Int32:
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
- **Reassigned locals** when all assignments are rvalue (emits `std::move((*p))` through the pointer)

Auto-move does NOT apply to:
- Regular parameters (borrowed by reference)
- T& reference locals (lvalue-initialized aliases)
- Reassigned locals with any lvalue assignment (may alias borrowed storage)
- Field accesses (`self.x`)
- Top-level (module scope) non-value-type variables
- Variables used across loop iterations

The analysis is conservative: if unsure whether a variable is at its last use (e.g., used inside a loop body that may iterate multiple times), the compiler does NOT auto-move and requires explicit `copy()` as before.

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
def make_point(x: Int32, y: Int32) -> Own[Point]:
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
def forward(p: Own[Point]) -> Int32:
    return consume(p)  # auto-move of Own param
```

**Generic forwarding refs**: When a generic **free function** takes `Own[T]` where `T` is a function-level type parameter, the compiler generates C++ forwarding references (`T&&`) with `std::forward<T>()` for perfect forwarding -- zero-copy pass-through for rvalue arguments:

```python
def wrapper[T](x: Own[T]) -> None:
    sink(x)  # std::forward<T>(x) at last use
```

Generated C++: `template<typename T> void wrapper(T&& x) { sink<T>(std::forward<T>(x)); }`

For **class methods**, `Own[T]` where `T` is a class-level type parameter generates `T` by value with `std::move()` instead -- because `T&&` in a class template is an rvalue reference (T is already bound at instantiation), not a forwarding reference.

#### @nocopy Types (Working)

Types decorated with `@nocopy` have their copy constructor and copy assignment deleted
in C++. Values can only be moved (via auto-move at last use), never copied:

```python
from tpy import Int32, Own, nocopy

@nocopy
class Handle:
    fd: Int32

def close(h: Own[Handle]) -> Int32:
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
    fd: Int32

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

**`__copy__` escape hatch:** A class that would be implicitly nocopy (due to nocopy fields)
can define `__copy__` to remain copyable. The method takes no parameters (besides self) and
returns `Own[ClassName]`. The compiler generates a C++ copy constructor that delegates to
`__copy__()`:

```python
@nocopy
class Handle:
    fd: Int32
    def __init__(self, fd: Int32):
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

#### Unsafe Memory Operations -- `tpy.unsafe` (Working)

The `tpy.unsafe` module provides low-level pointer operations that bypass the compiler's safety checks. These functions require an explicit import -- `from tpy import *` does NOT include them. This forces a deliberate opt-in for unsafe code.

```python
from tpy import Ptr, ReadOnlyPtr, Int32, UInt32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store
```

Import styles supported:
- `from tpy.unsafe import unsafe_ptr, unsafe_load` -- import specific functions
- `import tpy.unsafe` -- use as `tpy.unsafe.unsafe_ptr(...)`
- `import tpy.unsafe as m` -- use as `m.unsafe_ptr(...)`

**`unsafe_ptr`** -- get a raw pointer to the underlying data of a container or string:

```python
arr: Array[Int32, 4] = [Int32(1), Int32(2), Int32(3), Int32(4)]
p: Ptr[Int32] = unsafe_ptr(arr)          # Array[T, N] -> Ptr[T]

lst: list[Int32] = [Int32(10), Int32(20)]
q: Ptr[Int32] = unsafe_ptr(lst)          # list[T] -> Ptr[T]

s: str = "hello"
cp: ReadOnlyPtr[Char] = unsafe_ptr(s)       # str -> ReadOnlyPtr[Char]
```

The element type `T` is inferred from the argument. All three variants generate `.data()` in C++.

**`unsafe_load`** -- read through a pointer at an offset (no bounds checking):

```python
val: Int32 = unsafe_load(p, UInt32(0))   # Ptr[T], UInt32 -> T
val2: Int32 = unsafe_load(cp, UInt32(1)) # ReadOnlyPtr[T], UInt32 -> T
```

Generates `p[offset]` in C++.

**`unsafe_store`** -- write through a pointer at an offset (no bounds checking):

```python
unsafe_store(p, UInt32(0), Int32(99))    # Ptr[T], UInt32, Own[T] -> None
```

Generates `p[offset] = value` in C++. Only `Ptr[T]` is accepted (not `ReadOnlyPtr[T]`).

**`unsafe_copy_n`** -- copy N elements from a source pointer to a destination pointer:

```python
from tpy.unsafe import unsafe_copy_n

src: Array[Int32, 3] = [Int32(10), Int32(20), Int32(30)]
dst: Array[Int32, 3] = [Int32(0), Int32(0), Int32(0)]
unsafe_copy_n(unsafe_ptr(dst), unsafe_ptr(src), UInt32(3))  # Ptr[T], Ptr[T]|ReadOnlyPtr[T], UInt32 -> None
```

Generates `std::copy_n(src, count, dest)` in C++. The source can be either `Ptr[T]` or `ReadOnlyPtr[T]`.

**`unsafe_ptr_add`** -- advance a pointer by a signed element offset:

```python
from tpy.unsafe import unsafe_ptr_add

p: Ptr[Int32] = unsafe_ptr(arr)
q: Ptr[Int32] = unsafe_ptr_add(p, Int64(3))   # Ptr[T], Int64 -> Ptr[T]
```

Generates `(p + 3)` in C++. The offset is in elements (not bytes). Negative offsets move the pointer backward. Works with both `Ptr[T]` and `ReadOnlyPtr[T]`.

**`unsafe_ptr_diff`** -- compute the element distance between two pointers:

```python
from tpy.unsafe import unsafe_ptr_diff

d: Int64 = unsafe_ptr_diff(p2, p1)   # Ptr[T], Ptr[T] -> Int64
```

Generates `static_cast<int64_t>(p2 - p1)` in C++. Returns the number of elements between the two pointers (negative if `p2` precedes `p1`). Both pointers must point into the same allocation. Works with both `Ptr[T]` and `ReadOnlyPtr[T]`.

**`unsafe_const_cast`** -- remove const from a pointer:

```python
from tpy.unsafe import unsafe_const_cast

cp: ReadOnlyPtr[Int32] = ...
p: Ptr[Int32] = unsafe_const_cast(cp)    # ReadOnlyPtr[T] -> Ptr[T]
```

Generates `const_cast<T*>(p)` in C++.

**`unsafe_cast`** -- reinterpret a pointer as pointing to a different type:

```python
from tpy.unsafe import unsafe_cast

p: Ptr[Int32] = ...
q: Ptr[UInt32] = unsafe_cast[UInt32](p)  # explicit type arg (preferred)
q: Ptr[UInt32] = unsafe_cast(p)          # target inferred from annotation
print(unsafe_load(unsafe_cast[UInt32](p), UInt32(0)))  # works inline too
```

Generates `reinterpret_cast<T*>(p)` in C++. `unsafe_cast` is a standard two-type-param generic (`T` = target pointee, `U` = source pointee). The target type can be specified via explicit type argument (`unsafe_cast[UInt32](p)`, partial -- `U` inferred from arg) or inferred from context (`q: Ptr[UInt32] = unsafe_cast(p)` -- both `T` and `U` inferred). The pointer kind (`Ptr`/`ReadOnlyPtr`) is preserved: `Ptr[U]` returns `Ptr[T]`, `ReadOnlyPtr[U]` returns `ReadOnlyPtr[T]`. Casting `ReadOnlyPtr` to `Ptr` is rejected -- use `unsafe_const_cast` first.

**`unsafe_str_view`** -- create a `StrView` from a char pointer and length:

```python
from tpy.unsafe import unsafe_str_view

p: Ptr[Char] = ...
sv: StrView = unsafe_str_view(p, UInt32(5))   # Ptr[Char], UInt32 -> StrView
```

Generates `std::string_view(p, size)` in C++. Also accepts `ConstPtr[Char]`. The caller must ensure the pointer remains valid for the lifetime of the returned view.

**`unsafe_alloc`** / **`unsafe_alloc_n`** -- allocate raw memory for one or N elements:

```python
from tpy.unsafe import unsafe_alloc, unsafe_alloc_n
p: Ptr[Int32] = unsafe_alloc()        # allocate space for 1 element
q: Ptr[Int32] = unsafe_alloc_n(UInt32(10))  # allocate space for 10 elements
```

**`unsafe_free`** -- free raw memory allocated by `unsafe_alloc`/`unsafe_alloc_n`:

```python
from tpy.unsafe import unsafe_free
unsafe_free(p)  # Ptr[T] -> None
```

**`unsafe_init`** -- placement-construct an object at a pointer location:

```python
from tpy.unsafe import unsafe_init
unsafe_init(p, Int32(42))  # Ptr[T], Own[T] -> None
```

**`unsafe_drop`** -- call the destructor on an object at a pointer location (no-op for trivially destructible types):

```python
from tpy.unsafe import unsafe_drop
unsafe_drop(p)  # Ptr[T] -> None
```

**`unsafe_move_out`** -- move a value out of a pointer location without calling the destructor. The pointed-to memory is left in a moved-from state:

```python
from tpy.unsafe import unsafe_move_out
val: Int32 = unsafe_move_out(p)  # Ptr[T] -> Own[T]
```

Generates `std::move(*p)` in C++. Used by `Box[T].take()` to extract the contained value before freeing the raw memory.

#### Uninitialized Storage -- `tpy.mem` (Working)

The `tpy.mem` module provides low-level uninitialized storage types for building containers. Elements are not default-constructed -- the caller manages element lifetimes explicitly via `init`/`drop`. Debug builds include lifetime tracking that panics on misuse (double-init, use-after-drop, leak on destruction).

```python
from tpy import Int32, Ptr
from tpy.mem import UninitArrayStorage, UninitHeapStorage
```

**UninitArrayStorage[T, N]** -- inline (stack) storage for N elements. Uses a C++ union so elements are not default-constructed. Copy/move follows C++ union rules (trivially copyable T: copy works; otherwise: implicitly deleted).

```python
storage = UninitArrayStorage[Int32, 4]()
storage.init(0, 10)        # placement-new at index 0
storage.init(1, 20)
print(storage.load(0))     # access element -> 10
storage.drop(0)            # destroy element at index 0
```

**UninitHeapStorage[T]** -- heap-allocated storage for a given capacity. Non-copyable, movable (move transfers pointer ownership). Always allocated -- constructor allocates, destructor deallocates.

```python
storage = UninitHeapStorage[Int32](4)   # allocate capacity for 4 elements
storage.init(0, 100)
val: Int32 = storage.take(0)            # move value out + destroy slot

# Type can be inferred from assignment target context:
s: UninitHeapStorage[Int32] = UninitHeapStorage(1)  # T inferred as Int32
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
- **Working**: Auto-declare fields from `__init__`: `self.field = param` auto-declares a field using the parameter's type, no class-level annotation needed (CPython-compatible). Only direct parameter assignments at `__init__` top level; computed expressions and inheritance require explicit annotations. See `docs/CONSTRUCTOR_DESIGN.md`.
- **Working**: Field assignment control flow check: assigning `self.field` inside control flow (`if`/`else`/`while`/`for`) in `__init__` bypasses the C++ member initializer list. For `@nocopy` or `__del__` types this is a compile error (UB from default-constructing non-trivial types); for other types it produces a warning. Fix: compute the value unconditionally before the branch (`self.x = a if cond else b`) or use a helper function.
- **Working**: Single class inheritance (`class Child(Parent)`)
- **Working**: Generic inheritance with forwarded type params (`class Child[T](Parent[T])`)
- **Working**: Explicit protocol implementation (`class MyList(Sequence[Int32])`)
- **Working**: Enums -> `enum class` (base `Enum` with integer members, `auto()`, `.name`, `.value`, `==`/`!=`/`is`/`not`, truthiness, record field, `list[Enum]`, `Optional[Enum]`, cross-module import, iteration `for c in Color`, value lookup `Color(0)`, name lookup `Color["Red"]`, `try_parse(Color, "Red")` via `from tpy import try_parse`, `IntEnum` with arithmetic/ordering/int comparison, configurable underlying type via mixin `(Int8, Enum)`)

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

Generated C++ uses `tpy::__bool__()` free function dispatch, with a default template forwarding to user-defined `__bool__()` methods. The `tpy::Truthy` concept constrains generic parameters.

Implicit truthiness is supported: `if obj:`, `while obj:`, `not obj`, `and`/`or` all call `__bool__()` automatically for types that define it. Built-in containers (`list`, `str`, `Array`, `Span`) use `__len__() != 0` for truthiness, matching Python semantics where empty containers are falsy.

#### Working: Built-in `Hashable` Protocol

The built-in `Hashable` protocol is available from the `tpy` module. Types that implement `__hash__() -> UInt64` conform to `Hashable`. The `hash()` builtin dispatches to `tpy::__hash__()`:

```python
h = hash("hello")    # UInt64
h = hash(42)          # UInt64
h = hash(3.14)        # UInt64
h = hash(True)        # UInt64
```

All primitive types (str, int, fixed-width ints, float, bool, Char) and Enum types are hashable. Dict key validation uses the `Hashable` protocol -- only hashable types can be used as dict keys.

Generated C++ uses `tpy::__hash__()` free function dispatch with overloads for built-in types (`std::integral`, `double`, strings, `BigInt`, enums) and a default template forwarding to user-defined `__hash__()` methods.

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
from tpy import Int32, readonly

class Readable(Protocol):
    @readonly
    def read(self) -> Int32: ...

class GoodReader:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value

    @readonly
    def read(self) -> Int32:
        return self.value

class BadReader:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value

    def read(self) -> Int32:  # Not readonly
        return self.value

def use(r: Readable) -> Int32:
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
concept HasValue = requires(const T& t) {
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
concept Addable = requires(const T& t) {
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

#### Working: Self Type in Record Methods

`Self` can be used in return types and parameter types of record (class) methods. It resolves to the current class type, enabling method chaining and builder patterns:

```python
from typing import Self
from tpy import Int32

class Builder:
    name: str
    value: Int32

    def set_name(self, name: str) -> Self:
        self.name = name
        return self

    def with_offset(self, other: Self) -> Int32:
        return self.value + other.value

b = Builder("start", Int32(0))
b.set_name("hello").set_value(Int32(42))  # method chaining
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
- For classes with `__del__`, the compiler auto-inserts destructor suppression (`__tpy_owned_ = false`) at the top of the consuming method body, preventing double-free of moved-from objects

**Real-world example**: `Box[T].take()` uses consuming methods to safely extract the contained value:

```python
from tplib.box import Box
from tpy import Int32

b = Box(Int32(42))
val = b.take()      # Moves out value, destroys box
print(val)          # 42
# b is consumed -- any further use is a compile error
```

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

**Note on temporaries**: When passing a constructor expression (like `Value(21)`) to a protocol-typed parameter, the compiler generates a temporary variable. This is necessary because protocol parameters may use mutable references (`T&`) in C++, which cannot bind directly to temporaries.

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
- `list[T]`, `Array[T, N]`, `Span[T]` - built-in containers
- `str` conforms to `Sequence[Char]` - strings are sequences of characters
- User records with `__len__` and `__getitem__` methods (see below)

**Temporaries**: Passing temporaries (list literals, constructor calls) to protocol-typed parameters works. The compiler generates temporary variables automatically since protocol parameters may use mutable references (`T&`) in C++.

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
- **Non-empty**: at least one method required (no marker protocols)
- **Non-generic**: type parameters not supported (e.g., `class Container[T](Protocol)`)
- **Self-free**: no `Self` type in method params or return types

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

**`Optional[Pet]` rejection** -- `Optional` of a `@dynamic` protocol is a sema error (until `Box[P]` exists for heap-owned dynamic values).

**Cross-module** -- `@dynamic` protocols can be defined in one module and imported in another. The compiler generates fully qualified C++ names (e.g., `::tpy_user::pets::Pet`).

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

The `ValueType` marker protocol declares that a user-defined record has value semantics -- it is small, cheaply copyable, and behaves like a built-in value type (Int32, bool, etc.). Import it from the `tpy` module:

```python
from tpy import Int32, ValueType

class Vec2(ValueType):
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
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

**Implicit conformance:** Built-in value types (Int32, bool, float, etc.) implicitly conform to `ValueType`, so they can be used as arguments for `T: ValueType` bounded type params without explicit declaration.

#### Working: `Default` Marker Protocol and `make_default()`

The `Default` marker protocol declares that a type supports zero-argument default construction. It maps to the C++20 `std::default_initializable` concept.

**Implicit conformance** -- the following types conform to `Default` without explicit declaration:
- All primitives: fixed-width integers, `float`, `bool`, `str`, `String`, `StrView`, `Char`, `int`
- `list[T]`, `dict[K, V]`, `Span[T]` (empty container is default)
- `Optional[T]` (`None` is default)
- `Array[T, N]` if element `T` satisfies `Default`
- `tuple[T1, T2, ...]` if all element types satisfy `Default`
- User records whose `__init__` has no required parameters (all params have defaults or no `__init__`)

**`make_default()`** is a portable function for default-constructing generic types:

```python
from tpy import Int32, make_default

# Explicit type argument
x = make_default[Int32]()    # -> Int32(0)

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
        self._storage.init(UInt32(self._size), make_default())
        self._size += 1
```

**`T()` deprecation warning:** Using `T()` for default construction of type parameters emits a warning recommending `make_default()` instead, since `T()` is not supported in CPython.

#### Working: `NativeIterable[T]` (C++ range-for optimization marker)

`NativeIterable[T]` is a **marker protocol** for types that support C++ range-based for loops. It's defined in the `tpy` module (not `typing`) because it maps to C++ `begin()`/`end()` iteration rather than Python's `__iter__`/`__next__` protocol. It is used internally by the compiler for codegen optimization (choosing direct range-for vs iterator adapter) but should generally not appear in user-facing function signatures -- use `Iterable[T]` from `typing` instead.

```python
from typing import Iterable
from tpy import Int32

def sum_all(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

# All built-in containers work:
nums: list[Int32] = [1, 2, 3]
print(sum_all(nums))  # 6
```

**Key characteristics**:
- **Marker protocol**: Types declare conformance via `extends`, no methods required
- **Auto-derivation from `__span__()`**: Any user type with `__span__() -> Span[T]` automatically conforms to `NativeIterable[T]`. The compiler synthesizes C++ `begin()`/`end()` methods that delegate to `__span__()`.
- **Built-in conformance**: All built-in container types extend both `NativeIterable[T]` and `Iterable[T]`
- **Codegen optimization**: The compiler uses NativeIterable extends to select zero-overhead C++ range-based for loops for built-in types
- **API methods use `Iterable[T]`**: `list.extend()`, `str.join()`, `list()` constructor, `dict()` constructor all accept `Iterable[T]`

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

**Codegen**: `for i in range(...)` is optimized to a C-style counter loop. `range()` accepts all fixed-width integer types (Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64) as well as BigInt, preserving the element type in the loop variable.

Bare integer literals use the configured default integer type (`--default-int`, default: `Int32`), so `range(10)` uses `Range<Int32>` by default. For CPython-like behavior, use `--default-int=BigInt`.

```cpp
// range(Int8(0), Int8(10)) -> int8_t loop
for (int8_t i = 0; i < 10; ++i) { ... }

// range(Int32(0), Int32(100), Int32(3)) -> int32_t loop with upfront overflow check
tpy::range_check_overflow<int32_t>(0, 100, 3);
for (int32_t i = 0; i < 100; i += 3) { ... }
```

Step ±1 uses `++i`/`--i` with no overflow check. Other steps use an upfront `range_check_overflow` that verifies the final increment won't overflow, then uses unchecked `i += step` in the hot loop. Variable steps use a ternary condition (`step > 0 ? i < stop : i > stop`). Generic `OptIterator[T]` parameters (not `range()` calls) still use the while-loop path:

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
| 6. Iterator[T]/Iterable[T] | **Working** | Built-in protocols from `typing`, for-loop support, `iter()`, `try_next()` builtins |
| 7. `__span__` protocol | **Working** | `__span__() -> Span[T]` for zero-cost range-based for, implicit Span coercion |
| 7b. `ReadOnlySpanLike[T]` protocol | **Working** | Readonly protocol for types with `__span__()`, for-loop and ReadOnlySpan coercion |
| 8. Generator expressions | **Working** | `(expr for x in iterable)` → lazy `make_generator` wrapper, satisfies `Iterable[T]` |
| 9. Generator functions | Open | `yield` → state-machine class implementing OptIterator |
| 10. Iterator combinators | Open | `enumerate()`, `zip()`, `filter()`, `map()`, `reversed()` |

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
- Direct `obj.__next__()` calls are forbidden -- use a for-loop or call `obj.__next_opt__()` instead
- `raise StopIteration` is only allowed inside `__next__` methods
- The `next()` builtin is not yet supported

#### Working: `Iterator[T]` and `Iterable[T]` Protocols

`Iterator[T]` and `Iterable[T]` are built-in protocols from the `typing` module, matching standard Python iterator protocol semantics.

**Protocol definitions**:
- `Iterator[T]`: has `__next__(self) -> T` and `__iter__(self) -> Self`
- `Iterable[T]`: has `__iter__(self) -> Iterator[T]`

```python
from typing import Iterator, Iterable
from tpy import Int32

def sum_iter(it: Iterator[Int32]) -> Int32:
    total: Int32 = 0
    for x in it:
        total += x
    return total

def sum_all(items: Iterable[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total
```

**`iter()` builtin**: Calls `x.__iter__()`, returns `Iterator[T]`. Works on any type with an `__iter__` method.

```python
it: Iterator[Int32] = iter(Counter(5))
```

**`try_next()` builtin**: Calls `it.__next_opt__()`, returns `T | None`. Provides a safe way to advance an iterator without exceptions.

```python
it = iter(Counter(5))
val = try_next(it)  # Int32 | None
```

**Auto-synthesis of `__iter__`**: Types that define `__next__` (or `__next_opt__`) but not `__iter__` automatically get `__iter__` synthesized, returning `self`. This matches Python's convention where iterators are their own iterables.

**Built-in `Iterable[T]` conformance**: All standard container and string types conform to `Iterable[T]`: `list[T]`, `Array[T, N]`, `Span[T]`, `ReadOnlySpan[T]`, `Range[T]`, `str`, `String`, `StrView` (as `Iterable[Char]`), `dict[K,V]` (as `Iterable[K]`), `dict_keys`, `dict_values`, `dict_items`. This enables passing any builtin container to generic functions accepting `Iterable[T]`, calling `__iter__()` explicitly, and using the `iter()` builtin. Direct `for` loops over these types still use fast range-based C++ iteration (`NativeIterable`) as an optimization.

#### Working: `__span__` Protocol (Zero-Cost User-Defined Iteration)

Types that define `__span__(self) -> Span[T]` or `__span__(self) -> ReadOnlySpan[T]` get zero-cost
C++ range-based for iteration, avoiding iterator object allocation:

```python
from tpy import Int32, Span

class IntBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [10, 20, 30]

    def __span__(self) -> Span[Int32]:
        return self._data

buf = IntBuffer()
for x in buf:       # compiles to: for (int32_t x : buf.__span__()) { ... }
    print(x)
```

**Dual overloads**: When `__span__` returns mutable `Span[T]`, the compiler generates two C++
overloads -- a non-const overload returning `std::span<T>` (user's body) and a const overload
returning `std::span<const T>` (thin wrapper). This enables correct iteration on both mutable
and `@readonly` references. When `__span__` returns `ReadOnlySpan[T]`, only a single const
overload is generated.

**Priority**: `__span__` currently takes precedence over `__iter__` when both are defined for
codegen purposes (direct range-based for).

**Implicit coercion**: A type with `__span__()` coerces to `Span[T]` or `ReadOnlySpan[T]`
when passed as a function argument. `ReadOnlySpan[T]` return cannot coerce to mutable `Span[T]`.

**CPython compatibility**: `__span__` is not meaningful in CPython. Span-backed types should
define `__iter__(self) -> SpanIter[T]` (returning `SpanIter(self.__span__())`) to provide
CPython-compatible iteration. `SpanIter[T]` is a builtin type in the `tpy` module that wraps
a span and implements `Iterable[T]`/`Iterator[T]`/`OptIterator[T]`/`NativeIterable[T]`.

#### Working: `ReadOnlySpanLike[T]` Protocol

`ReadOnlySpanLike[T]` is a readonly protocol for types that expose contiguous storage via
`__span__()`. It enables writing generic functions that accept any span-producing type:

```python
from tpy import Int32, Span, ReadOnlySpanLike

class Buffer:
    _data: list[Int32]
    def __init__(self) -> None:
        self._data = [1, 2, 3]
    def __span__(self) -> Span[Int32]:
        return self._data

def sum_all(c: ReadOnlySpanLike[Int32]) -> Int32:
    total: Int32 = 0
    for x in c:
        total += x
    return total

# Works with user types, list, Array, Span, ReadOnlySpan
print(sum_all(Buffer()))           # 6
data: list[Int32] = [10, 20, 30]
print(sum_all(data))               # 60
```

**Conformance**: Any type with `__span__() -> Span[T]` or `__span__() -> ReadOnlySpan[T]`
structurally conforms. Builtins (list, Array, Span, ReadOnlySpan) also conform
via explicit `extends` declarations. User types implementing `__span__() -> Span[T]` satisfy
the readonly protocol via covariant return (the compiler auto-generates a const overload).

**Coercion**: `ReadOnlySpanLike[T]` values coerce to `ReadOnlySpan[T]` (not mutable `Span[T]`).

**CPython mixin**: In CPython stubs, `ReadOnlySpanLike` is a base class that auto-generates
`__iter__` from `__span__()`. User types inheriting it get iteration for free in CPython.

**Bounded type parameters**: `Iterator[T]` and `Iterable[T]` can be used as type parameter bounds:

```python
from typing import Iterable
from tpy import Int32

def sum_generic[T: Iterable[Int32]](items: T) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total
```

**For-loop support**: `for x in expr` works when `expr` has type `Iterator[T]` (uses while-loop via `__next_opt__`) or `Iterable[T]` (calls `__iter__()` first, then iterates the resulting iterator).

#### Working: Span coercion via `ReadOnlySpanLike[T]`

Types extending `ReadOnlySpanLike[T]` (which provides `__span__() -> ReadOnlySpan[T]`) can be implicitly coerced to `Span[T]`. This unifies span coercion with the `ReadOnlySpanLike` protocol -- no separate marker protocol is needed.

```python
from tpy import Int32, Span, Array

def sum_span(values: Span[Int32]) -> Int32:
    total: Int32 = 0
    for v in values:
        total += v
    return total

# All these work - Array, list extend ReadOnlySpanLike[T]
arr: Array[Int32, 3] = [1, 2, 3]
sum_span(arr)  # OK

lst: list[Int32] = [4, 5, 6]
sum_span(lst)  # OK

# str does NOT extend ReadOnlySpanLike - this is an error
# s: str = "hello"
# takes_span(s)  # ERROR: str does not extend ReadOnlySpanLike
```

**Key points:**
- **Built-in conformance**: `list[T]`, `Array[T, N]`, `Span[T]` extend `ReadOnlySpanLike[T]`
- **str excluded**: `str` iterates over `Char` but doesn't extend `ReadOnlySpanLike` (design choice)
- **Zero overhead**: Uses C++ `std::span` implicit construction from contiguous ranges

#### Compiler Traits Summary

Protocols serve as **compiler traits**—letting the compiler discover type capabilities without hardcoded type checks:

- `len(x)` works on any type conforming to `Sized` ✓ (working)
- `Sequence[T]` for types supporting `len()` and indexing ✓ (working)
- `for` loops work on `Iterable[T]`-typed parameters ✓ (working)
- `for` loops work on `NativeIterable[T]`-typed parameters ✓ (working)
- `for` loops work on `OptIterator[T]`-typed parameters ✓ (working)
- Implicit coercion to `Span[T]` works on types extending `ReadOnlySpanLike[T]` ✓ (working)
- `for` loops work on `Iterator[T]`-typed parameters ✓ (working)
- `list.extend()`, `str.join()`, `list()`, `dict()` accept `Iterable[T]` params ✓ (working)

See [docs/PROTOCOL_DESIGN.md](PROTOCOL_DESIGN.md) for the full design, including implementation phases and C++ codegen strategies.

### Union/Optional
- **Working**: Union types `A | B | C` → `std::variant<A, B, C>`
  - Two-way, three-way, and n-way unions in annotations (function params, returns, local variables)
  - Canonical member ordering (sorted by type name, `None`/`std::monostate` always first)
  - `A | None` with single non-None type still produces `Optional[T]` (backward compatible)
  - Member type compatibility: `T` assignable to `T | U`, `T | U` assignable to `T | U | V`
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
  - `assert isinstance(x, T)` narrowing: `std::get<T>` extraction persists for the rest of the scope
  - Compound conditions: `isinstance(x, T) and x.field > 0` narrows `x` on the RHS of `and`
  - `isinstance(x, T) or x.other_field > 0` narrows `x` to remaining members on `or` RHS
  - Negative (else-branch) narrowing: remaining union members after isinstance check
  - Chained elif isinstance for multi-way branching (3+ member unions) -- full codegen support for 2-way, 3-way, and n-way elif chains
  - Narrowed variables can be used for field access, method calls, and passed to functions expecting the member type
  - Implicit union wrapping at call sites: passing `A` to a parameter of type `A | B` auto-wraps into `std::variant`
  - `std::get<T>` extraction emitted once at block entry for efficient narrowed access
  - `while isinstance(x, T)` narrows `x` to `T` inside the loop body (same extraction as if-blocks)
  - Assignment narrowing: `v: A | B = A(...)` narrows `v` to `A` so field access works without isinstance; uses inline `std::get<T>()` at access points (not aliased, so `v` can still be passed to functions expecting the full union)
  - Assignment narrowing is cleared on reassignment (`v = B(...)` clears the `A` narrowing)
  - Value-type semantics: all-value unions (`int | bool`) pass as `const&`; unions with records pass as `&` (mutable)
  - Nullable unions: `A | B | None` maps to `std::variant<A, B, std::monostate>`
  - `v is None` / `v is not None` on nullable unions: `std::holds_alternative<std::monostate>(v)`
  - `is not None` narrows to remaining non-None members; chained isinstance further narrows
  - `v == None` / `v != None` on nullable unions errors with hint to use `is`/`is not`
  - Type aliases: `Shape = Circle | Rect` (old-style assignment) and `type Shape = Circle | Rect` (Python 3.12 `type` statement)
  - Aliases resolve eagerly at parse time to the underlying union type; sema and codegen see the expanded type
  - Old-style aliases support forward references (alias before class definitions)
  - Aliases can include `None` members: `MaybeShape = Circle | Rect | None`
  - Emits C++ `using Shape = std::variant<Circle, Rect>;` in the header
  - `isinstance(x, Shape)` where `Shape` is a type alias is not yet supported; isinstance on concrete member types only
  - Type aliases can be imported cross-module: `from shapes import Shape`
  - **Not yet supported**: `isinstance(x, (A, B))` tuple form, `isinstance(x, Protocol)` on concrete-typed variables
  - **Working**: `match`/`case` pattern matching on union subjects (see Control Flow > Other)
- **Working**: `T | None` for non-value types (records, lists, arrays) → nullable pointer (`T*`)
  - Locals, parameters, returns: `T*` (nullable pointer)
  - `x is None` / `x is not None` for null checks
  - Field/method/subscript access on unproven optional values emits a warning and inserts a runtime null check
  - Guarded paths (`if x is not None`) and `assert x is not None` narrow `x` to `T`
  - Returning narrowed optional values: `if x is not None: return x` correctly unwraps to `T`
  - Field narrowing: `if obj.field is not None:` narrows `obj.field` to `T` in the guarded scope
  - Field narrowing facts are invalidated when the root object is passed by mutable reference to a function call
  - Functions returning `T | None` return `T*` in C++
- **Working**: `Optional[T]` from `typing` is equivalent to `T | None` at parse time
  - `from typing import Optional` then `Optional[Int32]` produces the same type as `Int32 | None`
  - Works in all positions: parameters, returns, local annotations, class fields
- **Working**: `T | None` in generic contexts → `std::optional<T>` (C++ templates)
  - Generic type parameters (`TypeParamRef`) use `std::optional<T>` representation, not `T*`
  - Ensures correct codegen when the inner type may be either value or reference at instantiation time
  - `None` literals in generic Optional positions emit `std::nullopt` (not `nullptr`)
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
  - Literal-seeded variables default to the configured default integer type (`--default-int`, default: `Int32`) and may be refined by later writes.
  - `bool` does not auto-merge with numeric families during inference.
  - Augmented assignment currently does not perform literal anchoring (`x = 0; x += Int32(5)` remains `int`/`BigInt`).
  - For `x = 0` style literal-seeded vars, `x += Int32(...)` emits a warning that augmented assignment does not narrow the variable type.
  - The warning also applies when RHS is a function returning `Int32`; explicit `int` anchors (`x: int = 0`, `x = int(0)`) do not warn.
- **Working**: Numeric widening across reassignments
  - `x = Int32(1); x = Int64(2)` infers `Int64` (same-sign, wider wins)
  - `x = Int32(1); x = 1.5` infers `float` (any integer + float -> float)
  - `x = 1.5; x = Int32(1)` stays `float` (bidirectional -- order doesn't matter)
  - `x = Int32(1); x = int(2)` infers `int`/BigInt (FixedInt + BigInt -> BigInt)
  - `x = UInt8(1); x = Int32(2)` infers `Int32` (unsigned -> wider signed)
  - Mixed sign same width is an error: `x = Int32(1); x = UInt32(2)` (requires annotation)
  - Bool mixed with numeric is an error: `x = True; x = Int32(1)` (requires annotation)
- **Working**: Optional class members (`self.field: T | None`) → `std::optional<T>` inline storage
  - Field access through optional (`obj.field.x`) works via `std::optional::operator->()`
  - `is None` / `is not None` checks use `.has_value()`
  - Boundary conversions between `std::optional<T>` fields and `T*` pointer-locals handled automatically
- **Working**: Optional-aware operator checks for value-consuming expressions (e.g., `x + 1` where `x: Int32 | None`)
  - Unproven use emits warning and inserts runtime null checks
  - Proven non-None paths (guard/assert narrowed) emit unchecked unwraps
- **Working**: None-safe `==`/`!=` for Optional value types
  - `Optional[T] == T`, `Optional[T] != T`, `Optional[T] == Optional[T]` work without warnings or runtime panics
  - `None == 5` -> `False`, `None != 5` -> `True` (matches Python semantics)
  - Ordering operators (`<`, `>`, `<=`, `>=`) still use conservative runtime checks
- **Open**: `T | U` → templates with `if constexpr`, or overloads

For details, see [docs/NONE_SAFETY.md](NONE_SAFETY.md).

---

## Operators

### Arithmetic
- **Working**: `+`, `-`, `*`, `//`, `%`, `/`, unary `-`
- **Working**: `**` (power) for `int`, `Int32`, and `float`
- **Working**: `**` with negative integer exponent (e.g., `2 ** -3`) - compiles but panics at runtime (use `2.0 ** -3` for float result)

### Comparison
- **Working**: `==`, `!=`, `<`, `<=`, `>`, `>=`
- **Working**: Chained comparisons (`a < b < c`, `a <= b <= c`, `1 < x < 10`, etc.) -- each operand evaluated exactly once
- **Working**: `is`, `is not` (identity comparison with `None` only)
- **Working**: Mixed `int`/`float` comparisons (BigInt promoted to double)

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
- **Working**: Ternary `x if cond else y` -- same-type branches, numeric widening, `T`+`None` to `Optional[T]`, Optional narrowing (`is not None` / truthy)

### Loops
- **Working**: `while`
- **Working**: `for i in range(n)`, `for i in range(start, end)`, `for i in range(start, end, step)`
- **Working**: `for item in container` (for-each over list, Array, Span, str)
- **Working**: `for x in iterator` (for-each over OptIterator types -- user-defined iterators)
- **Working**: `for x in iter_param` (for-each over `Iterator[T]` and `Iterable[T]` protocol-typed parameters)
- **Working**: `break`, `continue`
- **Working**: Reassigning loop variables inside for-loop body (compiles as assignment, not redeclaration; note: affects iteration unlike Python)
- **Open**: `for/else`, `while/else` → flag variable pattern

### Other
- **Working**: `return`, `pass`
- **Working**: `del obj[key]` -- element deletion via `__delitem__` dunder (dict, list, user types)
- **Partial**: `match`/`case` -- structural pattern matching
  - **Union subjects**: class patterns (`case Circle():`, `case Circle(radius=r):`), subject narrowing, `switch (s.index())` codegen with `std::get<N>`
  - **Enum subjects**: value patterns (`case Color.Red:`), `switch` codegen
  - **Primitive subjects** (`int`, `bool`): literal patterns, `switch` codegen; (`str`, `float`): if/elif fallback
  - **Record subjects**: field-value matching (`case Point(x=0, y=0):`), if/elif codegen; `goto`-based fallthrough for guards
  - **Optional subjects**: `case None:` + literal/class/value patterns on inner type; if/elif with `has_value()`/`nullptr`; value-type and pointer-repr
  - Wildcard (`case _:`), capture (`case x:`), as-pattern (`case Dog() as d:`)
  - Keyword and positional field bindings in class patterns (`case Rect(w, h):`, `case Point(x, y, z=pz):`)
  - Positional patterns resolved via field declaration order (implicit `__match_args__` for all records, not just `@dataclass` -- CPython extension)
  - Pattern binding scope: bindings leak to enclosing scope (like Python), pre-declared when defined in all arms
  - Or-patterns (`case Dog() | Cat():`) with `switch` case fallthrough; with bindings (`case Dog(name=n) | Cat(name=n):`) via body duplication in switch (str/float fall back to if/elif with `||`)
  - Guard clauses (`case Dog(name=n) if n == "Rex":`) with `goto`-based fallthrough for unions and records, `switch` with if/else guard chains + `goto` to default for enums/int/bool, `&&` inlining for str/float
  - Error diagnostics: non-member type, duplicate case, type mismatch, unreachable case after wildcard, too many positional patterns, positional/keyword overlap, or-pattern variable name mismatch, wrong class for record subject
  - **Not yet**: exhaustiveness warnings

---

## Functions

### Definition
- **Working**: Typed parameters and return types
- **Working**: Reassigning function parameters (const-ref params like `int`/`str` auto-emit by value when reassigned)
- **Working**: C++ keyword escaping -- Python identifiers that clash with C++ reserved words (e.g., `default`, `class`, `namespace`) are automatically mangled in generated code
- **Working**: Default parameter values -- constant expressions (literals, `None`, fixed-int constructors). Maps to C++ default arguments. Defaults on generic type parameters are validated at instantiation time: `def f[T](x: T = 0)` called as `f[str]()` produces a clear sema error. `T()` default-construction syntax is supported: `def f[T](x: T = T()) -> T` maps to `T{}` in C++.
- **Working**: Keyword arguments at call sites -- `f(name="World")`, `Point(y=1, x=2)`, mixed positional+kwargs. Resolved to positional at compile time. Supported for user functions, methods, constructors, and generic functions. Not supported for overloaded builtins (e.g., `range`, `len`).
- **Open**: `*args` → variadic templates or fixed overloads
- **Open**: `**kwargs` definition syntax → if keys known at compile time

### Decorators
- **Working**: `@noalloc` (parsed and recognized, enforcement planned)
- **Working**: `@readonly` ("doesn't mutate its arguments" contract on functions/methods)
  - C++ codegen: `@readonly` non-value params emit `const T&` (or `const T*` for Optional aliases), matching sema-level enforcement
  - Type-based enforcement: `ReadonlyType` wraps non-value params; field access, subscript, and method calls propagate readonly through expressions
  - Local alias deduction: `alias = param` preserves `ReadonlyType` through variable assignments
  - Constructors, `print`, I/O, and global writes are generally allowed; constructors follow the same call rule when passed param-derived mutable refs
  - Value-type arguments are copies and can be freely passed to any function
  - Implicit readonly on dunders: `__len__`, `__getitem__`, `__eq__`, arithmetic operators, etc.
  - `@readonly(False)` opts out of implicit readonly (e.g., `__getitem__` that caches)
  - Readonly methods returning references get both const and non-const C++ overloads; value returns get const only
  - Protocol method readonly: `@readonly` on protocol methods enforces that implementations are also readonly; conformance fails at sema time if a record's method is not readonly when the protocol requires it
  - Protocol concept generation: user-defined protocols where all methods are readonly generate `const T&` in C++ concepts
  - Deep readonly for pointers: accessing a `Ptr[T]` field through a readonly receiver yields `ReadOnlyPtr[T]`, preventing mutation through pointer fields
  - Limitation: container-mediated aliases not tracked (e.g., `[param]` into list then iterate)
- **Working**: `readonly[T]` type modifier (per-parameter constness)
  - `readonly[T]` on a parameter means "immutable reference to T", maps to `const T&` in C++
  - Prevents: field writes, subscript writes, non-readonly method calls, passing to mutable `T` param
  - Local alias deduction: `alias = readonly_param` inherits readonly status for non-value types
  - `readonly[Int32]` (value types) is a no-op -- copies are always safe
  - `readonly[T | None]` and `readonly[T] | None` normalize to the same C++ type (`const T*`)
  - `readonly[Protocol]` generates `const T_name&` for template protocol params
  - See `docs/READONLY_DESIGN.md` for full design rationale
- **Working**: `@pure` (no observable side effects -- no mutation of non-local state, no I/O)
  - Trusted annotation (Phase 1): no enforcement, metadata only for future borrow checker / escape analysis
  - Pure implies readonly -- `@pure` methods can be called on `readonly` receivers
  - Heap allocation is permitted (not considered an observable side effect); `@noalloc` is orthogonal
  - Marked on built-in functions (`len`, `repr`, `hash`, `chr`, `ord`, `pow`, `round`, `divmod`, `abs`, `min`, `max`, `range`, `iter`), all `math.*` functions, and all readonly methods on builtin types
  - Supported on user functions and methods via `from tpy import pure`
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
has `delta: Int64`, passing `Int32` for `delta` is accepted via normal
argument coercion.

**Contextual Type Inference**: When arguments don't fully determine all type
parameters, the expected type from context (assignment annotation, return type,
reassignment, field assignment) fills in the remaining params:

```python
class Container[T]:
    val: T

def make_box[T]() -> Own[Container[T]]:
    return Container[T]()

# Assignment context: T inferred from annotation
b: Container[Int32] = make_box()     # T = Int32

# Record constructor context: T inferred from annotation
c: Container[Int32] = Container()    # T = Int32

# Return context: T inferred from enclosing function return type
def get_box() -> Own[Container[Int32]]:
    return make_box()                # T = Int32
```

Contextual inference also unwraps `Own[T]` on both sides, so `Own[Container[T]]`
matches `Container[Int32]` correctly. If no context is available, inference
still fails and explicit type arguments are required.

**Nested call context**: parameter types from outer calls flow as hints to inner
calls. This means `sink(make_box())` infers T from `sink`'s parameter type.
Record constructor arguments also propagate hints to inner generic calls.

**Partial explicit type args**: you can provide some type arguments and let the
compiler infer the rest from arguments or context:

```python
from tpy.unsafe import unsafe_cast
q = unsafe_cast[UInt32](p)  # T=UInt32 explicit, U=Int32 from arg

# Also works with module.func[T](args) syntax
import tpy.unsafe as m
q = m.unsafe_cast[UInt32](p)
```

**Not yet supported**: inference from field assignment targets (`self.field = expr`)
or subscript targets (`items[i] = expr`). See
`docs/BIDIRECTIONAL_CALL_INFERENCE_DESIGN.md` for the full design.

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
- `Deref[T]` - has `__deref__() -> T` (auto-deref for field/method access)
- `Covariant[T]` - marker: type param T is covariant, enables `G[Child] -> G[Parent]` coercion
- `Iterable[T]` - has `__iter__` returning `Iterator[T]` (standard Python iterable protocol, used in method signatures)
- `Iterator[T]` - has `__next__` and `__iter__` (Python iterator protocol)
- `OptIterator[T]` - lazy iteration via `next() -> Optional[T]`
- `NativeIterable[T]` - codegen optimization marker for C++ range-for iteration
- User-defined protocols (including protocols with inheritance)

**Protocol Inheritance with Bounds**: When using a child protocol as a bound (e.g., `T: PrintableAndSized`), methods from all ancestor protocols are available on `T`.

**Method-Level Type Parameters**: Methods can have their own type parameters, independent of the class:
```python
class Converter:
    def identity[U](self, val: U) -> U:
        return val
```
Supports inference from arguments (`c.identity(42)`) and explicit type args (`c.identity[Int32](42)`).

**Per-Method Bounds**: Methods on generic classes can add extra bounds to class type params:
```python
class Container[T]:
    def is_sorted[T: Comparable](self) -> bool: ...  # only when T is Comparable
```

---

## Classes

### Definition
- **Working**: Typed fields
- **Working**: `__init__`
- **Working**: Instance methods
- **Working**: Generic classes (Python 3.12+ syntax)
- **Working**: `@staticmethod` → static methods (including on generic classes with type inference)
- **Working**: Method-level type parameters (`def transform[U](self, val: U) -> U`), including on `@staticmethod`
- **Working**: Per-method type parameter bounds (`def is_sorted[T: Comparable](self) -> bool`)
- **Working**: Single class inheritance (`class Child(Parent)`)
- **Working**: Generic inheritance (`class Child[T](Parent[T])`)
- **Working**: Explicit protocol implementation (`class MyList(Sequence[T])`)
- **Working**: Docstrings in class and method bodies (silently ignored)
- **Working**: `@dataclass` decorator (`from dataclasses import dataclass`) -- auto-generates `__init__`, `__eq__`, `__repr__`, and `__hash__` (frozen only) from field annotations; `frozen=True` for immutable instances usable as dict keys; `order=True` for lexicographic comparison via `operator<=>`; `field(default=X)` and `field(default_factory=X)` for mutable defaults; dataclass inheritance (child inherits parent fields into all synthesized methods)
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
Pair<std::string, int32_t> pair{"hello", 100};
```

**Type Inference**:
- Type arguments can be inferred from constructor arguments: `Box(42)` -> `Box[int]`
- Inference works when all type parameters can be determined from arguments
- Contextual inference from assignment annotation, return type, reassignment, or nested call context fills unresolved params
- If inference fails, explicit type arguments are required
- When mixing int literals with `Int32`, inference upgrades to `Int32`: `Same(1, x: Int32)` → `Same[Int32]`
- Supports inference through wrapper types: `Ptr[T]`, `ReadOnlyPtr[T]`, `Own[T]`, `list[T]`
- `Ptr[T]` arguments match `ReadOnlyPtr[T]` parameters (follows coercion rules)

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
b2 = Box[Int32].from_optional(None)     # Explicit: T = Int32
```

Generated C++: `Box<int32_t>::from_optional(42)`, `Box<int32_t>::from_optional(std::nullopt)`.

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
Container<std::string, 10> c{};
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
- Single class inheritance only (multiple class inheritance is an error)
- Use `super().__init__(args)` to call the parent constructor
- Method override works by simply defining a method with the same name
- Inherited fields and methods are accessible via `self.field` and `self.method()`

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
a: Animal = d        # value upcast
greet(d)             # param passing (child -> parent)
```

**Pointer coercion** also works -- a child can be used where a pointer to parent is expected:

```python
from tpy import Ptr, ReadOnlyPtr

def read_animal(p: ReadOnlyPtr[Animal]) -> None:
    print(p.name)

d = Dog("Rex", "Lab")
read_animal(d)                    # Dog -> ReadOnlyPtr[Animal]
dp: Ptr[Dog] = Ptr(d)
ap: Ptr[Animal] = dp             # Ptr[Dog] -> Ptr[Animal]
cap: ReadOnlyPtr[Animal] = dp       # Ptr[Dog] -> ReadOnlyPtr[Animal]
```

Generic parent upcasting is supported with type argument matching:

```python
class IntContainer(Container[Int32]):
    ...

ic = IntContainer(Int32(42))
c: Container[Int32] = ic         # upcast to generic parent
```

**Note:** Covariant containers (`list[Dog] -> list[Animal]`) are not supported -- they are unsafe because the target list could be modified with incompatible types.

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
- `super()` not supported for builtin type parents (`list`, etc.) - use implicit default construction instead

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
from tpy import Int32

class IntStack(list[Int32]):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def push(self, value: Int32) -> None:
        self.append(value)  # Inherited from list

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

**Supported builtin parents:**
- `list[T]` - dynamic list
- `Array[T, N]` - fixed-size array (planned)

**Key points:**
- Inherited methods from builtins work automatically (e.g., `append`, `__getitem__`, `__len__`)
- Type parameters are substituted with concrete types (e.g., `T` -> `Int32`)
- No `super().__init__()` needed - C++ base class default constructor is called automatically
- Can add custom fields and methods to the child class
- Subscript (`stack[i]`) and `len(stack)` work on child types

**Limitations:**
- Generic parent without type args rejected (`class Child(Parent)` where `Parent[T]` is generic)
- No `super()` calls - child must initialize parent fields directly
- Cannot inherit from builtins with forwarded type parameters (`class Child[T](list[T])`)

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
- **Working**: `__del__` -- maps to C++ destructor `~ClassName()`. Parent destructors are called automatically after child body (no `super().__del__()` needed). If `super().__del__()` is written (Python style), it must be the last statement and is silently dropped in codegen. A warning is emitted when parent has `__del__` but child omits the call, since C++ always calls parent dtors automatically while Python requires an explicit call. Non-virtual; virtual dispatch is a separate feature. Classes with `__del__` get a hidden `__tpy_owned_` drop flag and custom move constructor/assignment to prevent double-drop after move -- the moved-from object's destructor skips its body.
- **Working**: `__str__` -> `str(obj)`, `print(obj)`, `f"{obj}"` (via `Stringable` protocol / `tpy::__str__`)
- **Working**: `__repr__` -> `repr(obj)`, `f"{obj!r}"` (via `Representable` protocol / `tpy::__repr__`)
- **Open**: `__setitem__` -> mutable `operator[]`
- **Open**: `__enter__`, `__exit__` -> RAII wrapper

---

## Built-in Functions

- **Working**: `print()`, `len()`, `hash()`, `range()`, `chr()`, `ord()`, `pow()`, `round()`, `divmod()`, `copy()`
  - `print("x", end="")` supported (`end` must be a string literal; dynamic `end` not yet supported)
  - Container printing matches Python format: bools as `True`/`False`, floats with `.0`, strings in `'quotes'`
- **Working**: `str()`, `repr()`, f-strings on containers (tuple, list, dict, Array, Span) -- uses runtime to_str helpers matching `print()` format
  - Generic type parameters use `ValuePrinter` for runtime dispatch (bool/float correctly formatted)
- **Working**: List methods: `append()`, `pop()`, `insert()`, `remove()`, `clear()`, `extend()`
  - **Note**: `remove(value)` silently does nothing when value not found (Python raises `ValueError`)
- **Working**: List repetition: `[element] * N` and `[elements...] * N`
  - Single-element: uses efficient fill constructor
  - Multi-element: uses `tpy::repeat_range` to repeat the sequence N times
  - Deferred resolution for untyped locals via `PendingListType`:
    - Constant count, unmutated -> `Array[T, N]` (stack-allocated, supports subscript)
    - Variable count, unmutated -> lazy `repeat[T]` (`tpy::repeat_range<T>`, no allocation)
    - Variable count with subscript -> auto-promoted to `list[T]`
    - Mutated (`.append()`, etc.) -> auto-promoted to `list[T]`
  - Lazy `repeat[T]` conforms to `Iterable[T]`, `Sized`, `NativeIterable[T]`
  - Explicit annotation (`z: list[T] = [v]*N`) always produces `list[T]`
  - Inline repeat cannot be passed directly to `Span` -- assign to a variable first
- **Working**: Negative indexing for list, Array, Span: `items[-1]` (last element)
- **Working**: `hash(x)` → `UInt64` hash value. Works on all `Hashable` types (str, int, fixed ints, float, bool, Char, Enum). Uses `tpy::__hash__()` free function dispatch.
- **Working**: `abs()`, `min()`, `max()`, `ord()`, `pow()`, `round()`, `divmod()` for numeric types
  - `round(x)` uses banker's rounding (round half to even, matching Python)
  - `round[T](x)` is generic: return type defaults to `default_int`, can be inferred from context
  - `divmod(a, b)` returns `tuple[T, T]` with Python floor-division semantics
- **Working**: String slicing: `s[1:3]`, `s[:3]`, `s[1:]`, `s[:-1]` -- returns `StrView`, Python clamping semantics, no step yet
- **Planned**: List slicing: `items[1:3]`
- **Working**: `isinstance(x, T)` → compile-time type narrowing for union types (`std::holds_alternative<T>` + `std::get<T>`)
- **Working**: `isinstance(x, Protocol)` → compile-time protocol check on protocol-typed template params (`if constexpr (Concept<T_x>)`)
- **Open**: `type()` → compile-time type info
- **Working**: `list()` → empty list constructor (requires type annotation), `list(iterable)` from Iterable containers, `list(range(...))`, `list(iterator)` from OptIterator
- **Working**: `int(float)` → truncates toward zero, panics on NaN/infinity
- **Working**: `float(int)`, `float(Int32)` → converts to float
- **Working**: `str()` → string conversions for scalars, containers, and Stringable/Representable types (see below)
- **Working**: `int(str)` → string-to-int parsing (via `BigInt::from_str`)
- **Working**: `iter(x)` → calls `x.__iter__()`, returns `Iterator[T]`
- **Working**: `try_next(it)` → calls `it.__next_opt__()`, returns `T | None` (safe iterator advancement)
- **Working**: `make_default[T]()` / `make_default()` → default-constructs `T` (maps to `T{}` in C++). Requires `T: Default`. Type can be explicit or inferred from context. Portable alternative to `T()`.
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

// list(container) - from Iterable (range, array, list, etc.)
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

- **Working**: `from tpy import ...` (built-in types like `Int32`, `Span`, `Array`)
  - **Note**: tpy types require explicit import -- using `Int32` without `from tpy import Int32` produces an error with a helpful suggestion
- **Working**: `from typing import ...` (type annotations like `Optional`, `Protocol`, `Self`, `Sized`, `Sequence`, `MutableSequence`, `Iterator`, `Iterable`)
  - **Note**: typing names require explicit import -- using `Optional` without `from typing import Optional` produces an error with a helpful suggestion
- **Working**: `import time` and `from time import time`
- **Working**: Import aliases: `from time import time as get_time`, `from tpy import Int32 as I32`, `from typing import Optional as Opt`
  - Aliases work for both type annotations (`x: I32`, `x: Opt[I32]`) and constructor calls (`I32(42)`)
- **Working**: Module-level aliases: `import time as t`, `import tpy as tp`, `import typing as t`
  - Qualified type annotations work: `tp.Int32`, `t.Optional[tp.Int32]`, `typing.Protocol`
  - Requires `import` (not `from ... import`): `import tpy` or `import tpy as tp`
- **Working**: `import sys` - system module with `sys.argv`
- **Working**: `import math` - mathematical functions
- **Working**: Namespace wrapping for modules (each module gets its own C++ namespace)
- **Working**: User-defined modules (multi-file projects)
- **Working**: Package support (dotted imports, `__init__.py`, namespace packages)
- **Working**: Relative imports (`from . import sibling`, `from ..pkg import func`)
- **Working**: Re-exports in `__init__.py` (functions, records, protocols, variables)
- **Working**: Shadowing warnings -- defining a class that shadows an imported special name (e.g., `class Sized` after `from typing import Sized`) emits a warning
- **Working**: Standard library infrastructure (`tplib`, `stdlib`) with `-L` search paths
- **Working**: `tplib.Box[T]` -- heap-allocated owning container (via `from tplib import Box`)
- **Working**: `tplib.ArrayList[T, N]` -- fixed-capacity list with ownership-correct element lifecycle (iterable via `for x in list`)
- **Working**: `tplib.FixStr[N]` -- fixed-capacity string with stack-allocated storage (char-level operations, `__str__` for zero-copy printing)
- **Working**: `bisect` module -- array bisection algorithms (via `from bisect import bisect_left`)
- **Open**: `from typing import *` (not supported)

### User-Defined Modules (Working)

TurboPython supports importing from other `.py` files in the same directory:

```python
# utils.py
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
# main.py
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

**Circular imports:** Detected at compile time with clear error messages.

**Shadowing builtin modules:** User modules can shadow builtin modules (`math`, `time`, `sys`). If you create `math.py` in your project, `from math import ...` will use your module instead of the builtin. A warning is emitted:
```
main.py:1: warning: import 'math' shadows builtin module
```

**C++ mapping:** Each module gets its own namespace (`tpy_user::utils::Point`). Cross-module references use fully qualified names. Package modules use nested namespaces (`tpy_user::mypackage::submod::func`).

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
- `mypackage/__init__.py` → `namespace tpy_user::mypackage`
- `mypackage/utils.py` → `namespace tpy_user::mypackage::utils`
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
# main.py - import from package level
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

### Standard Library Infrastructure (Working)

TurboPython has two library search roots that provide reusable modules:

- **`tplib`** -- TPy-specific standard library (`from tplib import Box`)
- **`stdlib`** -- Python stdlib analogs (`from bisect import bisect_left`)

**Module resolution order** (first match wins):
1. Entry point directory (user modules)
2. `-L` paths (user-specified, in order)
3. `lib/tpy/` (tplib, tpy ecosystem)
4. `lib/stdlib/` (Python stdlib analogs)
5. Hardcoded builtins as fallback (`math`, `time`, `sys`)

**CLI flags:**
- `-L /path` -- add extra library search path (can be repeated)
- `--no-tplib` -- disable tplib standard library
- `--no-stdlib` -- disable Python stdlib analogs

**Available tplib modules:**

| Module | Description |
|--------|-------------|
| `tplib.Box[T]` | Heap-allocated owning container (similar to Rust's `Box<T>`) |
| `tplib.ArrayList[T, N]` | Fixed-capacity list with stack-allocated uninitialized storage; full list API (`append`, `pop`, `insert`, `index`, `count`, `remove`, `reverse`, `sort`, `swap`, `truncate`, `extend`, `clear`, `__contains__`, `__eq__`, `__repr__`) |
| `tplib.FixStr[N]` | Fixed-capacity string with stack-allocated storage; `__str__() -> StrView` for zero-copy printing |

```python
from tplib import Box
from tpy import Int32

b = Box[Int32](42)
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

**Available stdlib modules:**

| Module | Functions |
|--------|-----------|
| `bisect` | `bisect_left`, `bisect_right`, `insort_left`, `insort_right` |

```python
from bisect import bisect_left, insort_left
from tpy import Int32

a: list[Int32] = [1, 3, 5, 7]
pos = bisect_left(a, Int32(4))  # 2
insort_left(a, Int32(4))        # a = [1, 3, 4, 5, 7]
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

## Native Interop (Partial)

TurboPython can import existing C/C++ functions and export its own functions with C linkage. See `docs/NATIVE_INTEROP.md` for the full design document.

### Native Functions (Working)

Three decorators, all imported from `tpy.extern`:

```python
from tpy.extern import native, native_c, extern_c
from tpy import Int32

# Import a C++ function (forward declaration)
@native
def global_func(x: Int32) -> Int32: ...

# Import a C++ function with qualified name
@native("physics::calculate_force")
def calc_force(mass: float, accel: float) -> float: ...

# Import a C function (extern "C" linkage)
@native_c
def abs(x: Int32) -> Int32: ...

# Import a C function with renamed symbol
@native_c("clock")
def get_clock() -> Int32: ...

# Export a TPy function with C linkage
@extern_c
def app_init() -> None:
    print("initialized")

# Export with renamed symbol
@extern_c("app_tick")
def game_tick(time: Int32) -> None:
    print(time)
```

Cross-module imports of native functions work normally — the compiler re-declares extern symbols in each module.

### Native Classes (Working)

Import existing C++ classes and C structs so TPy code can declare their fields, call their methods, and pass them to native functions. No struct definition is generated — the compiler trusts the external type exists.

```python
from tpy.extern import native, native_c
from tpy import Int32, Float

# @native — C++ class import (constructor call syntax)
@native
class Vec2:
    x: Int32
    y: Int32
    def sum(self) -> Int32: ...        # stub method (... body)
    def dot(self, other: Vec2) -> Int32: ...
    @staticmethod
    def zero() -> Vec2: ...            # static method

# @native with rename — fully qualified C++ name
@native("b2::Vec2")
class PhysVec:
    x: Float
    y: Float
    def length(self) -> Float: ...
    @native("mag")
    def magnitude(self) -> Float: ...  # method rename

# @native_c — C struct import (aggregate init syntax)
@native_c
class Point:
    x: Int32
    y: Int32
    def manhattan(self) -> Int32: ...

# @native_c with rename — C name differs from Python name
@native_c("SDL_Rect")
class Rect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32
    def area(self) -> Int32: ...

# Opaque handle — no fields
@native("SDL_Window")
class Window: ...
```

Generated C++:
- `@native` classes use constructor call syntax: `Vec2(1, 2)`
- `@native_c` classes use aggregate initialization: `Point{5, 6}`
- Renamed types use the native name everywhere, including composite types like `Ptr[Rect]` → `SDL_Rect*`
- Methods on native classes must have `...` body (stub declarations); methods with real bodies produce a parse error
- `@native("cpp_name")` on methods allows renaming individual methods

### Final Constants (Working)

Compile-time constant globals using `Final[T]` from Python's `typing` module:

```python
from typing import Final
from tpy import Int32, Char

MAX_SIZE: Final[Int32] = 100
PI: Final[float] = 3.14159
DEBUG: Final[bool] = True
NAME: Final[str] = "hello"
LETTER: Final[Char] = "A"
BASE: Final[Int32] = 10
ALIAS: Final[Int32] = BASE  # cross-reference to another Final
BIG: Final[int] = 1000000   # BigInt
```

**Semantics** (stricter than Python's `Final`):
- Frozen binding: cannot be reassigned at module level, cannot use `global X` in functions
- Immutable value: treated as readonly (no mutation through the binding)
- Local shadowing allowed: functions can declare local variables with the same name
- Initializer must be a compile-time constant: literal or reference to a previously declared Final (no forward references)

**C++ mapping:**
- Constexpr-eligible types (fixed-width integers, float, bool, char, str): `inline constexpr T NAME = VALUE;` in header
- BigInt (`int`): `const tpy::BigInt NAME = VALUE;` in source, `extern const` in header

**Restrictions (v1):**
- Module-level only (not in functions or classes)
- Primitive types only (no `Final[list[T]]`, `Final[SomeRecord]`)
- No arithmetic in initializers (`Final[Int32] = A + 1` not supported yet)
- Must use explicit type: `Final[T]` (bare `Final` not yet supported)

### Native Global Variables (Working)

Import extern C/C++ global variables:

```python
from tpy.extern import native_c_global, native_c_global_array, native_global
from tpy import Int32, Ptr, Int16

# C global (extern "C")
frame_count: Int32 = native_c_global("DG_FrameCount")

# C global without rename (Python name = C name)
tick: Int32 = native_c_global()

# C array global (extern "C" T name[]) -- for C arrays that decay to pointers
# Use this instead of native_c_global when the C symbol is an array (T name[N]),
# not a pointer (T* name). The type annotation should be Ptr[T].
scores: Ptr[Int16] = native_c_global_array("g_scores")

# C++ global (possibly namespaced)
score: Int32 = native_global("engine::score")
lives: Int32 = native_global()
```

Generated C++ emits `extern` declarations before the module namespace. References use the C/C++ name directly. Must be at module level with a type annotation. For array globals, `native_c_global_array` generates `extern "C" T name[];` (incomplete array type) which correctly links to C arrays and decays to a pointer when used.

**Not yet supported:**
- `# tpy: include()` header directives (planned)
- `# tpy: link()` link directives (planned)
- `@extern_c` class — export TPy struct for C (planned)
- C header generation (`--emit-c-header`) (planned)

---

## Variables & Scope

- **Working**: Local variables (inferred and annotated)
- **Working**: Global variables (typed)
- **Working**: Contextual type inference from assignment/return/nested-call context for generic functions, record constructors, and module-type constructors; partial explicit type args
- **Working**: `global` keyword for explicit global mutation from functions
- **Open**: `:=` walrus → if useful pattern emerges

---

## Expressions

- **Working**: Binary/unary ops, calls, field access
- **Working**: Ternary `x if cond else y` (see [Conditionals](#conditionals))
- **Working**: List comprehensions `[expr for x in iterable if cond]` -> IIFE with loop. When output size is compile-time known (`range()` with literal args, `Array[T,N]` source, no filter), produces `std::array<T, N>` (zero heap allocation). Otherwise `std::vector<T>` with `push_back` and `reserve()` for Sized iterables. Supports tuple unpacking, annotation propagation
- **Working**: Dict comprehensions `{key: value for x in iterable if cond}` -> IIFE with loop + `insert_or_assign`. Supports tuple unpacking, annotation propagation, all iteration strategies
- **Working**: Set comprehensions `{expr for x in iterable if cond}` -> IIFE with loop + `insert`. Supports tuple unpacking, annotation propagation, all iteration strategies
- **Open**: Lambda → anonymous struct with `operator()` or inline
- **Working**: String slice `s[start:end]` -> `std::string_view` (clamping, negative indices)
- **Open**: List slice `lst[start:end]` -> new `std::vector`

---

## Error Handling

- **Working**: Runtime panics (bounds checks → abort)
- **Working**: `assert` (`assert cond`, `assert cond, "msg"`)
  - Emits runtime panic when condition is false
  - Contributes control-flow narrowing facts
  - Current limitation: assert message must be a string literal
- **Open**: `try`/`except` → error codes, `std::expected`, or limited exceptions
- **Open**: `raise` → if exception model chosen
- **Open**: Warning when exceptions are used for control flow (e.g., `try: Color(99) except ValueError` to test validity) -- prefer safe alternatives like `try_parse()`

---

## Concurrency

### Thread Safety Markers (Send/Sync)

- **Working**: `is_send()` / `is_sync()` methods on all types in the type system
- **Working**: Auto-derivation for user records based on field types
  - A record is Send if all fields are Send (safe to transfer across threads)
  - A record is Sync if all fields are Sync (safe to share references across threads)
  - Parent class Send/Sync status is also considered
- **Working**: `Send` and `Sync` marker protocols in `tpy` module (for future explicit opt-in)
- **Working**: C++ `is_send<T>` / `is_sync<T>` traits and `Send` / `Sync` concepts in runtime
- **Planned**: Enforcement at channel/shared-reference boundaries (Phase 4)

Send/Sync rules for built-in types:

| Type | Send | Sync | Notes |
|------|------|------|-------|
| Value types (Int32, bool, float, str, ...) | Yes | Yes | Copied, no aliasing |
| `Ptr[T]` | No | No | Raw pointer, no ownership guarantee |
| `ReadOnlyPtr[T]` | No | Yes (if T Sync) | Read-only shared access |
| `list[T]` | Yes (if T Send) | No | Mutable container |
| `dict[K, V]` | Yes (if K,V Send) | No | Mutable container |
| `Array[T, N]` | Yes (if T Send) | Yes (if T Sync) | Fixed-size |
| `Span[T]` / `ReadOnlySpan[T]` | No | ReadOnly: Yes (if T Sync) | Non-owning view |
| `StrView` | No | Yes | Non-owning read-only view |
| `readonly[T]` | Same as T | Yes (if T Send or Sync) | Immutable wrapper |
| `tuple[T1, T2, ...]` | Yes (if all Ti Send) | Yes (if all Ti Sync) | Composite |

- **Open**: `async`/`await` → coroutines or state machines

---

## Generators

- **Working**: Generator expressions `(expr for x in iterable if cond)` → `tpy::make_generator<T>(lambda)` wrapper satisfying `Iterable[T]`. Supports range sources, container sources, filter clauses, tuple unpacking, outer local capture. See [COMPREHENSION_DESIGN.md](COMPREHENSION_DESIGN.md#generator-expressions).
- **Open**: `yield` → generator as state-machine class implementing OptIterator (see iterator roadmap)
- Could be zero-alloc if state machine is stack-allocated

---

## Interactive REPL

- **Working**: `tpyc --repl` launches an interactive session
- Supports function and class definitions that persist across inputs
- Expressions are evaluated and printed automatically
- Multi-line input with automatic continuation detection
- Configurable backends via `--backend`:
  - `clang-repl` -- incremental JIT, fastest for iteration (~20-80ms per expression)
  - `clang` -- compile-and-run via clang++ with PCH caching
  - `gcc` -- compile-and-run via g++ with PCH caching
  - `auto` (default) -- picks clang-repl if available, falls back to clang/gcc

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

**Limitation**: Subscript (`__getitem__`, `__setitem__`) and binary operator codegen paths currently fall back to registry lookup without `resolved_function_info`. This works because these methods are single-overload today. If multi-overload subscript or binop methods are needed in the future, those codegen paths must be updated to thread `resolved_function_info` through (similar to regular method calls).

## Open Questions

1. **Allocation control ergonomics**: `@noalloc` vs `@alloc` vs module-level vs compiler flag?
2. **Container element storage**: How to spell "list of values" vs "list of pointers"?
3. **String semantics**: When does `str` allocate vs use SSO?
4. **Exceptions**: Error codes, `std::expected`, or actual exceptions?
5. **Lambda efficiency**: Always template? Configurable? Type-erased fallback?
6. **Macro system scope**: How much compile-time Python execution? Safety limits?
