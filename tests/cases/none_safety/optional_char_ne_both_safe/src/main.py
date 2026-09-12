from tpy import char


def ne_both(a: char | None, b: char | None) -> bool:
    return a != b  # tpyc: ok


x: char = "x"
y: char = "y"
sx: char | None = x
sy: char | None = y
n: char | None = None

print(ne_both(sx, sx))
print(ne_both(sx, sy))
print(ne_both(n, sx))
print(ne_both(sx, n))
print(ne_both(n, n))
