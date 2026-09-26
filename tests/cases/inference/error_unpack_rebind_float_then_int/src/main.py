# A tuple unpack that rebinds a float local to an int element is refused like a
# plain assignment: a local has one numeric type.
from tpy import int32

def pair() -> tuple[int32, int32]:
    return (3, 4)

def unpack() -> None:
    x = 2.5
    x, y = pair()  # tpyc: error(/'x' is bound to float at line 9 and to int32 here/)
    print(x, y)

unpack()
