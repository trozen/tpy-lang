from tpy import Int32


def sum_non_none(items: list[Int32 | None]) -> Int32:
    total: Int32 = 0
    for item in items:
        if item is None:
            continue
        total = total + item  # tpyc: ok
    return total


vals: list[Int32 | None] = list()
vals.append(3)
vals.append(4)
print(sum_non_none(vals))
