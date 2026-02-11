from tpy import Int32


def add_pair(a: Int32 | None, b: Int32 | None) -> Int32:
    return a + b  # tpyc: warning(/Potential None access/)


print(add_pair(1, 2))
print(add_pair(None, 2))
