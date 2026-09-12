from tpy import int32


def use_after_write(items: list[int32 | None], i: int32) -> int32:
    if items[i] is not None:
        items[i] = items[0]
        return items[i] + 1  # tpyc: warning(/Potential None access/)
    return 0


vals: list[int32 | None] = list()
vals.append(9)
print(use_after_write(vals, 0))
