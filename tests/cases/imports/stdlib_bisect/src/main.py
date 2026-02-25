# Test importing bisect from stdlib
from bisect import bisect_left, insort_left
from tpy import Int32

def main() -> None:
    a: list[Int32] = [1, 3, 5, 7]
    print(bisect_left(a, Int32(4)))
    insort_left(a, Int32(4))
    print(len(a))

main()
