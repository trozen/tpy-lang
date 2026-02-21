from tpy import Int32


def write_none(items: list[Int32 | None]) -> Int32:
    items[0] = None
    items.append(None)
    items.insert(0, None)
    if items[1] is not None:
        return items[1] + 1  # tpyc: warning(/Potential None access/)
    return 0


vals: list[Int32 | None] = list()
vals.append(7)
print(write_none(vals))
