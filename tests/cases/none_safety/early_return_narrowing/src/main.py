from tpy import int32


def safe_add(x: int32 | None) -> int32:
    if x is None:
        return 0
    return x + 1


print(safe_add(5))
print(safe_add(None))
