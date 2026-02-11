from tpy import Int32


def body_renarrow(flag: bool, x: Int32 | None) -> Int32:
    while flag:
        if x is not None:
            return x + 1  # tpyc: ok
        flag = False
    return 0


print(body_renarrow(True, 8))
print(body_renarrow(True, None))
