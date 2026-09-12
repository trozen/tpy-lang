from tpy import int32


def after_break(x: int32 | None, flag: bool) -> int32:
    while flag:
        if x is None:
            break
        flag = False
    return x + 1  # tpyc: warning(/Potential None access/)


print(after_break(2, False))
