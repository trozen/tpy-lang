from tpy import Int32


def nested(flag: bool, a: Int32 | None, b: Int32 | None) -> Int32:
    while flag:
        if a is not None:
            if b is None:
                break
            return a + b  # tpyc: ok
        break
    return 0


print(nested(True, 1, 2))
print(nested(True, 1, None))
print(nested(True, None, 2))
