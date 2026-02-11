from tpy import Int32


def first_plus(items: list[Int32 | None], i: Int32) -> Int32:
    out: Int32 = 0
    while items[i] is not None:
        out = items[i] + 1  # tpyc: ok
        break
    return out


vals: list[Int32 | None] = list()
vals.append(4)
print(first_plus(vals, 0))
