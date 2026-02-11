from tpy import Int32


def stale_after_write(items: list[Int32 | None], i: Int32) -> Int32:
    while items[i] is not None:
        items[i] = items[i]
        if i < 0:
            return items[i] + 1  # tpyc: warning(/Potential None access/)
        break
    return 0


vals: list[Int32 | None] = list()
vals.append(8)
print(stale_after_write(vals, 0))
