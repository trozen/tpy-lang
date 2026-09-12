from tpy import int32


def nested(flag: bool, a: int32 | None, b: int32 | None) -> int32:
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
