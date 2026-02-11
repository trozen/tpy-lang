from tpy import Int32, Bool


def invert(x: Int32 | None) -> Bool:
    result: Bool = not x  # tpyc: ok
    return result


print(invert(2))
print(invert(0))
print(invert(None))
