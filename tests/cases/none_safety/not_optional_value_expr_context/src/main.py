from tpy import Int32


def invert(x: Int32 | None) -> bool:
    result: bool = not x  # tpyc: ok
    return result


print(invert(2))
print(invert(0))
print(invert(None))
