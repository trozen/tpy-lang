# TurboPython Language Features

Status legend:
- **Working** - Implemented now
- **Planned** - Will add
- **Open** - Could add with right design (notes on how)
- **Unlikely** - Probably not, but not ruled out

---

## Types

### Primitives
- **Working**: `Int32`
- **Planned**: `Int64`, `Int8`, `Int16`, `UInt*` variants
- **Planned**: `Float32`, `Float64`
- **Planned**: `Bool`
- **Working**: String literals (`"hello"` → `const char*`)
- **Open**: `str` variable type → fixed-size `char[N]` or `std::string_view`

### Containers
- **Working**: `StaticList[T, N]` (fixed-capacity)
- **Planned**: `StaticArray[T, N]` (fixed-size, pre-initialized)
- **Planned**: `Tuple[T1, T2, ...]`
- **Open**: `list` → `StaticList` when size known/bounded
- **Open**: `dict` → compile-time map or bounded hash table
- **Open**: `set` → bounded set implementation

### Pointers/References
- **Working**: `Ptr[T]` → `T*`
- **Working**: `ConstPtr[T]` → `const T*`
- **Planned**: `Ref[T]` → `T&`
- **Planned**: `ConstRef[T]` → `const T&`

### User-Defined
- **Working**: Classes → C++ structs
- **Planned**: Enums → `enum class`
- **Open**: Inheritance → could flatten or use composition patterns

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
- **Open**: `yield` → generator as state machine class

---

## Notes on "Open" Items

Many features marked "Open" could work with constraints:

1. **Dynamic containers** (`list`, `dict`, `set`): Require capacity bounds or use arena allocation
2. **Strings**: Fixed buffer, string_view, or arena-allocated
3. **Union types**: Templates with `if constexpr` or function overloads
4. **isinstance**: Compile-time type narrowing in if branches
5. **Comprehensions**: Unroll at compile time for known sizes
6. **Exceptions**: Could support with `@mayexcept` decorator, or use `std::expected`
7. **Lambda**: Inline at call site or generate functor class
8. **Generators**: Transform to state machine class

The key principle: if it can be resolved at compile time or bounded at compile time, it can probably work.
