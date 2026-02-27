# Test comparison between int (BigInt) and float variables
def test_eq() -> None:
    a: int = 5
    b: float = 5.0
    print(a == b)
    print(a != b)

def test_ordering() -> None:
    x: int = 3
    y: float = 3.5
    print(x < y)
    print(x > y)
    print(x <= y)
    print(x >= y)

def test_float_gt_int() -> None:
    f: float = 10.0
    i: int = 7
    print(f > i)
    print(f < i)

def main() -> None:
    test_eq()
    test_ordering()
    test_float_gt_int()

main()
