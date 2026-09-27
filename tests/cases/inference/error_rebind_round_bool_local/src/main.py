# A bool local rebound to round(x) is refused: the local's type does not pick
# round's result type, and CPython's round(2.7) is the int 3, not True.


def rebind(c: bool) -> None:
    b = c
    b = round(2.7)  # tpyc: error(/Type mismatch in reassignment to 'b': expected bool, got int32/)
    print(b)


rebind(True)
