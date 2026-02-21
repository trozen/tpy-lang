from tpy import Int32


def score(x: float | None) -> Int32:
    if x:  # tpyc: warning(/Truthiness check on optional value/)  # tpyc: warning(/variable 'x'/)
        return 1
    return 0


print(score(1.5))
print(score(0.0))
print(score(None))
