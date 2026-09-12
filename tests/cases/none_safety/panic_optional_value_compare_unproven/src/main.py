from tpy import int32


def cmp_score(x: int32 | None) -> int32:
    if x > 0:
        return 1
    return 0


print(cmp_score(1))
print(cmp_score(None))
