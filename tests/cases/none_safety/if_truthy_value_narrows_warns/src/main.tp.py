from tpy import Int32


def score(x: Int32 | None) -> Int32:
    if x:  # tpyc: warning(/Truthiness check on optional value/)
        return x + 1  # tpyc: ok
    return 0


print(score(3))
print(score(0))
print(score(None))
