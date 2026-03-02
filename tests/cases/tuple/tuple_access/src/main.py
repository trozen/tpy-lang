# Tuple element access with compile-time index and negative indexing
from tpy import Int32

def main() -> None:
    t = (Int32(10), "hello", True)

    # Positive indexing
    a = t[0]
    b = t[1]
    c = t[2]
    print(a)
    print(b)
    print(c)

    # Negative indexing
    print(t[-1])
    print(t[-2])
    print(t[-3])

main()
