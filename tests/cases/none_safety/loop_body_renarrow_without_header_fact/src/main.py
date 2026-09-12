from tpy import int32


def body_renarrow(flag: bool, x: int32 | None) -> int32:
    while flag:
        if x is not None:
            return x + 1  # tpyc: ok
        flag = False
    return 0


print(body_renarrow(True, 8))
print(body_renarrow(True, None))
