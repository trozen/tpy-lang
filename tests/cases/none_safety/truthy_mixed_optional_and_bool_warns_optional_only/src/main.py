from tpy import int32


def pick(x: int32 | None, flag: bool) -> int32:
    if x and flag:  # tpyc: warning(/variable 'x'/)
        return x + 1  # tpyc: ok
    return 0


print(pick(3, True))
print(pick(0, True))
print(pick(None, True))
print(pick(3, False))
