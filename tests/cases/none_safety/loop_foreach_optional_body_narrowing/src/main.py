from tpy import int32


def sum_non_none(items: list[int32 | None]) -> int32:
    total: int32 = 0
    for item in items:
        if item is None:
            continue
        total = total + item  # tpyc: ok
    return total


vals: list[int32 | None] = list()
vals.append(3)
vals.append(4)
print(sum_non_none(vals))
