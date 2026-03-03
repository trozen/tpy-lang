# Tuple unpacking from a named variable (not a function call)
from tpy import Int32

def main() -> None:
    t = (Int32(42), "hello")
    a, b = t
    print(a)
    print(b)

    # Original variable still usable after unpack
    print(t)

main()
