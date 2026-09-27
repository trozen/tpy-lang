# A float local rebound to round(x) is refused: the local's inferred type does
# not pick round's result type, and CPython's round(2.7) is the int 3.


def rebind() -> None:
    x = 0.5
    x = round(2.7)  # tpyc: error(/'x' is bound to float at line 6 and to int32 here/)
    print(x)


rebind()
