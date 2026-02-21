from tpy import Int32


def score(x: Int32 | None) -> Int32:
    if x is not None and x > 3:
        return x + 10
    if x is None or x < 0:
        return 0
    return x + 1


print(score(5))
print(score(1))
print(score(None))
