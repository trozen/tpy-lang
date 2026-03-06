# TurboPython for Python Programmers

TurboPython compiles Python source files to C++. Your source files are valid Python -- they work in CPython too. The main requirement: **type annotations on function signatures**.

## What You Need to Know

### Annotate your functions

Parameters and return types must be annotated. Locals are inferred.

```python
def add(a: int, b: int) -> int:
    result = a + b   # type inferred
    return result
```

### Numeric types

`int` is arbitrary-precision (custom runtime implementation), like in Python. For performance-critical code, use fixed-width types -- they use checked arithmetic and panic on overflow.

```python
from tpy import Int32, UInt8

x: int = 2 ** 100          # BigInt, works like Python
y: Int32 = 42              # 32-bit signed, panics on overflow
z: UInt8 = UInt8.trunc(y)  # wrapping conversion (no panic)
```

Mixed arithmetic between `Int32` and `int` promotes to `int`. Operations between same-width types stay in that type.

### Objects are stack-allocated and passed by reference

In Python, objects live on the heap and variables hold references. In TurboPython, objects live on the stack. Function parameters and locals are references to that stack storage, but class fields store values inline (by copy).

```python
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def nudge(p: Point) -> None:
    p.x += 1                # modifies the caller's Point (reference)

pt = Point(0, 0)
nudge(pt)
print(pt.x)                 # 1
```

### `copy()` and `Own[T]` -- the two things Python doesn't have

Since fields store values inline, assigning an object to a field creates a C++ copy. Use `copy()` to make this explicit:

```python
from tpy import copy, Own

class Line:
    start: Point
    end: Point
    def __init__(self, s: Point, e: Point) -> None:
        self.start = copy(s)   # explicit copy into field
        self.end = copy(e)
```

Returning a locally-created object requires `Own[T]` (returns by value):

```python
def make_point(x: Int32, y: Int32) -> Own[Point]:
    return Point(x, y)         # constructor result -- OK

def shifted(p: Point) -> Own[Point]:
    result = Point(p.x + 1, p.y + 1)
    return copy(result)        # local variable -- needs copy()
```

### `T | None` is enforced

Optional types require explicit null checks before use:

```python
def find() -> Point | None:
    return None

p = find()
# p.x             # warning: might be None
if p is not None:
    print(p.x)     # OK: compiler tracks narrowing
```

### Containers

```python
from tpy import Int32, Array, Span

items: list[Int32] = [1, 2, 3]    # heap-allocated, growable
items.append(4)

arr: Array[Int32, 3] = [10, 20, 30]  # stack, fixed size

def sum_all(data: Span[Int32]) -> Int32:  # read-only view, accepts list or Array
    total: Int32 = 0
    for x in data:
        total += x
    return total
```

Local list literals with no mutation compile to stack arrays automatically.

### Generics and protocols

Python 3.12+ generic syntax, protocols for structural typing:

```python
from typing import Protocol

class HasArea(Protocol):
    def area(self) -> Int32: ...

def print_area(shape: HasArea) -> None:   # any type with .area() works
    print(shape.area())

def first[T](items: list[T]) -> T:       # generic function
    return items[0]

class Box[T]:                             # generic class
    value: T
    def __init__(self, value: T) -> None:
        self.value = value
```

### Imports

```python
from tpy import Int32, Array, Span, Ptr, Own, copy   # TurboPython types
from typing import Protocol, Sized, Sequence           # type system
```

Multi-module projects use standard Python import syntax.

## Not Yet Available

The language is evolving fast. Things you'll reach for that aren't there yet:

- **String operations** -- no f-strings, `.join()`, `.split()`, `.replace()`, or concatenation with `+`
- **`dict` and `set`** -- no hash maps or sets
- **Tuple unpacking** -- no `a, b = func()` or multiple return values
- **Default arguments** -- no `def foo(x: int = 0)`
- **Ternary expressions** -- no `x = a if cond else b`, use an if/else block
- **List comprehensions** -- no `[x*2 for x in items]`, use a loop
- **Exception handling** -- no `try`/`except`, errors are panics
- **`lambda`**, **`with`**, **`async`/`await`**, **`yield`**

Class fields must be declared with types at the class level -- you can't just assign `self.x = 5` in `__init__` without a field declaration above.

See `docs/LANGUAGE_FEATURES.md` for current status and what's planned.
