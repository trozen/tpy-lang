# Test that import aliases work in type annotations
from tpy import int32 as I, char as C

def greet(n: I, c: C) -> None:
    print(n)
    print(c)

x: I = I(42)
ch: C = "x"  # char from string literal, not constructor
greet(x, ch)
