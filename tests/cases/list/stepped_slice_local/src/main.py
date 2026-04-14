# Stepped slice assigned to a local: list_stepped_slice returns
# std::vector<T> by value (rvalue), must not bind to lvalue reference.
from tpy import Int32

def test_stepped() -> None:
    items: list[Int32] = [1, 2, 3, 4, 5, 6, 7, 8]
    every_other = items[::2]
    print(every_other)

    rev = items[::-1]
    print(rev)

    mid = items[1:7:2]
    print(mid)

def main() -> None:
    test_stepped()

main()
