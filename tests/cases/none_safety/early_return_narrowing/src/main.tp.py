from tpy import Int32


def safe_add(x: Int32 | None) -> Int32:
    if x is None:
        return 0
    return x + 1


print(safe_add(5))
print(safe_add(None))
