from tpy import Char


def ne_both(a: Char | None, b: Char | None) -> bool:
    return a != b  # tpyc: ok


x: Char = "x"
y: Char = "y"
sx: Char | None = x
sy: Char | None = y
n: Char | None = None

print(ne_both(sx, sx))
print(ne_both(sx, sy))
print(ne_both(n, sx))
print(ne_both(sx, n))
print(ne_both(n, n))
