# A float-or-None local rebound to round(x) is refused: its float does not
# pick round's result type through the None, and CPython's round is an int.


def rebind(c: bool) -> None:
    y = 0.5 if c else None
    y = round(2.7)  # tpyc: error(/'y' is bound to float at line 6 and to int32 here/)
    print(y)


rebind(True)
