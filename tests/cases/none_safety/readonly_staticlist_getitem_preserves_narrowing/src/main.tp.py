from tpy import Int32, StaticList, readonly


@readonly
def touch(items: StaticList[Int32 | None, 2]) -> None:
    return


def use(items: StaticList[Int32 | None, 2], i: Int32) -> Int32:
    if items[i] is not None:
        touch(items)
        return items[i] + 1  # tpyc: ok
    return 0


vals: StaticList[Int32 | None, 2] = StaticList[Int32 | None, 2]()
vals.append(7)
print(use(vals, 0))
