from tpy import int32


def add_pair(a: int32 | None, b: int32 | None) -> int32:
    return a + b  # tpyc: warning(/Potential None access/)


print(add_pair(1, 2))
print(add_pair(None, 2))
