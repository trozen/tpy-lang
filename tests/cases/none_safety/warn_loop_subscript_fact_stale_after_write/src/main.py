from tpy import int32


def stale_after_write(items: list[int32 | None], i: int32) -> int32:
    while items[i] is not None:
        items[i] = items[i]
        if i < 0:
            return items[i] + 1  # tpyc: warning(/Potential None access/)
        break
    return 0


vals: list[int32 | None] = list()
vals.append(8)
print(stale_after_write(vals, 0))
