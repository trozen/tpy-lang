# Test that import aliases work in type annotations
from tpy import Int32 as I, Char as C

def greet(n: I, c: C) -> None:
    print(n)
    print(c)

x: I = I(42)
ch: C = "x"  # Char from string literal, not constructor
greet(x, ch)
