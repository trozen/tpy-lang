# Tuple unpacking into fresh variables
from tpy import Int32

def get_pair() -> tuple[Int32, str]:
    return (Int32(42), "hello")

def get_triple() -> tuple[bool, Int32, str]:
    return (True, Int32(7), "world")

def main() -> None:
    a, b = get_pair()
    print(a)
    print(b)

    x, y, z = get_triple()
    print(x)
    print(y)
    print(z)

main()
