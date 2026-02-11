from tpy import Int32, Bool


def pick(x: Bool | None) -> Int32:
    if x:  # tpyc: warning(/Truthiness check on optional value/)  # tpyc: warning(/variable 'x'/)
        return 1
    return 0


print(pick(True))
print(pick(False))
print(pick(None))
