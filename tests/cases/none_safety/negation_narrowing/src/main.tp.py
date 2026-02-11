from tpy import Int32


def f(x: Int32 | None) -> Int32:
    if not (x is None):
        return x + 1
    return 0


print(f(3))
print(f(None))
