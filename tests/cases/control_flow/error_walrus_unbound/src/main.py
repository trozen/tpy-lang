# Walrus variable from short-circuit RHS used after if -- must error
from tpy import int32

def test() -> None:
    x: int32 = 10
    if (x > 5) and (y := x * 2) > 15:
        print(y)
    print(y)  # tpyc: error(/may not be assigned/)

test()
