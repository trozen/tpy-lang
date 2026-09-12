# homogeneous *args mapped to Span[readonly[T]]
from tpy import int32

def sum_all(*args: int32) -> int32:
    total: int32 = 0
    for x in args:
        total += x
    return total

def main() -> None:
    print(sum_all(1, 2, 3))
    print(sum_all())
    print(sum_all(10))
    print(sum_all(1, 2, 3, 4, 5))

main()
