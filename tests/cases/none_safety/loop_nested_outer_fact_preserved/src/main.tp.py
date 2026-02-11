from tpy import Int32


def nested_ok(x: Int32 | None, y: Int32 | None) -> Int32:
    while x is not None:
        while y is not None:
            break
        return x + 1  # tpyc: ok
    return 0


print(nested_ok(4, 1))
print(nested_ok(None, 1))
