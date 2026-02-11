from tpy import Bool


def invert(x: Bool | None) -> Bool:
    if not x:  # tpyc: warning(/Truthiness check on optional value/)  # tpyc: warning(/variable 'x'/)
        return True
    return False


print(invert(True))
print(invert(False))
print(invert(None))
