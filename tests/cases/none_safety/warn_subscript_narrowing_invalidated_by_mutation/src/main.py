from tpy import Int32


def use_after_write(items: list[Int32 | None], i: Int32) -> Int32:
    if items[i] is not None:
        items[i] = items[0]
        return items[i] + 1  # tpyc: warning(/Potential None access/)
    return 0


vals: list[Int32 | None] = list()
vals.append(9)
print(use_after_write(vals, 0))
