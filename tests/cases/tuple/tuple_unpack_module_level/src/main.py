# Tuple unpacking at module level (global scope), including function references
from tpy import Int32

def get_pair() -> tuple[Int32, Int32]:
    return (Int32(10), Int32(20))

# Unpack from function call
a, b = get_pair()
print(a)
print(b)

# Unpack from tuple literal
x, y = Int32(100), Int32(200)
print(x)
print(y)

# Unpack with discard
def get_triple() -> tuple[Int32, Int32, Int32]:
    return (Int32(1), Int32(2), Int32(3))

first, _, last = get_triple()
print(first)
print(last)

# Globals referenced from a function body
lo, hi = Int32(0), Int32(99)

def use_globals() -> None:
    print(lo)
    print(hi)

use_globals()

# ALL_CAPS triggers Final warning
LO, HI = Int32(0), Int32(99)  # tpyc: warning(/ALL_CAPS/)
print(LO)
print(HI)
