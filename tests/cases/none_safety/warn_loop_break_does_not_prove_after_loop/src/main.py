from tpy import Int32


def after_break(x: Int32 | None, flag: bool) -> Int32:
    while flag:
        if x is None:
            break
        flag = False
    return x + 1  # tpyc: warning(/Potential None access/)


print(after_break(2, False))
