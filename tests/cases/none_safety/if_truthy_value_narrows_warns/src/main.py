from tpy import int32


def score(x: int32 | None) -> int32:
    if x:  # tpyc: warning(/Truthiness check on optional value/)
        return x + 1  # tpyc: ok
    return 0


print(score(3))
print(score(0))
print(score(None))
