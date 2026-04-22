# Test importing bisect from stdlib
from bisect import bisect_left, bisect_right, bisect, insort_left, insort_right, insort
from tpy import Int32

def main() -> None:
    a: list[Int32] = [1, 3, 5, 7]
    print(bisect_left(a, Int32(4)))
    print(bisect_right(a, Int32(5)))
    print(bisect(a, Int32(5)))  # alias for bisect_right
    insort_left(a, Int32(4))
    print(len(a))

    b: list[Int32] = [1, 3, 5, 7]
    insort_right(b, Int32(3))
    print(b[1], b[2])

    c: list[Int32] = [1, 3, 5, 7]
    insort(c, Int32(3))  # alias for insort_right
    print(c[1], c[2])

main()
