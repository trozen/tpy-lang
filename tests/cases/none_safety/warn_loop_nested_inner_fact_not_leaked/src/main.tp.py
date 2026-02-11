from tpy import Int32


def nested_warn(x: Int32 | None, y: Int32 | None) -> Int32:
    while x is not None:
        while y is not None:
            break
        return y + 1  # tpyc: warning(/Potential None access/)
    return 0


print(nested_warn(4, 1))
