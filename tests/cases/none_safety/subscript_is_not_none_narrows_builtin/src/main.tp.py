from tpy import Int32


def read_at(items: list[Int32 | None], i: Int32) -> Int32:
    if items[i] is not None:
        return items[i] + 1  # tpyc: ok
    return 0


vals: list[Int32 | None] = list()
vals.append(3)
print(read_at(vals, 0))
