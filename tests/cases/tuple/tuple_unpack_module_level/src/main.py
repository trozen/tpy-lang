# Tuple unpacking at module level (global scope), including function references
from tpy import int32

def get_pair() -> tuple[int32, int32]:
    return (int32(10), int32(20))

# Unpack from function call
a, b = get_pair()
print(a)
print(b)

# Unpack from tuple literal
x, y = int32(100), int32(200)
print(x)
print(y)

# Unpack with discard
def get_triple() -> tuple[int32, int32, int32]:
    return (int32(1), int32(2), int32(3))

first, _, last = get_triple()
print(first)
print(last)

# Globals referenced from a function body
lo, hi = int32(0), int32(99)

def use_globals() -> None:
    print(lo)
    print(hi)

use_globals()

# ALL_CAPS triggers Final warning
LO, HI = int32(0), int32(99)  # tpyc: warning(/ALL_CAPS/)
print(LO)
print(HI)
