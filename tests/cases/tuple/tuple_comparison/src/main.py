# Tuple equality and inequality comparison
from tpy import int32

def main() -> None:
    a = (int32(1), "hello")
    b = (int32(1), "hello")
    c = (int32(2), "world")

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
