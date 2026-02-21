def pick(cond: bool) -> None:
    if cond:
        x = None
    else:
        x = 5

    if x is None:
        print(0)
    else:
        print(x)


pick(True)
pick(False)
