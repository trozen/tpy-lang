from tpy import Int32


def use_after_continue_merge(flag: bool, x: Int32 | None) -> Int32:
    while flag:
        if x is None:
            flag = False
            continue
        x = x + 1  # tpyc: ok
        flag = False
    return 0


print(use_after_continue_merge(True, 2))
print(use_after_continue_merge(True, None))
