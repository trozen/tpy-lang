# Tuple equality and inequality comparison
from tpy import Int32

def main() -> None:
    a = (Int32(1), "hello")
    b = (Int32(1), "hello")
    c = (Int32(2), "world")

    print(a == b)
    print(a != b)
    print(a == c)
    print(a != c)

    # Comparison in conditional
    if a == b:
        print("equal")
    if a != c:
        print("not equal")

main()
