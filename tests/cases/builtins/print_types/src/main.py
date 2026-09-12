from __future__ import annotations
from tpy import int32, char, Array
from tplib import ArrayList

# None literal
print(None)

# Numeric types
print(42)
print(int32(7))
print(3.14)
print(True)
print(False)

# String literal and variable
print("hello")
s = "world"
print(s)

# char
c: char = "A"
print(c)

# Containers
items: list[int] = [1, 2, 3]
print(items)

arr: Array[int32, 3] = [10, 20, 30]
print(arr)

al = ArrayList[int32, 4]()
al.append(5)
al.append(6)
print(al)

# Range (has its own operator<<, not ListPrinter)
print(range(5))
print(range(2, 7))
print(range(0, 10, 3))

def print_range() -> None:
    r = range(3)
    print(r)

print_range()

# Multiple args
print("x:", 42, True, 3.14)

# Optional values
x: int32 | None = int32(10)
print(x)
x = None
print(x)

y: bool | None = True
print(y)

z: float | None = 2.5
print(z)
