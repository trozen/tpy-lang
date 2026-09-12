from tpy import int32


def f(x: int32 | None) -> int32:
    if not (x is None):
        return x + 1
    return 0


print(f(3))
print(f(None))
