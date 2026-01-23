# TurboPython Language Features

Status legend:
- **Working** - Implemented now
- **Planned** - Will add
- **Open** - Could add with right design (notes on how)

---

## Allocation Policy

Dynamic allocation is not forbidden - it's **controllable**. Different use cases have different needs:

- **Hot paths** (trading, real-time audio): No allocation allowed
- **Startup/initialization**: Allocation is fine
- **General applications**: Allocation acceptable everywhere

### Control Mechanisms (Open - deciding on ergonomics)

Option A: **Mark restricted functions** (current)
- `@noalloc` decorator on functions that must not allocate
- Verbose if many functions need it

Option B: **Mark permissive functions** (inverted)
- Default is no-alloc, use `@alloc` to opt-in to allocation
- Better for low-latency-first codebases

Option C: **Module-level default**
- `# tpy: noalloc` at top of file sets default
- `@alloc` on specific functions that need it
- Good balance of explicitness and brevity

Option D: **Compiler flag sets default**
- `tpyc --default-noalloc` makes no-alloc the default
- `@alloc` marks exceptions
- Same codebase can compile differently for different targets

Option E: **Class-level**
- `@noalloc class HotPath:` applies to all methods
- Mix allocation policies within same module

These can be combined. The goal is flexibility with explicit control.

---

## Type Categories

TurboPython distinguishes between **value types** and **object types**:

### Value Types
Small, immutable, passed by copy:
- `int`, `Int32`, `Int64`, `Float32`, `Float64`, `Bool`
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
- **Working**: `Int32`
- **Planned**: `int` (Python's int → platform int or arbitrary precision with allocation)
- **Planned**: `Int64`, `Int8`, `Int16`, `UInt8`, `UInt16`, `UInt32`, `UInt64`
- **Planned**: `Float32`, `Float64`, `float`
- **Planned**: `Bool`

### Strings
- **Working**: String literals (`"hello"` → `const char*`)
- **Planned**: `FixStr[N]` - fixed-capacity string, stack allocated
- **Planned**: `str` - dynamic string (requires allocation, respects `@noalloc`)

### Containers
- **Working**: `StaticList[T, N]` (fixed-capacity, no allocation)
- **Planned**: `StaticArray[T, N]` (fixed-size, all elements initialized)
- **Planned**: `Tuple[T1, T2, ...]`
- **Planned**: `list` - dynamic list (requires allocation)
- **Planned**: `dict` - hash map (requires allocation)
- **Planned**: `set` - hash set (requires allocation)
- **Open**: Bounded variants: `BoundedList[T, N]`, `BoundedDict[K, V, N]`

### Pointers/References
- **Working**: `Ptr[T]` → `T*`
- **Working**: `ConstPtr[T]` → `const T*`
- **Planned**: `Ref[T]` → `T&` (explicit reference)
- **Planned**: `ConstRef[T]` → `const T&`

### User-Defined
- **Working**: Classes → C++ structs
- **Planned**: Enums → `enum class`
- **Open**: Inheritance → could support simple cases

### Union/Optional
- **Open**: `T | None` → `std::optional<T>` or pointer
- **Open**: `T | U` → templates with `if constexpr`, or overloads

---

## Operators

### Arithmetic
- **Working**: `+`, `-`, `*`, `//`, `%`, unary `-`
- **Planned**: `/` (float division)
- **Planned**: `**` (power)

### Comparison
- **Working**: `==`, `!=`, `<`, `<=`, `>`, `>=`

### Logical
- **Working**: `and`, `or`, `not`

### Bitwise
- **Planned**: `&`, `|`, `^`, `~`, `<<`, `>>`

### Assignment
- **Working**: `=`
- **Planned**: `+=`, `-=`, `*=`, `//=`, `%=`, etc.

---

## Control Flow

### Conditionals
- **Working**: `if`, `elif`, `else`
- **Open**: Ternary `x if cond else y` → C++ ternary

### Loops
- **Working**: `while`
- **Working**: `for i in range(n)`, `for i in range(start, end)`
- **Planned**: `for i in range(start, end, step)`
- **Planned**: `for item in container`
- **Planned**: `break`, `continue`
- **Open**: `for/else`, `while/else` → flag variable pattern

### Other
- **Working**: `return`
- **Planned**: `pass`
- **Open**: `match`/`case` → switch or if/else chain

---

## Functions

### Definition
- **Working**: Typed parameters and return types
- **Planned**: Default parameter values
- **Open**: `*args` → variadic templates or fixed overloads
- **Open**: `**kwargs` → if keys known at compile time

### Decorators
- **Working**: `@noalloc`
- **Open**: Custom decorators → compile-time transforms

### Type Polymorphism
- **Open**: `def foo(x: int | str)` → template or overloads
- **Open**: Generic functions `def foo[T](x: T)` → templates

---

## Classes

### Definition
- **Working**: Typed fields
- **Working**: `__init__`
- **Working**: Instance methods
- **Planned**: `@staticmethod` → free functions or static methods
- **Open**: `@classmethod` → if use case is clear
- **Open**: `@property` → getter/setter methods
- **Open**: Inheritance → composition, or actual inheritance for simple cases

### Special Methods
- **Working**: `__init__`
- **Planned**: `__del__` (destructor)
- **Open**: `__eq__`, `__lt__` → `operator==`, `operator<`
- **Open**: `__getitem__`, `__setitem__` → `operator[]`
- **Open**: `__str__` → if we have string type
- **Open**: `__enter__`, `__exit__` → RAII wrapper

---

## Built-in Functions

- **Working**: `print()`, `len()`, `range()`
- **Planned**: `abs()`, `min()`, `max()`
- **Open**: `isinstance()` → compile-time type check / type narrowing
- **Open**: `type()` → compile-time type info
- **Open**: `str()`, `int()` → type conversion functions
- **Open**: `enumerate()` → compile-time transform
- **Open**: `zip()` → compile-time transform for fixed iterables

---

## Modules & Imports

- **Working**: `from tpy import ...`
- **Planned**: Multi-file projects
- **Planned**: `import module`
- **Open**: Importing Python stdlib subsets that can be statically compiled

---

## Variables & Scope

- **Working**: Local variables (inferred and annotated)
- **Working**: Global constants
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
6. **Generics**: Python 3.12 syntax `def foo[T](x: T)` or something else?
7. **Lambda efficiency**: Always template? Configurable? Type-erased fallback?
8. **Macro system scope**: How much compile-time Python execution? Safety limits?
