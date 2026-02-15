from tpy import Char


def eq_both(a: Char | None, b: Char | None) -> bool:
    return a == b  # tpyc: ok


x: Char = "x"
y: Char = "y"
sx: Char | None = x
sy: Char | None = y
n: Char | None = None

print(eq_both(sx, sx))
print(eq_both(sx, sy))
print(eq_both(n, sx))
print(eq_both(sx, n))
print(eq_both(n, n))
