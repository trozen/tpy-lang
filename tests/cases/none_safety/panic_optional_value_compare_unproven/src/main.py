from tpy import Int32


def cmp_score(x: Int32 | None) -> Int32:
    if x > 0:
        return 1
    return 0


print(cmp_score(1))
print(cmp_score(None))
