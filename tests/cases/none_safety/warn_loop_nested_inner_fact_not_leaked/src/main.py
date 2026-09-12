from tpy import int32


def nested_warn(x: int32 | None, y: int32 | None) -> int32:
    while x is not None:
        while y is not None:
            break
        return y + 1  # tpyc: warning(/Potential None access/)
    return 0


print(nested_warn(4, 1))
