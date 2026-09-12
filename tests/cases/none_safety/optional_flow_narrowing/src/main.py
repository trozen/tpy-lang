from tpy import int32


def score(x: int32 | None) -> int32:
    if x is not None and x > 3:
        return x + 10
    if x is None or x < 0:
        return 0
    return x + 1


print(score(5))
print(score(1))
print(score(None))
