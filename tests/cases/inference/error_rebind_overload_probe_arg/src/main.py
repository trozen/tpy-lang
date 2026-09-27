# A float local rebound to abs(round(x)) is refused: the inferred local picks
# no abs overload, so round keeps its int result, as CPython's does.


def rebind() -> None:
    x = 0.5
    x = abs(round(2.7))  # tpyc: error(/'x' is bound to float at line 6 and to int32 here/)
    print(x)


rebind()
