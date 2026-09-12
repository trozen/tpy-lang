# Tuple unpacking into fresh variables
from tpy import int32

def get_pair() -> tuple[int32, str]:
    return (int32(42), "hello")

def get_triple() -> tuple[bool, int32, str]:
    return (True, int32(7), "world")

def main() -> None:
    a, b = get_pair()
    print(a)
    print(b)

    x, y, z = get_triple()
    print(x)
    print(y)
    print(z)

main()
