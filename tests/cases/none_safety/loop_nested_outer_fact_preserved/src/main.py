from tpy import int32


def nested_ok(x: int32 | None, y: int32 | None) -> int32:
    while x is not None:
        while y is not None:
            break
        return x + 1  # tpyc: ok
    return 0


print(nested_ok(4, 1))
print(nested_ok(None, 1))
