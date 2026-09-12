from tpy import char


def eq_both(a: char | None, b: char | None) -> bool:
    return a == b  # tpyc: ok


x: char = "x"
y: char = "y"
sx: char | None = x
sy: char | None = y
n: char | None = None

print(eq_both(sx, sx))
print(eq_both(sx, sy))
print(eq_both(n, sx))
print(eq_both(sx, n))
print(eq_both(n, n))
